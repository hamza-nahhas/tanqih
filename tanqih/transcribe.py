"""Speech-to-text with faster-whisper (runs on the CPU, works on Windows and macOS)."""
import time

import numpy as np

from .config import cpu_threads, load_json, save_json, settings, work_dir
from .items import merge_ranges, subtract
from .media import ensure_wav, read_wav

_model = None


def get_model(progress=None):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        name = settings()["whisper_model"]
        if progress:
            progress(None, f"تحميل نموذج التفريغ {name} (يُنزَّل من الإنترنت أول مرة فقط)")
        _model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=cpu_threads())
    return _model


def transcript_path(media):
    return work_dir(media) / "transcript.json"


def load_transcript(media):
    return load_json(transcript_path(media), {"windows": [], "words": [], "segments": []})


def transcribe_range(media, t0, t1, margin=10.0, duration=None):
    """Transcribe [t0,t1]. Audio is read with a margin on each side so edge sentences are whole;
    words outside [t0,t1] are dropped."""
    a0, a1 = max(0.0, t0 - margin), t1 + margin
    if duration:
        a1 = min(duration, a1)
    audio = read_wav(ensure_wav(media), a0, a1).astype(np.float32) / 32768
    cfg = settings()
    segs, _ = get_model().transcribe(audio, language=cfg["language"] or None, word_timestamps=True,
                                     vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                                     beam_size=int(cfg.get("beam_size", 1)), condition_on_previous_text=False)
    words, segments = [], []
    for sg in segs:
        s, e = sg.start + a0, sg.end + a0
        if e < t0 or s > t1:
            continue
        segments.append({"s": round(s, 2), "e": round(e, 2), "text": sg.text.strip(),
                         "lp": round(sg.avg_logprob, 2), "ns": round(sg.no_speech_prob, 2)})
        for w in sg.words or []:
            ws, we = w.start + a0, w.end + a0
            if t0 <= (ws + we) / 2 <= t1:
                words.append({"w": w.word.strip(), "s": round(ws, 2), "e": round(we, 2),
                              "p": round(w.probability, 2)})
    return words, segments


def add_transcript(media, wins, duration, progress, pct0=20, pct1=100):
    tpath = transcript_path(media)
    tr = load_transcript(media)
    todo = []
    for w in merge_ranges(wins):
        todo += subtract(w, tr["windows"])
    total = sum(b - a for a, b in todo)
    if not total:
        return tr
    get_model(progress)
    done, t_start = 0.0, time.time()
    for a, b in todo:
        el = time.time() - t_start
        eta = f" — يبقى نحو {max(1, (total - done) / max(done / el, 0.1) / 60):.0f} دقيقة" if done > 30 and el > 5 else ""
        progress(pct0 + (pct1 - pct0) * done / total,
                 f"تفريغ {done / 60:.0f} من {total / 60:.0f} دقيقة{eta}")
        words, segs = transcribe_range(media, a, b, duration=duration)
        tr["words"] = sorted(tr["words"] + words, key=lambda x: x["s"])
        tr["segments"] = sorted(tr["segments"] + segs, key=lambda x: x["s"])
        tr["windows"] = merge_ranges(tr["windows"] + [[a, b]], gap=0.5)
        save_json(tpath, tr)
        done += b - a
    el = time.time() - t_start
    print(f"INFO transcribed {done:.0f}s audio in {el:.0f}s ({done / max(el, 1):.2f}x realtime)", flush=True)
    return tr
