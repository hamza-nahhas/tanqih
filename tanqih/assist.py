"""Work with any AI chat (ChatGPT, Claude, Gemini, ...) without an API key.

export_batches() writes self-contained text: the instructions, then for each item the reviewer's
note and the timed transcript around it, with pauses and loud sounds marked. The user pastes it into
any chat, pastes the JSON reply back, and import_reply() applies it.
The same JSON format is accepted from the command line (`python -m tanqih apply`) for agents."""
import json
import re

import numpy as np

from .config import FRAME, settings
from .items import snap
from .media import loud_bursts
from .util import fmt, parse_time

INSTRUCTIONS = """\
أنت تساعد في تنقيح تسجيل صوتي لدرس (حذف ما ليس من الدرس). الملف: «{name}»، ومدته {duration}.
استمع المراجِعون إلى التسجيل وكتبوا أوقاتًا تقريبية لمواضع يُراد حذفها: مزاح، كلام جانبي، عطاس وسعال، سكتات طويلة، مشكلات البث، وغيرها. أوقاتهم قد تبتعد عن الموضع الحقيقي حتى دقيقتين، وبعضهم يكتب وقتًا واحدًا فقط لموضع المقاطعة.

مهمتك: لكل بند أدناه حدّد بدقة أين يبدأ الحذف وأين ينتهي، معتمدًا على التفريغ الآلي المؤقَّت المرفق.

كيف تقرأ التفريغ:
- كل سطر يبدأ بوقت بدايته ونهايته [س:د:ث.ث–س:د:ث.ث]. الأسطر تنقطع عند كل سكتة، وعند كل ست ثوانٍ تقريبًا في الكلام المتصل. أفضل مواضع القطع سكتة بين سطرين (نهاية السطر أبكر من بداية الذي يليه).
- «… فجوة» تعني مدة لم يُفرَّغ فيها كلام. إن كان «النشاط الصوتي» فيها مرتفعًا فقد يكون فيها كلام أسقطه التفريغ (يحدث هذا كثيرًا مع الكلام الجانبي البعيد عن المِكروفون).
- «أصوات مرتفعة» أصوات مفاجئة أعلى من مستوى الكلام: عطاس، سعال، ضحك، طرق.
- «أصوات ميّزها نموذج الأصوات» تسمّي الصوت: ضحك أو عطاس أو سعال، بمدى تقريبي (قطع من ثلاث ثوانٍ). الضحك يأتي غالبًا بعد المزاح، فابحث قبله عن بداية المزاح في التفريغ.
- التفريغ آلي وقد يخطئ في بعض الكلمات؛ الكلمة المتبوعة بـ؟ ضعيفة الثقة.

قواعد الحذف:
1. ابدأ الحذف بعد نهاية آخر كلمة تبقى، وأنهِه قبل بداية أول كلمة تبقى؛ أي عند حدود الأسطر لا في وسطها. فالبداية = نهاية آخر سطر يبقى، والنهاية = بداية أول سطر يبقى بعده.
2. الوصلة يجب أن تكون طبيعية: ما قبل الحذف وما بعده يتصلان في المعنى، فلا يبقى نصف جملة معلّق.
3. بعد المقاطعة يعيد القارئ أو الشيخ كثيرًا قراءة الجملة التي قُطعت. حينئذ وسّع الحذف ليشمل القراءة الأولى المقطوعة إلى ما قبل الإعادة، حتى لا يتكرر الكلام بعد الحذف.
4. لا تحذف شيئًا من صلب الدرس (القراءة والشرح) إلا ما يصفه البند. وإذا كان الموضع فيه فائدة علمية مختلطة بالمزاح فاذكر ذلك في السبب.
5. البند الموسوم «يحتاج قرارًا» (فائدة، وعظ، سؤال...): حدّد حدوده فقط، والقرار للمستخدم.
6. البند ذو الوقت الواحد: ابحث حوله عن الشيء الموصوف (العطسة، الكلام الجانبي...) وحدّد مداه كاملًا.
7. إن لم تجد الموضع داخل التفريغ المعروض فأعد البند بـ "status": "not_found" واشرح ما رأيت، ولا تخمّن.
8. يجوز أن تقترح بنودًا إضافية واضحة لم ترد في القائمة رأيتها داخل التفريغ المعروض، بمعرّف يبدأ بـ new- مع "label".

صيغة الرد: كتلة JSON واحدة فقط بين ```json و```، وهي مصفوفة فيها عنصر لكل بند:
```json
[
  {{"id": "L3a9f21", "start": "1:02:03.4", "end": "1:02:30.1", "confidence": "high", "reason": "آخر ما يبقى قبل الحذف: «...»، وأول ما يبقى بعده: «...»"}},
  {{"id": "L77d0e2", "status": "not_found", "reason": "..."}},
  {{"id": "new-1", "label": "كلام جانبي", "start": "1:10:05.2", "end": "1:10:19.8", "confidence": "medium", "reason": "..."}}
]
```
- start و end بصيغة س:د:ث.ث كما في التفريغ.
- confidence: ‏high إذا كان الموضع والحدّان واضحين، ‏medium إذا كان أحد الحدّين محتملًا، ‏low إذا كان الأمر غير مؤكد.
- reason: جملة أو جملتان بالعربية، واذكر فيها آخر كلمات تبقى قبل الحذف وأول كلمات تبقى بعده.
"""


