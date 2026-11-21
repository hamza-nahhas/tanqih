"""Write the trimmed recording from the approved cuts.

The original is decoded once, the approved ranges are dropped sample-accurately, every join gets a
short fade-out / fade-in, and the result is encoded a single time. A cut log with a time map
(original -> new) is written next to it."""
import csv
from pathlib import Path

import numpy as np

from .config import output_dir, settings
from .media import ffmpeg_exe, popen, probe, run
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
    res = {"file": str(out_path), "log": str(log_path), "removed": removed, "cuts": len(cuts)}
    if cfg.get("export_markers", True):
        res["markers"] = write_markers(src, st)
    if cfg.get("export_benefits", True):
        progress(97, "كتابة ملف الفوائد")
        b = write_benefits(src, st, ext, enc_args, sr_out)
        if b:
            res["benefits"] = b
    return res


BENEFIT = "فائدة"


def write_markers(media, st):
    """Labels on the ORIGINAL recording's timeline: Audacity (File > Import > Labels) and an EDL that
    DaVinci Resolve reads as timeline markers (Timeline > Import > Timeline Markers from EDL;
    25 fps, timeline starting at 01:00:00:00)."""
    src = Path(media)
    out_dir = output_dir(src)
    rows = []
    for it in sorted(st["items"], key=lambda i: i["start"]):
        if it.get("category") == BENEFIT:
            kind, color = "فائدة", "ResolveColorYellow"
        elif it["status"] == "approved":
            kind, color = "قص", "ResolveColorRed"
        elif it["status"] == "kept":
            kind, color = "يبقى", "ResolveColorGreen"
        else:
            kind, color = "لم يُحسم", "ResolveColorPurple"
        label = f"{kind}: {it.get('label') or it.get('category') or ''}".strip()
        rows.append((it["start"], it["end"], label.replace("\t", " ").replace("|", "/"), color))
    aud = out_dir / f"{src.stem} - علامات Audacity.txt"
    with open(aud, "w", encoding="utf-8", newline="\n") as f:
        for s, e, label, _ in rows:
            f.write(f"{s:.3f}\t{e:.3f}\t{label}\n")
    fps = 25

    def tc(fr):
        return f"{fr // (3600 * fps):02d}:{fr // (60 * fps) % 60:02d}:{fr // fps % 60:02d}:{fr % fps:02d}"

    edl = out_dir / f"{src.stem} - علامات DaVinci.edl"
    with open(edl, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(f"TITLE: {src.stem}\nFCM: NON-DROP FRAME\n\n")
        for k, (s, e, label, color) in enumerate(rows, 1):
            dur = max(1, int(round((e - s) * fps)))
            f0 = int(round((s + 3600) * fps))     # the timeline starts at 01:00:00:00
            f.write(f"{k:03d}  001      V     C        {tc(f0)} {tc(f0 + 1)} {tc(f0)} {tc(f0 + 1)}  \n")
            f.write(f" |C:{color} |M:{label} |D:{dur}\n\n")
    return [str(aud), str(edl)]


def write_benefits(media, st, ext, enc_args, sr_out):
    """The items marked فائدة, joined into one file with a short pause between them, plus an index."""
    src = Path(media)
    segs = sorted(([i["start"], i["end"], i.get("label", "")] for i in st["items"]
                   if i.get("category") == BENEFIT and i["end"] - i["start"] > 0.5), key=lambda x: x[0])
    if not segs:
        return None
    out_dir = output_dir(src)
    out = out_dir / f"{src.stem} - فوائد{ext}"
    tmp = out.with_name(out.stem + ".part" + ext)
    parts, labels = [], []
    for k, (a, b, _) in enumerate(segs):
        fade = min(0.25, (b - a) / 4)
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={fade:.3f},afade=t=out:st={b - a - fade:.3f}:d={fade:.3f},"
                     f"apad=pad_dur=0.8[s{k}]")
        labels.append(f"[s{k}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=0:a=1[out]"
    r = run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, "-map", "[out]",
             "-ar", str(sr_out), *enc_args, "-f", {".m4a": "ipod", ".ogg": "ogg"}.get(ext, ext[1:]), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise SystemExit("فشل كتابة ملف الفوائد:\n" + (r.stderr or "")[-400:])
    tmp.replace(out)
    idx = out_dir / f"{src.stem} - فوائد.txt"
    pos = 0.0
    with open(idx, "w", encoding="utf-8") as f:
        f.write(f"فوائد «{src.name}»\n\n")
        for k, (a, b, label) in enumerate(segs, 1):
            f.write(f"{k}. {label}\n   في ملف الفوائد: {fmt(pos)}    في الأصل: {fmt(a)} – {fmt(b)}\n")
            pos += (b - a) + 0.8
    return str(out)
