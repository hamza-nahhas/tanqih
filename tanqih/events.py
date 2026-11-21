"""Find laughter, sneezes, coughs and applause with a small sound-event model (no internet after
the first download). The model is AST trained on Google's AudioSet (527 sound classes), run through
onnxruntime, which faster-whisper already installs.

The features follow transformers' ASTFeatureExtractor exactly (Kaldi-style log-mel filter bank,
128 bins, 25 ms frames every 10 ms, 1024 frames = 10.24 s per window)."""
import json
import time

import numpy as np

from .config import SR, load_json, save_json, work_dir
from .media import ensure_wav, read_wav

REPO = "Xenova/ast-finetuned-audioset-10-10-0.4593"
MODEL_FILE = "onnx/model_quantized.onnx"
MEAN, STD = -4.2677393, 4.5689974
N_FFT, WIN, HOP, N_MEL, MAX_FRAMES = 512, 400, 160, 128, 1024
WINDOW_S = (MAX_FRAMES - 1) * HOP / SR + WIN / SR     # 10.24 s

# AudioSet classes we care about -> our names
GROUPS = {
    "ضحك": ["Laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle", "Baby laughter"],
    "عطاس": ["Sneeze"],
    "سعال": ["Cough", "Throat clearing"],
    "تصفيق": ["Applause", "Clapping"],
}

_session = None
_labels = None
_mel = None