def _level(db, analysis, t0, t1):
    if db is None:
        return -99.0, 0.0
    i0 = int(t0 / FRAME)
    x = db[i0:max(i0 + 1, int(t1 / FRAME))]
    if not len(x):
        return -99.0, 0.0
    return float(np.max(x) - analysis["speech_db"]), float(np.mean(x > analysis["pause_threshold_db"]))


EVENT_WORDS = {"ضحك": "ضحك", "عطاس": "عطاس", "سعال": "سعال", "تصفيق": "تصفيق"}


def format_window(words, analysis, db, a, b, events=None):
    ws = [w for w in words if a <= (w["s"] + w["e"]) / 2 <= b]
    out, line, l0, prev = [], [], None, None

    def flush():
        if line:
            out.append(f"[{fmt(l0, True)[:-1]}–{fmt(prev, True)[:-1]}] " + " ".join(line))

    for w in ws:
        if prev is not None and (w["s"] - prev >= 0.3 or w["s"] - l0 >= 6):
            flush()
            line = []
            g = w["s"] - prev
            if g >= 1.5:
                pk, act = _level(db, analysis, prev, w["s"])
                out.append(f"  … فجوة {g:.1f} ث بلا كلام مفرَّغ (النشاط الصوتي {act:.0%}، الذروة {pk:+.0f} dB)")
        if not line:
            l0 = w["s"]
        line.append(w["w"] + ("؟" if w["p"] < 0.4 else ""))
        prev = w["e"]
    flush()
    bursts = loud_bursts(db, analysis["speech_db"], a, b, above=14)
    if bursts:
        out.append("أصوات مرتفعة: " + "، ".join(f"{fmt(s, True)[:-1]} (+{p:.0f} dB)" for s, e, p in bursts[:20]))
    evs = [e for e in (events or []) if e["start"] < b and e["end"] > a]
    if evs:
        out.append("أصوات ميّزها نموذج الأصوات: " + "، ".join(
            f"{EVENT_WORDS.get(e['kind'], e['kind'])} {fmt(e['start'])}–{fmt(e['end'])} (ثقة {e['score']:.0%})" for e in evs))
    if not ws:
        out.append("(لا كلام مفرَّغ في هذا المدى)")
    return out


def covered(transcript, a, b):
    got = sum(max(0.0, min(b, y) - max(a, x)) for x, y in transcript.get("windows", []))
    return got >= (b - a) - 3


def item_block(it, analysis, transcript, db, events=None):
    pad = settings()["window_pad"]
    long_cut = settings()["long_cut"]
    D = analysis["duration"]
    s, e = it["approx"]
    raw = it.get("raw") or {}
    lines = [f"=== البند {it['id']} ===",
             f"الوصف: {it.get('label') or '-'} — التصنيف: {it.get('category') or '-'}"
             + (" — يحتاج قرارًا من المستخدم" if it.get("action") == "decide" else "")]
    if raw.get("start"):
        t = raw["start"] + (f" – {raw['end']}" if raw.get("end") else " (وقت واحد فقط)")
        if raw.get("time_ref") == "remaining":
            t += f"؛ كُتب من الوقت المتبقي، ويقابل {fmt(s)}" + (f" – {fmt(e)}" if not it.get("point") else "")
        lines.append("في قائمة المراجِع: " + t)
    if it.get("note_list") or raw.get("line"):
        lines.append("سطر القائمة كما كُتب: " + (raw.get("line") or it.get("note_list")))
    if not it.get("point"):
        lines.append(f"الاقتراح الحالي: {fmt(it['start'], True)} – {fmt(it['end'], True)}")
    if e - s <= long_cut:
        ranges = [[max(0, s - pad), min(D, e + pad)]]
    else:
        ranges = [[max(0, s - pad), min(D, s + 90)], [max(0, e - 90), min(D, e + pad)]]
    missing = [r for r in ranges if not covered(transcript, *r)]
    for k, (a, b) in enumerate(ranges):
        if k:
            lines.append(f"  … (وسط البند لا يُعرض: من {fmt(ranges[0][1])} إلى {fmt(a)}) …")
        lines.append(f"التفريغ من {fmt(a)} إلى {fmt(b)}:")
        lines += format_window(transcript["words"], analysis, db, a, b, events)
    return "\n".join(lines), bool(missing)


