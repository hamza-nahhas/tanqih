"""Command line.  With no command it opens the interface.

    python -m tanqih                         open the interface in the browser
    python -m tanqih items  <file>           list the items of a recording
    python -m tanqih prepare <file> [--full] analyse + transcribe (the same job as the interface)
    python -m tanqih peek   <file> <from> <to>   timed transcript with pauses and loud sounds
    python -m tanqih words  <file> <from> <to>   one word per line with its times
    python -m tanqih prompt <file> [--all]   the text for an AI chat (all open items, or every item)
    python -m tanqih apply  <file> <proposals.json> [--force]   apply proposals (same JSON as the chat reply)
    python -m tanqih check  <file>           check the approved cuts: words inside an edge, repeated speech, ...
    python -m tanqih render <file>           write the trimmed file from the approved cuts

Times are seconds or h:mm:ss(.s). See AGENTS.md for working with a coding agent."""
import sys
from pathlib import Path


def _utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main():
    _utf8()
    a = sys.argv[1:]
    if not a or a[0] in ("ui", "--no-browser"):
        from .server import main as serve
        return serve(a[1:] if a and a[0] == "ui" else a)
    if a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, media = a[0], Path(a[1]).resolve()
    from .config import load_json, work_dir
    from .util import fmt, parse_time
    if cmd == "prepare":
        from .worker import prepare
        return prepare(media, full="--full" in a)
    if cmd == "render":
        from .worker import main as w
        return w(["render", str(media)])
    an = load_json(work_dir(media) / "analysis.json")
    if an is None:
        raise SystemExit("الملف لم يُجهّز بعد: python -m tanqih prepare <file>")
    from .items import load_state, save_state
    from .media import frame_db
    from .transcribe import load_transcript
    st = load_state(media)
    if cmd == "items":
        for it in st["items"]:
            raw = it.get("raw") or {}
            print(f"{it['id']:<10} {it['status']:<9} {fmt(it['start'], True)} – {fmt(it['end'], True)}"
                  f"  [{it.get('category', '')}] {it.get('label', '')}"
                  + (f"  (القائمة: {raw.get('start')}{'–' + raw['end'] if raw.get('end') else ''}"
                     f"{' متبقٍّ' if raw.get('time_ref') == 'remaining' else ''})" if raw.get("start") else ""))
    elif cmd in ("peek", "words"):
        t0, t1 = parse_time(a[2]), parse_time(a[3])
        tr = load_transcript(media)
        if cmd == "words":
            for w in tr["words"]:
                if t0 <= w["s"] <= t1:
                    print(f"{fmt(w['s'], True)} {fmt(w['e'], True)} {w['p']:.2f} {w['w']}")
        else:
            from .assist import covered, format_window
            if not covered(tr, t0, t1):
                print("تنبيه: جزء من هذا المدى غير مفرّغ. فرّغه بـ: python -m tanqih.worker transcribe <file> <from> <to>")
            print("\n".join(format_window(tr["words"], an, frame_db(media), t0, t1)))
    elif cmd == "prompt":
        from .assist import export_batches
        ids = None if "--all" in a else {i["id"] for i in st["items"] if i["status"] in ("proposed", "pending")}
        batches, skipped = export_batches(media.name, st, an, load_transcript(media), frame_db(media), ids)
        for k, b in enumerate(batches, 1):
            if len(batches) > 1:
                print(f"\n########## الدفعة {k} من {len(batches)} ##########\n")
            print(b["text"])
        if skipped:
            print("بنود لم تُفرَّغ نوافذها بعد: " + ", ".join(skipped), file=sys.stderr)
    elif cmd == "check":
        from .seamcheck import check
        warns = check(st, an, load_transcript(media), frame_db(media))
        for w in warns:
            print(f"{'!' if w['level'] == 'warn' else '-'} {fmt(w['start'])} [{w['id']}] {w['msg']}"
                  + (f"  -> {w['fix']}" if w["fix"] else ""))
        if not warns:
            print("لا مشكلات.")
    elif cmd == "apply":
        from .assist import apply_proposals, extract_json
        props = extract_json(Path(a[2]).read_text(encoding="utf-8"))
        rep = apply_proposals(st, an, props, force="--force" in a, by="agent")
        save_state(media, st)
        for k, v in rep.items():
            if v:
                print(k, len(v), ", ".join(v[:20]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
