"""Local web server for the interface. Run `python -m tanqih` and use the page it opens."""
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import wave
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, assist, cutlist, seamcheck
from .config import (MODELS, REPO, SR, UI, add_folder, file_id, id_path, is_allowed, library, load_json,
                     media_files, output_dir, remove_folder, save_settings, settings, work_dir)
from .items import auto_propose, load_state, merge_list, save_state, suggest
from .media import ensure_wav, frame_db, read_wav
from .transcribe import load_transcript

PY = sys.executable
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
state_lock = threading.Lock()


# ---------- jobs: one heavy lane (prepare / transcribe) and one light lane (render) ----------

class Jobs:
    def __init__(self):
        self.jobs = {}       # fid -> job dict
        self.lock = threading.Lock()
        self.queues = {"heavy": [], "light": []}
        self.cv = threading.Condition(self.lock)
        for lane in self.queues:
            threading.Thread(target=self._loop, args=(lane,), daemon=True).start()

    def add(self, fid, kind, args):
        lane = "light" if kind == "render" else "heavy"
        with self.lock:
            j = self.jobs.get(fid)
            if j and not j["done"]:
                return False
            self.jobs[fid] = {"kind": kind, "pct": 0, "msg": "في الانتظار…", "log": [], "done": False,
                              "error": None, "queued": True, "started": time.time(), "result": None,
                              "args": args, "proc": None, "name": id_path(fid).name}
            self.queues[lane].append(fid)
            self.cv.notify_all()
        return True

    def cancel(self, fid):
        with self.lock:
            j = self.jobs.get(fid)
            if not j or j["done"]:
                return False
            for q in self.queues.values():
                if fid in q:
                    q.remove(fid)
            j["cancelled"] = True
            if j["proc"]:
                try:
                    j["proc"].kill()
                except OSError:
                    pass
            else:
                j.update(done=True, error="أُلغي", queued=False)
        return True

    def public(self, fid=None):
        def pub(j):
            return {k: v for k, v in j.items() if k not in ("proc", "args")}
        with self.lock:
            if fid is not None:
                j = self.jobs.get(fid)
                return pub(j) if j else {}
            return {k: pub(v) for k, v in self.jobs.items()}

    def _loop(self, lane):
        while True:
            with self.lock:
                while not self.queues[lane]:
                    self.cv.wait()
                fid = self.queues[lane].pop(0)
                job = self.jobs[fid]
                job["queued"] = False
                job["msg"] = "بدء…"
                job["started"] = time.time()
            self._run(job)

    def _run(self, job):
        try:
            p = subprocess.Popen([PY, "-u", "-m", "tanqih.worker", *job["args"]],
                                 cwd=str(Path(__file__).resolve().parent.parent), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                                 creationflags=_NO_WINDOW)
            job["proc"] = p
            for line in p.stdout:
                line = line.rstrip()
                if line.startswith("PROGRESS "):
                    _, pct, msg = (line.split(" ", 2) + [""])[:3]
                    job["pct"], job["msg"] = float(pct), msg
                elif line.startswith("RESULT "):
                    job["result"] = line[7:]
                elif line and "it/s]" not in line and "B/s]" not in line:
                    job["log"] = (job["log"] + [line])[-40:]
            p.wait()
            if job.get("cancelled"):
                job["error"] = "أُلغي"
            elif p.returncode != 0:
                tail = [x for x in job["log"] if x.strip()][-6:]
                job["error"] = "\n".join(tail) or f"exit {p.returncode}"
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
        job["proc"] = None
        job["done"] = True
        job["finished"] = time.time()


JOBS = Jobs()


# ---------- helpers ----------

def media_of(fid):
    p = id_path(fid)
    if not is_allowed(p) or not p.exists():
        raise PermissionError("الملف غير موجود أو ليس في مجلد مضاف")
    return p


