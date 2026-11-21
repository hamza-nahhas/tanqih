"""Check every approved cut before export, and propose a fix where one is clear.

- an edge that falls inside a word;
- an edge that is not in a silence, so a click or a clipped syllable may be heard;
- words repeated across the join: the reader re-read what the interruption cut off, so the cut
  should start at the first reading (the most common mistake when cutting by hand);
- two cuts with only a second or two kept between them;
- edges that have no transcript, so they could not be checked."""
import difflib
import re

from .config import FRAME

_DIAC = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_PUNCT = re.compile(r"[^\w\s]", re.U)


def norm(w):
    w = _DIAC.sub("", w)
    w = _PUNCT.sub("", w)
    w = re.sub("[إأآٱا]", "ا", w).replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return w.strip().lower()


def _covered(tr, t, pad=3.0):
    return any(a <= t - pad and t + pad <= b for a, b in tr.get("windows", []))


def _loud_at(db, analysis, t, w=0.06):
    """Sound on both sides of t (not just the edge of a pause)."""
    if db is None:
        return False
    i, k = int(t / FRAME), max(1, int(w / FRAME))
    left, right = db[max(0, i - k):i], db[i:i + k]
    lim = analysis["pause_threshold_db"] + 3
    return len(left) > 0 and len(right) > 0 and float(left.mean()) > lim and float(right.mean()) > lim


def check(st, analysis, tr, db, ids=None):
    words = tr.get("words", [])
    D = analysis["duration"]
    cuts = sorted((i for i in st["items"] if i["status"] == "approved" and (ids is None or i["id"] in ids)),
                  key=lambda i: i["start"])
    out = []

    def warn(it, kind, msg, fix=None):
        out.append({"id": it["id"], "label": it.get("label", ""), "start": it["start"], "kind": kind,
                    "level": "warn" if kind in ("repeat", "in_word") else "info", "msg": msg, "fix": fix})

    for k, it in enumerate(cuts):
        s, e = it["start"], it["end"]
        edges_ok = True
        for edge, t in (("start", s), ("end", e)):
            if (edge == "start" and t < 0.5) or (edge == "end" and t > D - 0.5):
                continue
            if not _covered(tr, t):
                edges_ok = False
                warn(it, "untranscribed", f"{'البداية' if edge == 'start' else 'النهاية'} في موضع غير مفرَّغ، فلم يُفحص.")
                continue
            loud = _loud_at(db, analysis, t)
            # word timings from the transcript are loose (they often run into the next pause),
            # so only trust "inside a word" when the audio is not silent there
            inside = next((w for w in words if w["s"] + 0.04 < t < w["e"] - 0.04), None) if loud else None
            if inside:
                before, after = round(inside["s"] - 0.05, 2), round(inside["e"] + 0.05, 2)
                if edge == "start":
                    fix = {"start": before if t - inside["s"] < inside["e"] - t else after}
                else:
                    fix = {"end": after if inside["e"] - t < t - inside["s"] else before}
                warn(it, "in_word", f"{'البداية' if edge == 'start' else 'النهاية'} داخل كلمة «{inside['w']}».", fix)
            elif loud:
                warn(it, "not_silent", f"{'البداية' if edge == 'start' else 'النهاية'} ليست في سكتة؛ قد تُسمع قطعة صوت عند الوصلة.")
        if edges_ok and s > 0.5 and e < D - 0.5:
            pre = [w for w in words if w["e"] <= s + 0.05 and w["s"] >= s - 40][-15:]
            post = [w for w in words if w["s"] >= e - 0.05 and w["e"] <= e + 40][:15]
            a, b = [norm(w["w"]) for w in pre], [norm(w["w"]) for w in post]
            m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
            if m.size >= 3 and m.b <= 2 and sum(len(x) for x in a[m.a:m.a + m.size]) >= 8:
                first = pre[m.a]
                prev_end = pre[m.a - 1]["e"] if m.a > 0 else None
                new_s = round(first["s"] - min(0.15, (first["s"] - prev_end) / 2), 2) if prev_end else round(first["s"] - 0.1, 2)
                rep = " ".join(w["w"] for w in post[m.b:m.b + m.size])
                warn(it, "repeat", f"بعد الوصلة يتكرر كلام قيل قبلها: «{rep}». الأرجح أن القارئ أعاد الجملة، "
                                   "فابدأ القص من القراءة الأولى.", {"start": new_s})
        if k + 1 < len(cuts):
            gap = cuts[k + 1]["start"] - e
            if 0 < gap < 2.0:
                warn(it, "tiny_gap", f"بين هذا القص والذي يليه {gap:.1f} ث فقط. هل يُدمجان؟",
                     {"end": cuts[k + 1]["start"]})
    return out