def _mel_filters():
    """Kaldi mel scale, triangles built in mel space (transformers: mel_scale='kaldi',
    triangularize_in_mel_space=True), 257 FFT bins x 128 filters."""
    def mel(f):
        return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)
    edges = np.linspace(mel(20.0), mel(SR / 2), N_MEL + 2)
    fft_mel = mel(np.linspace(0, SR / 2, N_FFT // 2 + 1))
    left, center, right = edges[:-2], edges[1:-1], edges[2:]
    up = (fft_mel[:, None] - left[None, :]) / (center - left)[None, :]
    down = (right[None, :] - fft_mel[:, None]) / (right - center)[None, :]
    return np.maximum(0.0, np.minimum(up, down))       # (257, 128)


def features(wave):
    """float32 mono 16 kHz in [-1, 1] -> normalized (1024, 128) input."""
    global _mel
    if _mel is None:
        _mel = _mel_filters()
    wave = np.asarray(wave, dtype=np.float64)
    n = max(0, 1 + (len(wave) - WIN) // HOP)
    out = np.zeros((MAX_FRAMES, N_MEL), dtype=np.float64)
    if n:
        idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
        fr = wave[idx]
        fr = fr - fr.mean(axis=1, keepdims=True)                  # remove DC offset
        fr[:, 1:] = fr[:, 1:] - 0.97 * fr[:, :-1]                 # pre-emphasis
        fr[:, 0] *= 1 - 0.97
        fr *= np.hanning(WIN)[None, :]
        spec = np.abs(np.fft.rfft(fr, n=N_FFT, axis=1)) ** 2
        logmel = np.log(np.maximum(spec @ _mel, 1.192092955078125e-07))
        out[:min(n, MAX_FRAMES)] = logmel[:MAX_FRAMES]
    return ((out - MEAN) / (STD * 2)).astype(np.float32)


def _load(progress=None):
    global _session, _labels
    if _session is None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        if progress:
            progress(None, "تحميل نموذج تمييز الأصوات (يُنزَّل أول مرة فقط، نحو 90 MB)")
        cfg = json.load(open(hf_hub_download(REPO, "config.json"), encoding="utf-8"))
        _labels = {int(k): v for k, v in cfg["id2label"].items()}
        opts = ort.SessionOptions()
        from .config import cpu_threads
        opts.intra_op_num_threads = cpu_threads()
        _session = ort.InferenceSession(hf_hub_download(REPO, MODEL_FILE), opts,
                                        providers=["CPUExecutionProvider"])
    return _session, _labels


def classify(waves):
    """List of waveforms (each up to 10.24 s) -> sigmoid scores (n, 527)."""
    sess, _ = _load()
    x = np.stack([features(w) for w in waves])
    logits = sess.run(None, {"input_values": x})[0]
    return 1.0 / (1.0 + np.exp(-logits))


def group_index():
    _, labels = _load()
    by_name = {v: k for k, v in labels.items()}
    return {g: [by_name[n] for n in names if n in by_name] for g, names in GROUPS.items()}


# ---------- scanning a whole recording ----------

THRESH = {"ضحك": 0.08, "عطاس": 0.3, "سعال": 0.3, "تصفيق": 0.3}   # tuned on reviewed sessions
SCAN_VERSION = 1


def events_path(media):
    return work_dir(media) / "events.json"


def load_events(media):
    return (load_json(events_path(media), {}) or {}).get("events", [])


def _batches(audio, starts, length, B=8):
    out = []
    for i in range(0, len(starts), B):
        out.append(classify([audio[int(s * SR):int((s + length) * SR)] for s in starts[i:i + B]]))
    return np.concatenate(out) if out else np.zeros((0, 527), dtype=np.float32)


def scan(media, progress=None, pct0=15, pct1=30):
    """Laughter, sneezes, coughs and applause in the whole recording, cached in events.json."""
    cached = load_json(events_path(media))
    if cached and cached.get("version") == SCAN_VERSION:
        return cached["events"]
    _load(progress)
    gi = group_index()
    audio = read_wav(ensure_wav(media)).astype(np.float32) / 32768
    D = len(audio) / SR
    t0 = time.time()

    # pass 1: a window every 10 s
    starts = np.arange(0, max(1.0, D - 1.0), 10.0)
    scores = []
    for i in range(0, len(starts), 8):
        scores.append(_batches(audio, starts[i:i + 8], WINDOW_S))
        if progress:
            done = min(len(starts), i + 8) / len(starts)
            left = (time.time() - t0) / done * (1 - done)
            progress(pct0 + (pct1 - pct0) * 0.8 * done,
                     f"البحث عن الضحك والعطاس {done:.0%}" + (f" — يبقى نحو {left / 60:.0f} دقيقة" if left > 90 else ""))
    scores = np.concatenate(scores)

    def group_scores(S):
        return {g: S[:, idx].max(axis=1) for g, idx in gi.items() if idx}

    gs = group_scores(scores)
    # pass 2: windows shifted by 5 s around anything that looks close, so nothing falls between windows
    near = set()
    for g, sc in gs.items():
        for k in np.where(sc > THRESH[g] * 0.4)[0]:
            near.update((starts[k] - 5, starts[k] + 5))
    extra = np.array(sorted(s for s in near if 0 <= s < D - 1))
    if len(extra):
        starts = np.concatenate([starts, extra])
        scores = np.concatenate([scores, _batches(audio, extra, WINDOW_S)])
        gs = group_scores(scores)

    # localize: merge flagged windows into regions, score 3 s pieces every second inside each region,
    # and keep the pieces near that region's own best (a loud sneeze must not hide a quiet one)
    events = []
    for g, sc in gs.items():
        regions = []
        for k in sorted(np.where(sc > THRESH[g])[0], key=lambda k: starts[k]):
            a, b, p = float(starts[k]), float(starts[k]) + WINDOW_S, float(sc[k])
            if regions and a <= regions[-1][1]:
                regions[-1][1] = max(regions[-1][1], b)
                regions[-1][2] = max(regions[-1][2], p)
            else:
                regions.append([a, b, p])
        for ra, rb, rp in regions:
            pieces = np.arange(ra, max(ra + 0.1, min(rb, D) - 2.9), 1.0)
            ps = _batches(audio, pieces, 3.0)[:, gi[g]].max(axis=1)
            best = float(ps.max()) if len(ps) else 0.0
            if best < THRESH[g] * 0.3:        # cannot be pinned down: keep the whole region
                events.append({"kind": g, "start": round(ra, 2), "end": round(min(D, rb), 2),
                               "score": round(rp, 3), "coarse": True})
                continue
            segs = []
            for s, p in zip(pieces, ps):
                if p < best * 0.5:
                    continue
                if segs and s <= segs[-1][1]:
                    segs[-1][1] = s + 3.0
                    segs[-1][2] = max(segs[-1][2], float(p))
                else:
                    segs.append([float(s), float(s) + 3.0, float(p)])
            for a, b, p in segs:
                events.append({"kind": g, "start": round(a, 2), "end": round(min(D, b), 2),
                               "score": round(max(p, rp), 3)})
    events.sort(key=lambda e: e["start"])
    save_json(events_path(media), {"version": SCAN_VERSION, "model": REPO, "events": events,
                                   "seconds": round(time.time() - t0)})
    return events