def file_summary(p):
    d = p.parent / ".tanqih" / p.name
    st = load_json(d / "state.json") if (d / "state.json").exists() else None
    side = p.with_suffix(".txt")
    if st is None and side.exists():
        st = import_sidecar(p)
    items = (st or {}).get("items", [])
    renders = [r for r in (st or {}).get("renders", []) if Path(r.get("file", "")).exists()]
    has_list = bool(((st or {}).get("list") or {}).get("text", "").strip())
    return {
        "id": file_id(p), "name": p.name, "size": p.stat().st_size,
        "duration": (st or {}).get("duration"),
        "has_list": has_list,
        "list_rows": len(cutlist.parse_text(st["list"]["text"])[0]) if has_list else 0,
        "prepared": (d / "analysis.json").exists(),
        "transcribed": (d / "transcript.json").exists(),
        "items": len(items),
        "approved": sum(1 for i in items if i["status"] == "approved"),
        "kept": sum(1 for i in items if i["status"] == "kept"),
        "open": sum(1 for i in items if i["status"] in ("proposed", "pending")),
        "rendered": renders[-1]["file"] if renders else None,
    }


def import_sidecar(p):
    """A reviewers' list saved as <recording name>.txt next to the recording is picked up."""
    side = p.with_suffix(".txt")
    try:
        text = side.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = side.read_text(encoding="cp1256", errors="replace")
    with state_lock:
        st = load_state(p)
        if not st["list"].get("text", "").strip():
            st["list"]["text"] = text
            st["list"]["source"] = side.name
            save_state(p, st)
    return st


def lib_view():
    out = []
    for f in library():
        files = []
        for p in media_files(f):
            try:
                files.append(file_summary(p))
            except OSError:
                continue
        out.append({"path": f, "name": Path(f).name or f, "exists": Path(f).is_dir(), "files": files})
    return out


def merge_save(disk, body):
    """Save the interface's copy without losing what a background job changed meanwhile."""
    if disk is None:
        return body
    dk = {i["id"]: i for i in disk.get("items", [])}
    out = []
    seen = set()
    for it in body.get("items", []):
        seen.add(it["id"])
        d = dk.get(it["id"])
        if (d and not it.get("edited") and it.get("status") not in ("approved", "kept")
                and d.get("proposed_by") != it.get("proposed_by")):
            out.append(d)      # a job refined it after the page loaded
        else:
            out.append(it)
    for iid, d in dk.items():
        if iid not in seen and d.get("source") != "manual":
            out.append(d)      # added by a job; the page can only delete manual items
    res = dict(disk)
    for k, v in body.items():
        if k not in ("items", "list", "renders", "file"):
            res[k] = v
    res["items"] = sorted(out, key=lambda i: i["start"])
    return res


def wav_bytes(samples):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


_update = {"t": 0, "info": None}


def update_info():
    """The newest release on GitHub, checked at most every six hours. Sends nothing but the request."""
    if not settings().get("check_updates", True):
        return {"newer": False}
    if time.time() - _update["t"] < 6 * 3600 and _update["info"] is not None:
        return _update["info"]
    info = {"newer": False, "current": __version__}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "tanqih"})
        with urllib.request.urlopen(req, timeout=5) as r:
            rel = json.loads(r.read())
        latest = str(rel.get("tag_name", "")).lstrip("vV")

        def key(v):
            return [int(x) if x.isdigit() else 0 for x in v.split(".")]
        info.update(latest=latest, url=rel.get("html_url"), notes=(rel.get("body") or "")[:1500],
                    newer=bool(latest) and key(latest) > key(__version__))
    except Exception:  # noqa: BLE001  (offline, rate-limited, no release yet)
        pass
    _update.update(t=time.time(), info=info)
    return info


def open_in_os(path):
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


PICK_SCRIPT = r"""
import sys, tkinter as tk
from tkinter import filedialog
r = tk.Tk(); r.withdraw()
try: r.attributes('-topmost', True)
except Exception: pass
r.update()
p = filedialog.askdirectory(title='Choose the folder with the recordings')
sys.stdout.write(p or '')
"""


def pick_folder():
    r = subprocess.run([PY, "-c", PICK_SCRIPT], capture_output=True, text=True, encoding="utf-8",
                       timeout=900)
    if r.returncode != 0:
        raise RuntimeError("تعذّر فتح نافذة اختيار المجلد؛ الصق مسار المجلد بدلًا من ذلك.")
    return r.stdout.strip()


