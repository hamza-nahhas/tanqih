"""Time formatting and parsing."""
from .cutlist import DIGITS


def fmt(t, ms=False):
    if t is None:
        return ""
    sign = "-" if t < 0 else ""
    t = abs(t)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    if ms:
        return f"{sign}{h}:{m:02d}:{s:05.2f}"
    return f"{sign}{h}:{m:02d}:{int(s):02d}"


def parse_time(s):
    """'1:02:03.5', '62:03', '3723.5' or a number -> seconds."""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s or "").strip().translate(DIGITS).replace("٫", ".").replace(",", ".")
    if not s:
        return None
    t = 0.0
    for x in s.split(":"):
        t = t * 60 + float(x)
    return t
