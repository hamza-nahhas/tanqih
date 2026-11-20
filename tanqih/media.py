"""ffmpeg, the 16 kHz analysis copy, and the loudness / pause map."""
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

from .config import FRAME, SR, load_json, save_json, settings, work_dir

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def ffmpeg_exe():
    custom = (settings().get("ffmpeg") or "").strip()
    if custom and Path(custom).exists():
        return custom
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise SystemExit("لم يُعثر على ffmpeg. ثبّته أو حدّد مساره في الإعدادات.") from e


def run(args, **kw):
    return subprocess.run(args, creationflags=_NO_WINDOW, **kw)


def popen(args, **kw):
    return subprocess.Popen(args, creationflags=_NO_WINDOW, **kw)


def probe(path, with_bitrate=False):
    """(sample_rate, channels, duration_seconds[, bit_rate]) of the first audio stream."""
    import av
    with av.open(str(path)) as c:
        st = c.streams.audio[0]
        cc = st.codec_context
        ch = getattr(cc, "channels", None) or len(cc.layout.channels)
        dur = float(c.duration / 1e6) if c.duration else None
        if dur is None and st.duration and st.time_base:
            dur = float(st.duration * st.time_base)
        res = (int(cc.sample_rate), int(ch), dur)
        if with_bitrate:
            br = cc.bit_rate or st.bit_rate or (c.bit_rate if len(c.streams) == 1 else None)
            res += (int(br) if br else None,)
        return res


def wav_path(media):
    return work_dir(media) / "audio16k.wav"


def ensure_wav(media, progress=None):
    dst = wav_path(media)
    if dst.exists():
        return dst
    if progress:
        progress(1, "استخراج الصوت للتحليل")
    tmp = dst.with_name("audio16k.tmp.wav")
    r = run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", str(SR),
             "-c:a", "pcm_s16le", str(tmp)], capture_output=True, text=True, encoding="utf-8",
            errors="replace")
    if r.returncode != 0:
        raise SystemExit("تعذّر قراءة الملف بـ ffmpeg:\n" + (r.stderr or "")[-600:])
    tmp.replace(dst)
    return dst


def read_wav(path, t0=0.0, t1=None):
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        n = w.getnframes()
        a = int(max(0, t0) * sr)
        b = n if t1 is None else min(n, int(t1 * sr))
        w.setpos(min(a, n))
        data = w.readframes(max(0, b - a))
    return np.frombuffer(data, dtype=np.int16)


def analyse(media, progress=None):
    """Loudness per 20 ms frame, an overview for the top strip, and the list of pauses."""
    d = work_dir(media)
    out = d / "analysis.json"
    if out.exists() and (d / "frame_db.npy").exists():
        return load_json(out)
    if progress:
        progress(8, "تحليل مستوى الصوت والسكتات")
    a = read_wav(ensure_wav(media, progress)).astype(np.float32) / 32768
    duration = len(a) / SR
    n = int(SR * FRAME)
    frames = a[: len(a) // n * n].reshape(-1, n)
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-10)
    np.save(d / "frame_db.npy", db.astype(np.float32))
    floor, speech = np.percentile(db, 15), np.percentile(db, 80)
    thr = floor + 0.35 * (speech - floor)
    quiet = db < thr
    pauses, start = [], None
    for i, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * FRAME >= 0.25:
                pauses.append([round(start * FRAME, 2), round(i * FRAME, 2)])
            start = None
    k = int(0.5 / FRAME)
    ov = db[: len(db) // k * k].reshape(-1, k)
    overview = np.round(10 * np.log10(np.mean(10 ** (ov / 10), axis=1))).astype(int).tolist()
    res = {"duration": duration, "overview_step": 0.5, "overview": overview,
           "floor_db": float(floor), "speech_db": float(speech), "pause_threshold_db": float(thr),
           "pauses": pauses}
    save_json(out, res)
    return res


def frame_db(media):
    p = work_dir(media) / "frame_db.npy"
    return np.load(p) if p.exists() else None


def loud_bursts(db, speech_db, t0, t1, above=9.0, join=0.5):
    """Sudden loud sounds (sneeze, cough, laughter): [[start, end, peak_dB_above_speech], ...]."""
    if db is None:
        return []
    i0, i1 = max(0, int(t0 / FRAME)), min(len(db), int(t1 / FRAME))
    seg = db[i0:i1]
    out = []
    for i in np.where(seg > speech_db + above)[0]:
        t = (i0 + i) * FRAME
        if out and t - out[-1][1] < join:
            out[-1][1] = t + FRAME
            out[-1][2] = max(out[-1][2], float(seg[i] - speech_db))
        else:
            out.append([t, t + FRAME, float(seg[i] - speech_db)])
    return out
