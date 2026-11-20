"""Read a reviewers' list of approximate times, written however people usually write it.

Every line that holds one or two times becomes an item; anything else on the line is the label.
Examples that are all understood:
    0:08:00 عطسة
    1:00:50 - 1:01:30 رد السلام
    من 1:10:00 إلى 1:12:30 مزح
    3) 25:40 – 26:10 كلام جانبي
    ١:٠٥:٣٠ سعال
    1:30:00 - النهاية   نهاية الدرس
    1:02:10 مزح (متبقي)          <- time read off a countdown player
"""
import re

DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
TIME_RE = re.compile(r"(?<![\d:.])(\d{1,3}(?::\d{1,2}){1,2}(?:\.\d{1,3})?|\d{1,2}\.\d{2}\.\d{2})(?![\d:])")
SEP = r"\s*(?:-|–|—|ـ+|~|→|->|إلى|الى|حتى|لـ|to)\s*"
END_WORDS = r"(?:النهاية|نهاية التسجيل|نهاية الملف|نهاية المقطع|آخر التسجيل|آخره|الآخر|end)"
START_WORDS = r"(?:البداية|بداية التسجيل|أول التسجيل|start)"
REMAINING_RE = re.compile(r"(?:ال)?متبق[يٍّ]*|معكوس[ةه]?|(?:ال)?باقي|تنازلي|remaining", re.I)

# (category, pattern, action override). Order matters: the first match wins.
CATEGORIES = [
    ("فائدة", r"فائد|وعظ|موعظ|نصيح|نصائح|مفيد", "decide"),
    ("يراجع", r"يراجع|يُراجَع|ممكن يبق|يحتمل|محتمل|للمراجعة", "decide"),
    ("عطاس/سعال", r"عطس|عطاس|عطسة|سعال|سعل|كح|تنحنح|تثاؤب", None),
    ("غير مسموع", r"غير (?:مسموع|واضح|مفهوم)|لا يسمع|لا يُسمع", None),
    ("بث/أجهزة", r"بث|جهاز|أجهز|اجهز|مايك|ميكروفون|المكبر|انقطاع|تقطيع|تشويش|شبكة|الإنترنت|النت\b", None),
    ("أذان", r"أذان|اذان|الإقامة|الاقامة", None),
    ("مزح", r"مز[حا]|مزاح|ضحك|نكت|مداعب", None),
    ("سكتة", r"سك[تو]|صمت|توقف|فراغ|انتظار", None),
    ("تكرار", r"تكرار|مكرر|إعادة|اعادة|يعيد|تصحيح", None),
    ("سؤال", r"سؤال|سائل|استفسار", "decide"),
    ("بداية", r"قبل (?:بد|ال)|بداية|بدء", None),
    ("نهاية", r"نهاية|ختام|انتهاء", None),
    ("كلام جانبي", r"كلام|حديث|جانبي|مداخل|مشارك|نقاش|مناقش|خارج|سلام|اتصال|هاتف|جوال", None),
]
CATEGORY_NAMES = [c[0] for c in CATEGORIES] + ["أخرى"]


def parse_clock(tok, two_part="ms"):
    tok = tok.replace(".", ":", 2) if tok.count(".") == 2 and ":" not in tok else tok
    frac = 0.0
    if "." in tok:
        tok, f = tok.rsplit(".", 1)
        frac = float("0." + f)
    parts = [int(x) for x in tok.split(":")]
    if len(parts) == 2 and two_part == "hm":
        parts = parts + [0]
    t = 0
    for x in parts:
        t = t * 60 + x
    return t + frac


def categorize(label):
    for name, pat, action in CATEGORIES:
        if re.search(pat, label, re.I):
            return name, action
    return "غير مصنف", None


