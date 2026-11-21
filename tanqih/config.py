"""Settings, the folder library, and where each recording's working files live."""
import base64
import json
import os
import sys
from pathlib import Path

APP_NAME = "Tanqih"
PKG = Path(__file__).resolve().parent
UI = PKG / "ui"

AUDIO_EXT = {".ogg", ".oga", ".opus", ".m4a", ".mp3", ".wav", ".aac", ".flac", ".wma", ".amr",
             ".mp4", ".m4v", ".mkv", ".webm", ".mov"}
WORK_DIRNAME = ".tanqih"      # created next to the recordings
OUTPUT_DIRNAME = "منقح"       # default output folder, next to the recordings
SR = 16000                     # analysis sample rate
FRAME = 0.02                   # loudness frame, seconds

DEFAULTS = {
    "language": "ar",
    "whisper_model": "large-v3-turbo",
    "cpu_threads": 0,          # 0 = automatic
    "beam_size": 1,
    "window_pad": 150,         # seconds searched on each side of an approximate time
    "long_cut": 360,           # longer cuts get two edge windows instead of one
    "fade_ms": 15,
    "output_format": "same",   # same | m4a | mp3 | opus | flac | wav
    "output_bitrate": "160k",
    "output_dir": "",          # empty = a folder named منقح next to the recording
    "ffmpeg": "",              # empty = ffmpeg on PATH, else the bundled one
    "assist_batch_chars": 30000,
    "suggest_silence": 5.0,    # suggest shortening silences longer than this (seconds); 0 = off
    "suggest_loud": True,      # suggest sudden loud sounds (sneeze, cough, knock) anywhere in the file
    "export_benefits": True,   # also write the items marked فائدة into a separate file
    "export_markers": True,    # also write Audacity labels and a DaVinci Resolve marker list
    "check_updates": True,
    "port": 8765,
}

MODELS = ["large-v3-turbo", "large-v3", "medium", "small", "base", "tiny"]
REPO = "hamza-nahhas/tanqih"


def config_dir():
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path, default=None):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
    return default


def save_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def settings():
    s = dict(DEFAULTS)
    s.update(load_json(config_dir() / "settings.json", {}) or {})
    return s


def save_settings(new):
    cur = load_json(config_dir() / "settings.json", {}) or {}
    for k, v in new.items():
        if k in DEFAULTS:
            cur[k] = v
    save_json(config_dir() / "settings.json", cur)
    return settings()


def cpu_threads():
    n = int(settings().get("cpu_threads") or 0)
    return n if n > 0 else max(1, min(os.cpu_count() or 4, 12))


# ---------- library: the folders the user added ----------

def library():
    lib = load_json(config_dir() / "library.json", {}) or {}
    return [f for f in lib.get("folders", []) if f]


def add_folder(path):
    p = str(Path(path).expanduser().resolve())
    if not Path(p).is_dir():
        raise ValueError("المجلد غير موجود: " + p)
    folders = library()
    if p not in folders:
        folders.append(p)
    save_json(config_dir() / "library.json", {"folders": folders})
    return p


def remove_folder(path):
    folders = [f for f in library() if f != path]
    save_json(config_dir() / "library.json", {"folders": folders})


def media_files(folder):
    d = Path(folder)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT
                   and not p.name.startswith(".")), key=lambda p: natural_key(p.name))


def natural_key(s):
    import re
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def is_allowed(media):
    """Only files inside a library folder may be opened through the interface."""
    m = Path(media).resolve()
    return m.suffix.lower() in AUDIO_EXT and any(m.parent == Path(f) for f in library())


# ---------- per-recording working folder ----------

def work_dir(media):
    m = Path(media)
    d = m.parent / WORK_DIRNAME / m.name
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir(media):
    o = settings().get("output_dir") or ""
    d = Path(o) if o else Path(media).parent / OUTPUT_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_id(path):
    return base64.urlsafe_b64encode(str(Path(path)).encode("utf-8")).decode("ascii").rstrip("=")


def id_path(fid):
    pad = "=" * (-len(fid) % 4)
    return Path(base64.urlsafe_b64decode(fid + pad).decode("utf-8"))
