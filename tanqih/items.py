"""Turn list rows into review items, and make automatic first proposals without any AI.

state.json (one per recording):
  {"file": <path>, "duration": s, "items": [item, ...], "list": {"text", "remaining", "two_part"}}
item:
  id, n, label, category, action (cut | find | decide), raw {start,end,time_ref},
  approx [s,e], point, start, end, status (proposed | pending | approved | kept),
  confidence (high | medium | low | none), reason, source (list | manual | ai),
  proposed_by (auto | ai | ...), edited, note, window [a,b]
"""
import hashlib

import numpy as np

from . import cutlist
from .config import load_json, save_json, settings, work_dir
from .media import loud_bursts


def state_path(media):
    return work_dir(media) / "state.json"


def load_state(media):
    st = load_json(state_path(media))
    if st is None:
        st = {"file": str(media), "items": [], "list": {"text": "", "remaining": False, "two_part": "ms"}}
    st.setdefault("list", {"text": "", "remaining": False, "two_part": "ms"})
    st.setdefault("items", [])
    st["file"] = str(media)
    return st


def save_state(media, st):
    save_json(state_path(media), st)


# ---------- snapping ----------

def snap(t, pauses, side, reach=2.0):
    """Move a cut edge into the nearest pause within `reach` seconds.
    side='start': the cut begins in the pause just after the last kept word;
    side='end':   the cut ends in the pause just before the next kept word."""
    best, bd = t, reach
    for s, e in pauses:
        if e < t - reach:
            continue
        if s > t + reach:
            break
        cand = min(s + 0.12, (s + e) / 2) if side == "start" else max(e - 0.12, (s + e) / 2)
        if abs(cand - t) < bd:
            best, bd = cand, abs(cand - t)
    return round(best, 2)


def snap_range(s, e, pauses, duration, reach=2.0):
    ps = 0.0 if s < 1 else snap(s, pauses, "start", reach)
    pe = duration if e > duration - 1 else snap(e, pauses, "end", reach)
    if pe <= ps:
        ps, pe = s, e
    return ps, pe


# ---------- list -> items ----------

def _sig(r):
    return f"{r['start_txt']}|{r['end_txt']}|{r['time_ref']}|{r['label']}"


def merge_list(st, analysis):
    """Rebuild the list items from st['list'], keeping every decision the user already made."""
    duration, pauses = analysis["duration"], analysis["pauses"]
    st["duration"] = duration
    pad = settings()["window_pad"]
    L = st["list"]
    rows, _ = cutlist.parse_text(L.get("text", ""), L.get("remaining", False), L.get("two_part", "ms"))
    cutlist.check(rows, duration)
    old = {it["id"]: it for it in st["items"] if it.get("source") == "list"}
    keep = [it for it in st["items"] if it.get("source") != "list"]
    seen = {}
    new_items = []
    for r in rows:
        if r["problem"]:
            continue
        base = "L" + hashlib.sha1(_sig(r).encode("utf-8")).hexdigest()[:6]
        seen[base] = seen.get(base, 0) + 1
        iid = base if seen[base] == 1 else f"{base}-{seen[base]}"
        if iid in old:
            it = old.pop(iid)
            it["n"] = r["n"]
            it.pop("orphan", None)
            new_items.append(it)
            continue
        s, e, point = cutlist.resolve(r, duration)
        action = r["action"]
        if point:
            ps, pe = s, min(duration, s + 2)
        else:
            ps, pe = snap_range(s, e, pauses, duration)
        new_items.append({
            "id": iid, "n": r["n"], "label": r["label"], "category": r["category"], "action": action,
            "raw": {"start": r["start_txt"], "end": r["end_txt"], "time_ref": r["time_ref"], "line": r["line"]},
            "approx": [round(s, 2), round(e, 2)], "point": point,
            "start": ps, "end": pe,
            "status": "pending" if point or action == "decide" else "proposed",
            "confidence": "none" if point else "low",
            "reason": ("وقت واحد فقط: يجب تحديد المدى." if point else
                       "اقتراح آلي: الوقت التقريبي كما ورد، ملصقًا بأقرب سكتة. لم يُراجع بعد."),
            "source": "list", "proposed_by": "list", "note": "",
            "window": [round(max(0, s - pad), 2), round(min(duration, e + pad), 2)],
        })
    for it in old.values():  # removed from the list
        if it.get("edited") or it["status"] in ("approved", "kept"):
            it["orphan"] = True
            new_items.append(it)
    st["items"] = sorted(new_items + keep, key=lambda i: i["start"])
    return st