def clean_label(s):
    s = re.sub(r"^\s*[\(\[]?\d{1,3}\s*[\)\].\-ـ:]\s+", "", s)            # "3) " "12- "
    s = re.sub(r"(?:^|\s)(?:من|إلى|الى|حتى|الدقيقة|الساعة|من الدقيقة|to|from)(?=\s|$)", " ", s)
    s = re.sub(r"[()\[\]]\s*[()\[\]]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" \t-–—ـ:،,.;|•*→~")


def parse_line(line, remaining=False, two_part="ms"):
    raw = line.rstrip("\r\n")
    norm = raw.translate(DIGITS).replace("٫", ".").replace("：", ":").replace("‏", "").replace("‎", "")
    toks = list(TIME_RE.finditer(norm))
    if not toks:
        return None
    toks = toks[:2]
    start_txt = toks[0].group(1)
    end_txt = toks[1].group(1) if len(toks) > 1 else ""
    start, end = parse_clock(start_txt, two_part), (parse_clock(end_txt, two_part) if end_txt else None)
    rest = norm
    for m in reversed(toks):
        rest = rest[:m.start()] + " " + rest[m.end():]
    if not end_txt and re.search(TIME_RE.pattern + SEP + END_WORDS, norm, re.I):
        end, end_txt = "END", "النهاية"
        rest = re.sub(SEP + END_WORDS, " ", rest, count=1, flags=re.I)
    if not end_txt and re.search(START_WORDS + SEP + TIME_RE.pattern, norm, re.I):
        end, end_txt = start, start_txt
        start, start_txt = 0.0, "0:00"
        rest = re.sub(START_WORDS + SEP, " ", rest, count=1, flags=re.I)
    ref = "remaining" if remaining or REMAINING_RE.search(norm) else "elapsed"
    rest = REMAINING_RE.sub(" ", rest)
    rest = re.sub(r"(?:^|\s)(?:وقت|الوقت)\s*(?=\)|$|\s)", " ", rest)
    label = clean_label(rest)
    category, action = categorize(label)
    if action is None:
        action = "find" if end is None else "cut"
    return {"line": raw.strip(), "start_txt": start_txt, "end_txt": end_txt, "start": start, "end": end,
            "time_ref": ref, "label": label or category, "category": category, "action": action}


def parse_text(text, remaining=False, two_part="ms"):
    rows, ignored = [], []
    for i, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        r = parse_line(line, remaining, two_part)
        if r is None:
            ignored.append({"n": i, "line": line.strip()})
            continue
        r["n"] = len(rows) + 1
        r["line_no"] = i
        rows.append(r)
    return rows, ignored


def resolve(row, duration):
    """Approximate [start, end] in elapsed seconds; end == start for a single time."""
    s, e = row["start"], row["end"]
    if row["time_ref"] == "remaining":
        if e == "END":
            e = 0.0
        s = duration - s
        e = None if e is None else duration - e
    elif e == "END":
        e = duration
    point = e is None
    if point:
        e = s
    if e < s:
        s, e = e, s
    s, e = max(0.0, s), min(duration, e)
    if s > duration:
        s = e = duration
    return s, e, point


def check(rows, duration):
    """Add a 'problem' note to rows whose times do not fit the recording."""
    for r in rows:
        r["problem"] = ""
        if duration is None:
            continue
        s, e = r["start"], r["end"]
        if r["time_ref"] == "remaining":
            if isinstance(s, (int, float)) and s > duration + 1:
                r["problem"] = "الوقت المتبقي أكبر من مدة التسجيل"
        else:
            if isinstance(s, (int, float)) and s > duration + 1:
                r["problem"] = "بعد نهاية التسجيل"
            elif isinstance(e, (int, float)) and e > duration + 60:
                r["problem"] = "النهاية بعد نهاية التسجيل"
        if isinstance(s, (int, float)) and isinstance(e, (int, float)) and e < s and r["time_ref"] == "elapsed":
            r["problem"] = r["problem"] or "النهاية قبل البداية (هل الأوقات من الوقت المتبقي؟)"
        if not r["problem"]:
            a, b, point = resolve(r, duration)
            r["resolved"] = [round(a, 2), round(b, 2)]
    return rows
