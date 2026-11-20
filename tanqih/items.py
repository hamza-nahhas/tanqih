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
        if e - s <= long_cut:
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