def needed_windows(st):
    """Ranges to transcribe: around every item (two edge windows for long cuts)."""
    cfg = settings()
    pad, long_cut = cfg["window_pad"], cfg["long_cut"]
    D = st["duration"]
    wins = []
    for it in st["items"]:
        s, e = it["approx"]
        if it.get("source") == "auto":   # found by the program itself: its own window is enough
            wins.append(list(it.get("window") or [max(0, s - 20), min(D, e + 20)]))
        elif e - s <= long_cut:
            wins.append([max(0, s - pad), min(D, e + pad)])
        else:
            wins.append([max(0, s - pad), min(D, s + pad)])
            wins.append([max(0, e - pad), min(D, e + pad)])
    return merge_ranges(wins)


def merge_ranges(wins, gap=5):
    out = []
    for w in sorted(wins):
        if out and w[0] <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], w[1])
        else:
            out.append(list(w))
    return out


def subtract(win, covered):
    parts = [list(win)]
    for c0, c1 in covered:
        nxt = []
        for a, b in parts:
            if c1 <= a or c0 >= b:
                nxt.append([a, b])
                continue
            if c0 > a:
                nxt.append([a, c0])
            if c1 < b:
                nxt.append([c1, b])
        parts = nxt
    return [p for p in parts if p[1] - p[0] > 1]


# ---------- automatic proposals (no AI) ----------

def _pause_before(t, pauses):
    best = None
    for s, e in pauses:
        if s > t:
            break
        best = (s, e)
    return best


def _pause_after(t, pauses):
    for s, e in pauses:
        if e >= t and s >= t - 0.05:
            return (s, e)
    return None


def _word_gap(words, t0, t1, longest=25.0):
    """The stretch with no transcribed speech that contains [t0,t1]: (last word end, next word start)."""
    prev_e, nxt_s = None, None
    for w in words:
        if w["e"] <= t0 + 0.05:
            prev_e = w["e"]
        elif w["s"] >= t1 - 0.05:
            nxt_s = w["s"]
            break
        else:
            return None   # a word overlaps the burst
    if prev_e is None or nxt_s is None or nxt_s - prev_e > longest:
        return None
    return prev_e, nxt_s


def auto_propose(st, analysis, db, words=None):
    """Refine items the user has not touched, without any AI:
    sneezes/coughs -> the loud burst nearest the given time, widened to the silent stretch around it
    when the transcript shows one; silences -> the longest pause."""
    D, pauses, sp = analysis["duration"], analysis["pauses"], analysis["speech_db"]
    for it in st["items"]:
        if it.get("edited") or it.get("proposed_by") not in ("list", "auto") or it["status"] in ("approved", "kept"):
            continue
        a0, a1 = it["approx"]
        cat = it.get("category", "")
        if cat == "عطاس/سعال" and (it["point"] or a1 - a0 < 20):
            reach = 25 if it["point"] else 8
            bursts = loud_bursts(db, sp, max(0, a0 - reach), min(D, a1 + reach))
            if not bursts:
                bursts = loud_bursts(db, sp, max(0, a0 - reach), min(D, a1 + reach), above=6)
            if not bursts:
                continue
            mid = (a0 + a1) / 2
            b = min(bursts, key=lambda x: abs((x[0] + x[1]) / 2 - mid) - x[2] * 0.3)
            pb, pa = _pause_before(b[0] + 0.05, pauses), _pause_after(b[1] - 0.05, pauses)
            s = min(pb[0] + 0.12, (pb[0] + pb[1]) / 2) if pb and b[0] - pb[1] < 1.5 else b[0] - 0.25
            e = max(pa[1] - 0.12, (pa[0] + pa[1]) / 2) if pa and pa[0] - b[1] < 1.5 else b[1] + 0.25
            if e - s > 6:
                s, e = b[0] - 0.25, b[1] + 0.25
            reason = f"اقتراح آلي: صوت مرتفع مفاجئ (+{b[2]:.0f} dB) قرب الوقت المذكور، والقص بين السكتتين حوله."
            gap = _word_gap(words, b[0], b[1]) if words else None
            if gap:
                g0, g1 = gap
                s, e = g0 + min(0.15, (g1 - g0) / 6), g1 - min(0.15, (g1 - g0) / 6)
                reason = (f"اقتراح آلي: صوت مرتفع مفاجئ (+{b[2]:.0f} dB) قرب الوقت المذكور، داخل فترة بلا كلام "
                          f"مفرَّغ مدتها {g1 - g0:.1f} ث، فالقص يشملها كلها من آخر كلمة قبلها إلى أول كلمة بعدها.")
            it.update(start=round(max(0, s), 2), end=round(min(D, e), 2), status="proposed",
                      confidence="medium", proposed_by="auto", reason=reason + " استمع للتأكد.")
        elif cat == "سكتة" and it["point"]:
            cands = [(s, e) for s, e in pauses if e > a0 - 60 and s < a1 + 60 and e - s >= 1.5]
            if not cands:
                continue
            s, e = max(cands, key=lambda p: p[1] - p[0])
            it.update(start=round(s + 0.35, 2), end=round(e - 0.35, 2),
                      status="proposed", confidence="medium", proposed_by="auto",
                      reason="اقتراح آلي: أطول سكتة قرب الوقت المذكور، تُختصر إلى نحو ثلثي ثانية.")
    return st