# ---------- HTTP ----------

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def parts(self):
        u = urlparse(self.path)
        return [unquote(x) for x in u.path.strip("/").split("/") if x], {k: v[0] for k, v in parse_qs(u.query).items()}

    def do_GET(self):
        parts, q = self.parts()
        try:
            if not parts or parts[0] == "index.html":
                return self.send(200, (UI / "index.html").read_bytes(), "text/html; charset=utf-8")
            if parts[0] == "ui" and len(parts) == 2 and (UI / parts[1]).is_file():
                ext = parts[1].rsplit(".", 1)[-1]
                ctype = {"js": "text/javascript", "css": "text/css", "svg": "image/svg+xml",
                         "png": "image/png"}.get(ext, "application/octet-stream")
                return self.send(200, (UI / parts[1]).read_bytes(), ctype + ("; charset=utf-8" if ext in ("js", "css") else ""))
            if parts == ["api", "ping"]:
                return self.send(200, {"app": "tanqih", "version": __version__})
            if parts == ["api", "library"]:
                return self.send(200, lib_view())
            if parts == ["api", "settings"]:
                return self.send(200, {"settings": settings(), "models": MODELS,
                                       "categories": cutlist.CATEGORY_NAMES, "version": __version__})
            if parts == ["api", "jobs"]:
                return self.send(200, JOBS.public())
            if parts == ["api", "update"]:
                return self.send(200, update_info())
            if parts[:2] == ["api", "file"] and len(parts) == 3:
                m = media_of(parts[2])
                d = work_dir(m)
                st = load_json(d / "state.json")
                if st is None and m.with_suffix(".txt").exists():
                    st = import_sidecar(m)
                return self.send(200, {
                    "id": parts[2], "name": m.name, "folder": str(m.parent),
                    "state": st, "analysis": load_json(d / "analysis.json"),
                    "transcript": load_transcript(m), "job": JOBS.public(parts[2]),
                })
            if parts[:2] == ["api", "list"] and len(parts) == 3:
                m = media_of(parts[2])
                st = load_json(work_dir(m) / "state.json")
                if st is None and m.with_suffix(".txt").exists():
                    st = import_sidecar(m)
                st = st or {}
                return self.send(200, {"list": st.get("list") or {"text": ""}, "duration": st.get("duration")})
            if parts[:2] == ["api", "clip"] and len(parts) == 3:
                m = media_of(parts[2])
                t0, t1 = float(q["from"]), float(q["to"])
                return self.send(200, wav_bytes(read_wav(ensure_wav(m), t0, t1)), "audio/wav")
            return self.send(404, {"error": "not found"})
        except PermissionError as e:
            return self.send(403, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self.send(500, {"error": str(e)})

    def do_POST(self):
        parts, _ = self.parts()
        try:
            b = self.body()
            if parts == ["api", "library", "add"]:
                path = b.get("path") or pick_folder()
                if not path:
                    return self.send(200, {"ok": False})
                return self.send(200, {"ok": True, "path": add_folder(path.strip().strip('"'))})
            if parts == ["api", "library", "remove"]:
                remove_folder(b["path"])
                return self.send(200, {"ok": True})
            if parts == ["api", "settings"]:
                return self.send(200, {"settings": save_settings(b)})
            if parts == ["api", "open"]:
                target = b.get("path")
                if b.get("id"):
                    m = media_of(b["id"])
                    target = output_dir(m) if b.get("what") == "output" else m.parent
                if target and Path(target).exists():
                    open_in_os(target)
                return self.send(200, {"ok": True})
            if parts == ["api", "list", "parse"]:
                rows, ignored = cutlist.parse_text(b.get("text", ""), b.get("remaining", False), b.get("two_part", "ms"))
                cutlist.check(rows, b.get("duration"))
                return self.send(200, {"rows": rows, "ignored": ignored})
            if parts == ["api", "prepare-many"]:
                n = 0
                for fid in b.get("ids", []):
                    m = media_of(fid)
                    n += JOBS.add(fid, "prepare", ["prepare", str(m)])
                return self.send(200, {"ok": True, "queued": n})
            if len(parts) != 3 or parts[0] != "api":
                return self.send(404, {"error": "not found"})
            act, fid = parts[1], parts[2]
            m = media_of(fid)
            if act == "save":
                with state_lock:
                    path = work_dir(m) / "state.json"
                    disk = load_json(path)
                    if path.exists():
                        bak = work_dir(m) / "backups"
                        bak.mkdir(exist_ok=True)
                        shutil.copy(path, bak / f"state-{time.strftime('%Y%m%d-%H%M%S')}.json")
                        for old in sorted(bak.glob("state-*.json"))[:-200]:
                            old.unlink()
                    st = merge_save(disk, b)
                    save_state(m, st)
                return self.send(200, {"ok": True})
            if act == "list":
                with state_lock:
                    st = load_state(m)
                    st["list"] = {"text": b.get("text", ""), "remaining": bool(b.get("remaining")),
                                  "two_part": b.get("two_part", "ms")}
                    if not st["list"]["text"].strip():
                        st["list"]["text"] = ""   # an empty list never re-imports the .txt next to the file
                        st["list"]["cleared"] = True
                    an = load_json(work_dir(m) / "analysis.json")
                    if an:
                        merge_list(st, an)
                        words = load_transcript(m)["words"] or None
                        auto_propose(st, an, frame_db(m), words)
                        suggest(st, an, frame_db(m), words)
                    save_state(m, st)
                queued = False
                if b.get("prepare", True) and (an or st["list"]["text"].strip()):
                    queued = JOBS.add(fid, "prepare", ["prepare", str(m)])
                return self.send(200, {"ok": True, "state": st, "queued": queued})
            if act == "prepare":
                args = ["prepare", str(m)] + (["--full"] if b.get("full") else [])
                ok = JOBS.add(fid, "prepare", args)
                return self.send(200 if ok else 409, {"ok": ok})
            if act == "transcribe":
                ok = JOBS.add(fid, "transcribe", ["transcribe", str(m), str(b["from"]), str(b["to"])])
                return self.send(200 if ok else 409, {"ok": ok})
            if act == "render":
                ok = JOBS.add(fid, "render", ["render", str(m)])
                return self.send(200 if ok else 409, {"ok": ok})
            if act == "cancel":
                return self.send(200, {"ok": JOBS.cancel(fid)})
            if act == "check":
                an = load_json(work_dir(m) / "analysis.json")
                if not an:
                    return self.send(400, {"error": "جهّز الملف أولًا"})
                st = load_state(m)
                return self.send(200, {"warnings": seamcheck.check(st, an, load_transcript(m), frame_db(m))})
            if act == "markers":
                from .render import write_markers
                st = load_state(m)
                files = write_markers(m, st)
                return self.send(200, {"files": files})
            if act == "assist-export":
                st = load_state(m)
                an = load_json(work_dir(m) / "analysis.json")
                if not an:
                    return self.send(400, {"error": "جهّز الملف أولًا"})
                ids = b.get("ids")
                batches, skipped = assist.export_batches(m.name, st, an, load_transcript(m), frame_db(m),
                                                         set(ids) if ids is not None else None)
                return self.send(200, {"batches": batches, "skipped": skipped})
            if act == "assist-import":
                props = assist.extract_json(b.get("text", ""))
                with state_lock:
                    st = load_state(m)
                    an = load_json(work_dir(m) / "analysis.json")
                    rep = assist.apply_proposals(st, an, props, force=bool(b.get("force")), by="ai")
                    save_state(m, st)
                return self.send(200, {"report": rep, "state": st})
            return self.send(404, {"error": "not found"})
        except PermissionError as e:
            return self.send(403, {"error": str(e)})
        except (ValueError, KeyError, RuntimeError) as e:
            return self.send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.loads(r.read()).get("app") == "tanqih"
    except Exception:  # noqa: BLE001
        return False


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    port = int(settings().get("port") or 8765)
    if already_running(port):
        url = f"http://127.0.0.1:{port}/"
        print("البرنامج يعمل من قبل:", url, flush=True)
        if "--no-browser" not in argv:
            webbrowser.open(url)
        return
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), H)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("لا يوجد منفذ متاح")
    url = f"http://127.0.0.1:{port}/"
    print(f"تنقيح {__version__} يعمل على {url}", flush=True)
    print("اترك هذه النافذة مفتوحة أثناء العمل، وأغلقها لإيقاف البرنامج.", flush=True)
    if "--no-browser" not in argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
