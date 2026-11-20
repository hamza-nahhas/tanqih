"""Long jobs, run as a separate process by the server (or by hand).

    python -m tanqih.worker prepare <file> [--full] [--no-transcribe]
    python -m tanqih.worker transcribe <file> <from> <to>
    python -m tanqih.worker render <file>

Prints "PROGRESS <0-100|-> <message>" lines that the server shows in the interface."""
import sys
from pathlib import Path

from .items import auto_propose, load_state, merge_list, needed_windows, save_state
from .media import analyse, ensure_wav, frame_db
from .transcribe import add_transcript

_last = [0.0]


def progress(pct, msg):
    if pct is None:
        pct = _last[0]
    _last[0] = pct
    print(f"PROGRESS {pct:.0f} {msg}", flush=True)


def prepare(media, full=False, transcribe=True):
    ensure_wav(media, progress)
    an = analyse(media, progress)
    progress(15, "بناء البنود من قائمة الأوقات")
    st = load_state(media)
    merge_list(st, an)
    auto_propose(st, an, frame_db(media))
    save_state(media, st)
    tr = None
    if full:
        tr = add_transcript(media, [[0, an["duration"]]], an["duration"], progress)
    elif transcribe:
        tr = add_transcript(media, needed_windows(st), an["duration"], progress)
    if tr and tr["words"]:
        st = load_state(media)  # re-read: the interface may have saved meanwhile
        auto_propose(st, an, frame_db(media), tr["words"])
        save_state(media, st)
    progress(100, "اكتمل التجهيز")


def main(argv=None):
    a = argv or sys.argv[1:]
    kind, media = a[0], Path(a[1])
    if kind == "prepare":
        prepare(media, full="--full" in a, transcribe="--no-transcribe" not in a)
    elif kind == "transcribe":
        an = analyse(media, progress)
        t0, t1 = float(a[2]), float(a[3])
        add_transcript(media, [[max(0, t0), min(an["duration"], t1)]], an["duration"], progress, pct0=3)
        progress(100, "اكتمل التفريغ")
    elif kind == "render":
        from .render import render
        st = load_state(media)
        res = render(media, st, progress)
        st = load_state(media)  # re-read: the interface may have saved while rendering
        st.setdefault("renders", []).append(res)
        save_state(media, st)
        print("RESULT " + res["file"], flush=True)
        progress(100, f"تم التصدير: {Path(res['file']).name}")
    else:
        raise SystemExit("unknown job " + kind)


if __name__ == "__main__":
    main()