# ---------- suggestions the reviewers did not write ----------

def _overlaps(a, b, items, pad=2.0):
    return any(i["start"] - pad < b and i["end"] + pad > a for i in items)


EVENT_NAMES = {"ضحك": "ضحك", "عطاس": "عطاس", "سعال": "سعال", "تصفيق": "تصفيق"}


def _event_items(events, others, analysis, db, words, D):
    """Items from the sound-event model. Sounds inside an existing item are noted on that item."""
    pauses = analysis["pauses"]
    new = []
    for it in others:
        it.pop("events", None)
    for ev in events:
        a0, a1, kind, p = ev["start"], ev["end"], ev["kind"], ev["score"]
        host = next((i for i in others if i["start"] - 3 < a1 and i["end"] + 3 > a0
                     or (i.get("point") and abs(i["approx"][0] - (a0 + a1) / 2) < 30)), None)
        if host is not None:
            host.setdefault("events", []).append({"kind": kind, "start": a0, "end": a1, "score": p})
            continue
        if kind in ("عطاس", "سعال"):
            # the loudest moment inside the event is the sneeze / cough itself
            i0, i1 = int(a0 / 0.02), max(int(a0 / 0.02) + 1, int(a1 / 0.02))
            seg = db[i0:i1] if db is not None else None
            peak = a0 + (int(np.argmax(seg)) * 0.02 if seg is not None and len(seg) else (a1 - a0) / 2)
            if words is not None:
                gap = _word_gap(words, peak - 0.15, peak + 0.15)
                if gap is None:
                    continue          # in the middle of speech: cannot be cut without cutting words
                s, e = gap[0] + min(0.15, (gap[1] - gap[0]) / 6), gap[1] - min(0.15, (gap[1] - gap[0]) / 6)
            else:
                pb, pa = _pause_before(peak, pauses), _pause_after(peak, pauses)
                s = min(pb[0] + 0.12, (pb[0] + pb[1]) / 2) if pb and peak - pb[1] < 1.5 else peak - 0.6
                e = max(pa[1] - 0.12, (pa[0] + pa[1]) / 2) if pa and pa[0] - peak < 1.5 else peak + 0.6
            if e - s > 15:
                continue
            new.append({"id": f"E{int(peak * 100)}", "label": EVENT_NAMES[kind], "category": "عطاس/سعال",
                        "start": round(max(0, s), 2), "end": round(min(D, e), 2), "status": "proposed",
                        "confidence": "high" if p >= 0.5 else "medium",
                        "reason": f"اقتراح آلي: نموذج تمييز الأصوات سمع {EVENT_NAMES[kind]} هنا (ثقة {p:.0%})، "
                                  "والقص يشمل الفترة بلا كلام حوله.",
                        "window": [max(0, s - 20), min(D, e + 20)]})
        else:
            s, e = snap_range(a0, a1, pauses, D, reach=1.5)
            new.append({"id": f"E{int(a0 * 100)}", "label": EVENT_NAMES[kind],
                        "category": "مزح" if kind == "ضحك" else "أخرى",
                        "start": s, "end": e, "status": "pending", "confidence": "low",
                        "reason": (f"اقتراح آلي: ضحك مسموع (ثقة {p:.0%}). الأرجح أن قبله مزاحًا: استمع إلى ما قبله "
                                   "ووسّع البداية إلى أول المزاح، أو استعن بالمساعد الذكي." if kind == "ضحك" else
                                   f"اقتراح آلي: {EVENT_NAMES[kind]} (ثقة {p:.0%})."),
                        "window": [max(0, a0 - 60), min(D, a1 + 30)]})
    return new