def export_batches(name, st, analysis, transcript, db, ids=None, events=None):
    items = [it for it in st["items"] if (ids is None or it["id"] in ids)]
    head = INSTRUCTIONS.format(name=name, duration=fmt(analysis["duration"]))
    limit = int(settings().get("assist_batch_chars") or 30000)
    blocks, skipped = [], []
    for it in items:
        txt, missing = item_block(it, analysis, transcript, db, events)
        if missing:
            skipped.append(it["id"])
            continue
        blocks.append((it["id"], txt))
    batches, cur, cur_ids, size = [], [], [], len(head)
    for iid, txt in blocks:
        if cur and size + len(txt) > limit:
            batches.append((cur_ids, cur))
            cur, cur_ids, size = [], [], len(head)
        cur.append(txt)
        cur_ids.append(iid)
        size += len(txt) + 2
    if cur:
        batches.append((cur_ids, cur))
    out = []
    for k, (bids, txts) in enumerate(batches, 1):
        part = f"\n(هذه الدفعة {k} من {len(batches)}.)\n" if len(batches) > 1 else ""
        out.append({"ids": bids, "text": head + part + "\nالبنود:\n\n" + "\n\n".join(txts) + "\n"})
    return out, skipped


# ---------- reading the reply ----------

def extract_json(text):
    text = (text or "").replace("“", '"').replace("”", '"').replace("„", '"')
    blocks = re.findall(r"```(?:json|JSON)?\s*(.*?)```", text, re.S) or [text]
    found = []
    for blk in blocks:
        i, j = blk.find("["), blk.rfind("]")
        if i < 0 or j <= i:
            continue
        s = re.sub(r",\s*([\]}])", r"\1", blk[i:j + 1])
        try:
            v = json.loads(s)
        except ValueError:
            continue
        if isinstance(v, dict):
            v = v.get("items") or []
        found += [x for x in v if isinstance(x, dict)]
    if not found:
        raise ValueError("لم أجد في الرد مصفوفة JSON صالحة. انسخ الرد كاملًا، أو اطلب من المساعد إعادة كتابته بالصيغة المطلوبة.")
    return found


def apply_proposals(st, analysis, props, force=False, by="ai"):
    D, pauses = analysis["duration"], analysis["pauses"]
    by_id = {it["id"]: it for it in st["items"]}
    report = {"applied": [], "not_found": [], "skipped_edited": [], "errors": []}
    pad = 30
    for k, p in enumerate(props):
        iid = str(p.get("id", "")).strip()
        it = by_id.get(iid)
        try:
            st_t = parse_time(p["start"]) if p.get("start") not in (None, "") else None
            en_t = parse_time(p["end"]) if p.get("end") not in (None, "") else None
        except (TypeError, ValueError):
            report["errors"].append(f"{iid}: وقت غير مفهوم")
            continue
        status = str(p.get("status", "")).lower()
        if it is None:
            if not (iid.startswith("new") or p.get("new")):
                report["errors"].append(f"{iid}: معرّف غير موجود")
                continue
            if st_t is None or en_t is None or en_t <= st_t:
                report["errors"].append(f"{iid}: بند جديد بلا مدى صحيح")
                continue
            iid = f"A{len(st['items'])}-{k}"
            it = {"id": iid, "n": "", "label": p.get("label") or "مقترح إضافي",
                  "category": p.get("category") or p.get("label") or "مقترح من المساعد", "action": "cut",
                  "raw": {}, "approx": [st_t, en_t], "point": False, "start": st_t, "end": en_t,
                  "status": "proposed", "confidence": "low", "reason": "", "source": "ai", "note": "",
                  "window": [max(0, st_t - 60), min(D, en_t + 60)]}
            st["items"].append(it)
            by_id[iid] = it
        elif it.get("edited") and not force:
            report["skipped_edited"].append(iid)
            continue
        if status == "not_found":
            it["reason"] = "لم يجده المساعد: " + str(p.get("reason", ""))
            it["confidence"] = "none"
            it["proposed_by"] = by
            report["not_found"].append(iid)
            continue
        if st_t is None or en_t is None:
            report["errors"].append(f"{iid}: لا بداية أو نهاية")
            continue
        if en_t < st_t:
            st_t, en_t = en_t, st_t
        st_t, en_t = max(0.0, st_t), min(D, en_t)
        if en_t - st_t < 0.05:
            report["errors"].append(f"{iid}: مدى فارغ")
            continue
        it["start"] = 0.0 if st_t < 0.3 else snap(st_t, pauses, "start", 0.6)
        it["end"] = D if en_t > D - 0.3 else snap(en_t, pauses, "end", 0.6)
        if it["end"] <= it["start"]:
            it["start"], it["end"] = round(st_t, 2), round(en_t, 2)
        conf = str(p.get("confidence", "medium")).lower()
        it["confidence"] = conf if conf in ("high", "medium", "low") else "medium"
        it["reason"] = str(p.get("reason", ""))
        if p.get("label") and it.get("source") == "ai":
            it["label"] = p["label"]
        if it["status"] not in ("approved", "kept") or force:
            it["status"] = "pending" if it.get("action") == "decide" else "proposed"
        it["proposed_by"] = by
        it["window"] = [round(max(0, min(it["window"][0], it["start"] - pad)), 2),
                        round(min(D, max(it["window"][1], it["end"] + pad)), 2)]
        report["applied"].append(iid)
    st["items"].sort(key=lambda i: i["start"])
    return report
