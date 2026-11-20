"""Write the trimmed recording from the approved cuts.

The original is decoded once, the approved ranges are dropped sample-accurately, every join gets a
short fade-out / fade-in, and the result is encoded a single time. A cut log with a time map
(original -> new) is written next to it."""
import csv
from pathlib import Path

import numpy as np

from .config import output_dir, settings
from .media import ffmpeg_exe, popen, probe
from .util import fmt

FORMATS = {  # name -> (extension, encoder args; {br} = bitrate)
    "m4a": (".m4a", ["-c:a", "aac", "-b:a", "{br}", "-movflags", "+faststart"]),
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "{br}"]),
    "opus": (".ogg", ["-c:a", "libopus", "-b:a", "{br}"]),
    "flac": (".flac", ["-c:a", "flac"]),
    "wav": (".wav", ["-c:a", "pcm_s16le"]),
}
SAME = {".mp3": "mp3", ".m4a": "m4a", ".aac": "m4a", ".mp4": "m4a", ".m4v": "m4a", ".mov": "m4a",
        ".ogg": "opus", ".oga": "opus", ".opus": "opus", ".webm": "opus", ".mkv": "opus",
        ".flac": "flac", ".wav": "wav"}


def merged_cuts(items, duration):
    cuts = sorted([max(0.0, i["start"]), min(duration, i["end"])]
                  for i in items if i["status"] == "approved" and i["end"] > i["start"])
    out = []
    for a, b in cuts:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def render(media, st, progress):
    cfg = settings()
    src = Path(media)
    duration = st["duration"]
    sr, ch, _, src_br = probe(src, with_bitrate=True)
    ch = min(ch, 2)
    cuts = merged_cuts(st["items"], duration)
    if not cuts:
        raise SystemExit("لا توجد قصّات مقبولة")

    fmt_name = cfg["output_format"]
    if fmt_name not in FORMATS:
        fmt_name = SAME.get(src.suffix.lower(), "m4a")
    ext, enc_args = FORMATS[fmt_name]
    if fmt_name == "opus":
        sr_out = 48000 if sr not in (8000, 12000, 16000, 24000, 48000) else sr
    else:
        sr_out = sr
    br = cfg["output_bitrate"]
    if cfg["output_format"] == "same" and src_br:
        # never inflate: keep about the bitrate of the original when it is lower
        want = int(str(br).rstrip("kK")) if str(br).rstrip("kK").isdigit() else 160
        br = f"{min(want, max(32, -(-src_br // 16000) * 16))}k"
    enc_args = [a.replace("{br}", br) for a in enc_args]

    total = int(round(duration * sr)) + sr
    keep, prev = [], 0
    for a, b in cuts:
        sa, sb = int(round(a * sr)), int(round(b * sr))
        if sa > prev:
            keep.append([prev, sa])
        prev = sb
    keep.append([prev, None])

    out_dir = output_dir(src)
    out_path = out_dir / f"{src.stem} - منقح{ext}"
    if out_path.resolve() == src.resolve():
        raise SystemExit("ملف الإخراج سيكتب فوق الأصل؛ غيّر مجلد الإخراج.")
    tmp_path = out_path.with_name(out_path.stem + ".part" + ext)
    fade = max(1, int(sr * cfg["fade_ms"] / 1000))
    ff = ffmpeg_exe()
    dec = popen([ff, "-v", "error", "-i", str(src), "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
                 "-ar", str(sr), "-ac", str(ch), "-"], stdout=-1)
    enc = popen([ff, "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", str(ch), "-i", "-",
                 "-i", str(src), "-map", "0:a", "-map_metadata", "1", "-ar", str(sr_out), *enc_args,
                 "-f", {".m4a": "ipod", ".ogg": "ogg"}.get(ext, ext[1:]), str(tmp_path)], stdin=-1)
    chunk, pos, last_pct = sr * 2, 0, -1
    try:
        while True:
            raw = dec.stdout.read(chunk * ch * 2)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).reshape(-1, ch)
            n = len(x)
            pieces = []
            for a, b in keep:
                bb = b if b is not None else pos + n
                lo, hi = max(a, pos), min(bb, pos + n)
                if lo >= hi:
                    continue
                seg = x[lo - pos:hi - pos].astype(np.float32)
                idx = np.arange(lo, hi)
                g = np.ones(hi - lo, dtype=np.float32)
                if a > 0:
                    g = np.minimum(g, np.clip((idx - a) / fade, 0, 1))
                if b is not None:
                    g = np.minimum(g, np.clip((b - idx) / fade, 0, 1))
                pieces.append((seg * g[:, None]).astype(np.int16))
            if pieces:
                enc.stdin.write(np.concatenate(pieces).tobytes())
            pos += n
            pct = int(95 * pos / total)
            if pct != last_pct:
                progress(pct, f"إخراج الصوت {fmt(pos / sr)} من {fmt(duration)}")
                last_pct = pct
    finally:
        enc.stdin.close()
        dec.stdout.close()
    if enc.wait() != 0 or dec.wait() != 0:
        tmp_path.unlink(missing_ok=True)
        raise SystemExit("فشل ffmpeg أثناء الإخراج")
    tmp_path.replace(out_path)

    removed = sum(b - a for a, b in cuts)
    log_path = out_dir / f"{src.stem} - سجل القص.csv"
    approved = sorted((i for i in st["items"] if i["status"] == "approved"), key=lambda i: i["start"])
    with open(log_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["البند", "الوصف", "التصنيف", "بداية (الأصل)", "نهاية (الأصل)", "المدة",
                    "موضع الوصل في النسخة الجديدة", "ملاحظة"])
        for it in approved:
            new_t = it["start"] - sum(min(b, it["start"]) - a for a, b in cuts if a < it["start"])
            w.writerow([it.get("n", ""), it["label"], it["category"], fmt(it["start"], True),
                        fmt(it["end"], True), f"{it['end'] - it['start']:.1f}", fmt(new_t, True),
                        it.get("note", "")])
        w.writerow([])
        w.writerow(["المدة الأصلية", fmt(duration), "المحذوف", fmt(removed), "المدة الجديدة",
                    fmt(duration - removed)])
        w.writerow([])
        w.writerow(["خريطة الأوقات: من (الأصل)", "إلى (الأصل)", "يبدأ في النسخة الجديدة عند"])
        new = 0.0
        for a, b in keep:
            a_s, b_s = a / sr, (b / sr) if b is not None else duration
            w.writerow([fmt(a_s, True), fmt(b_s, True), fmt(new, True)])
            new += b_s - a_s
    return {"file": str(out_path), "log": str(log_path), "removed": removed, "cuts": len(cuts)}