def suggest(st, analysis, db, words=None, events=None):
    """Add items for long silences and sudden sounds anywhere in the recording.
    With `events` (from the sound-event model) laughter, sneezes and coughs are named and placed;
    without it, any sudden loud sound is suggested. Untouched suggestions are rebuilt each time;
    decided ones are kept."""
    cfg = settings()
    D, pauses, sp = analysis["duration"], analysis["pauses"], analysis["speech_db"]
    keep = [i for i in st["items"] if i.get("source") != "auto" or i.get("edited")
            or i["status"] in ("approved", "kept")]
    others = [i for i in keep]
    new = []
    min_sil = float(cfg.get("suggest_silence") or 0)
    if min_sil > 0:
        for s, e in pauses:
            if e - s < min_sil or s < 1 or e > D - 1 or _overlaps(s, e, others, 0):
                continue
            a, b = round(s + 0.35, 2), round(e - 0.35, 2)
            spoken = words is not None and any(s < (w["s"] + w["e"]) / 2 < e for w in words)
            new.append({"id": f"S{int(s * 100)}", "label": f"سكتة {e - s:.0f} ث", "category": "سكتة",
                        "start": a, "end": b, "status": "proposed",
                        "confidence": "low" if spoken else "high",
                        "reason": ("اقتراح آلي: سكتة طويلة لم يذكرها المراجعون، تُختصر إلى نحو ثلثي ثانية."
                                   + (" لكن في التفريغ كلامًا داخلها، فاستمع قبل القبول." if spoken else ""))})
    if events is not None:
        new += _event_items(events, others + new, analysis, db, words, D)
    # sudden loud sounds the model did not name stay as "loud sound" (e.g. a sneeze over speech)
    if cfg.get("suggest_loud", True) and db is not None:
        for b0, b1, peak in loud_bursts(db, sp, 0, D, above=18, join=0.6):
            if b1 - b0 > 3 or _overlaps(b0, b1, others + new, 1.0):
                continue
            pb, pa = _pause_before(b0 + 0.05, pauses), _pause_after(b1 - 0.05, pauses)
            a = min(pb[0] + 0.12, (pb[0] + pb[1]) / 2) if pb and b0 - pb[1] < 1.5 else b0 - 0.25
            b = max(pa[1] - 0.12, (pa[0] + pa[1]) / 2) if pa and pa[0] - b1 < 1.5 else b1 + 0.25
            gap = _word_gap(words, b0, b1) if words else None
            if gap:
                a, b = gap[0] + min(0.15, (gap[1] - gap[0]) / 6), gap[1] - min(0.15, (gap[1] - gap[0]) / 6)
            if b - a > 8:
                a, b = b0 - 0.25, b1 + 0.25
            new.append({"id": f"B{int(b0 * 100)}", "label": f"صوت مرتفع (+{peak:.0f} dB)", "category": "صوت مرتفع",
                        "start": round(max(0, a), 2), "end": round(min(D, b), 2), "status": "pending",
                        "confidence": "low",
                        "reason": "اقتراح آلي: صوت مفاجئ أعلى بكثير من مستوى الكلام (عطاس أو سعال أو طرق أو ضحك). "
                                  "قد يكون صوت المتكلم نفسه، فاستمع ثم قرّر."})
    have = {i["id"] for i in keep}
    for n in new:
        if n["id"] in have:
            continue
        n.setdefault("window", [max(0, n["start"] - 20), min(D, n["end"] + 20)])
        n.update(n="", action="cut", raw={}, approx=[n["start"], n["end"]], point=False, source="auto",
                 proposed_by="auto", note="")
        keep.append(n)
    st["items"] = sorted(keep, key=lambda i: i["start"])
    return st
