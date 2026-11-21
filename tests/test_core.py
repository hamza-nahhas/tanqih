import json
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tanqih-test-")
os.environ["APPDATA"] = TMP                 # keep the real settings untouched
os.environ["XDG_CONFIG_HOME"] = TMP
os.environ["HOME"] = TMP

from tanqih import assist, cutlist, items  # noqa: E402


class CutList(unittest.TestCase):
    def one(self, line, **kw):
        r = cutlist.parse_line(line, **kw)
        self.assertIsNotNone(r, line)
        return r

    def test_range_and_label(self):
        r = self.one("1:00:50 - 1:01:30 رد السلام")
        self.assertEqual((r["start"], r["end"], r["label"], r["action"]), (3650, 3690, "رد السلام", "cut"))

    def test_from_to_words(self):
        r = self.one("من 1:10:00 إلى 1:12:30 مزح")
        self.assertEqual((r["start"], r["end"], r["label"], r["category"]), (4200, 4350, "مزح", "مزح"))

    def test_point(self):
        r = self.one("0:08:00 عطسة")
        self.assertEqual((r["start"], r["end"], r["action"], r["category"]), (480, None, "find", "عطاس/سعال"))

    def test_arabic_digits_and_numbering(self):
        r = self.one("3) ١:٠٥:٣٠ – ١:٠٦:٠٠ كلام جانبي")
        self.assertEqual((r["start"], r["end"], r["label"]), (3930, 3960, "كلام جانبي"))

    def test_end_and_start_words(self):
        r = self.one("1:30:00 - النهاية   نهاية الدرس")
        self.assertEqual((r["end"], r["label"]), ("END", "نهاية الدرس"))
        r = self.one("البداية - 0:00:40 قبل بدء الدرس")
        self.assertEqual((r["start"], r["end"]), (0.0, 40))

    def test_remaining(self):
        r = self.one("1:02:10 مزح (متبقي)")
        self.assertEqual((r["time_ref"], r["label"]), ("remaining", "مزح"))
        s, e, point = cutlist.resolve(r, 4580.0)
        self.assertEqual((s, point), (850.0, True))
        r = self.one("1:07:30 - 1:07:23 (خالد)", remaining=True)
        s, e, _ = cutlist.resolve(r, 4580.0)
        self.assertEqual((s, e), (530.0, 537.0))

    def test_decide_and_two_part(self):
        self.assertEqual(self.one("1:20:00 - 1:24:00 مزاح - ممكن يبقى")["action"], "decide")
        self.assertEqual(self.one("2:01:00 2:05:00 كلام مفيد طويل")["category"], "فائدة")
        self.assertEqual(self.one("1:23 سعال", two_part="hm")["start"], 4980)
        self.assertEqual(self.one("1:23 سعال")["start"], 83)

    def test_ignored_lines_and_problems(self):
        rows, ignored = cutlist.parse_text("المجلس 04 - خالد\n0:08:00 عطسة\n3:00:00 مزح\n")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(ignored), 1)
        cutlist.check(rows, 3600)
        self.assertEqual(rows[0]["problem"], "")
        self.assertTrue(rows[1]["problem"])


def analysis(duration=600.0):
    pauses = [[t, t + 0.6] for t in range(5, int(duration), 10)]
    return {"duration": duration, "pauses": pauses, "speech_db": -20.0, "pause_threshold_db": -40.0,
            "floor_db": -60.0, "overview": [], "overview_step": 0.5}


class Items(unittest.TestCase):
    def test_merge_keeps_decisions(self):
        st = {"items": [], "list": {"text": "0:01:00 - 0:01:20 مزح\n0:03:00 عطسة", "remaining": False}}
        items.merge_list(st, analysis())
        self.assertEqual(len(st["items"]), 2)
        joke = next(i for i in st["items"] if i["label"] == "مزح")
        self.assertEqual(joke["start"], 60.0)           # no pause within reach: stays
        joke["status"], joke["edited"] = "approved", True
        st["list"]["text"] = "0:03:00 عطسة\n0:05:00 - 0:05:30 كلام جانبي"   # joke line removed
        items.merge_list(st, analysis())
        labels = sorted(i["label"] for i in st["items"])
        self.assertEqual(labels, ["عطسة", "كلام جانبي", "مزح"])
        self.assertTrue(next(i for i in st["items"] if i["label"] == "مزح")["orphan"])


class Assist(unittest.TestCase):
    def test_extract_json(self):
        txt = 'نعم:\n```json\n[{"id": "L1", "start": "0:01:00.5", "end": "0:01:10", "confidence": "high",},]\n```\nو\n```json\n[{"id": "L2", "status": "not_found"}]\n```'
        got = assist.extract_json(txt)
        self.assertEqual([g["id"] for g in got], ["L1", "L2"])
        with self.assertRaises(ValueError):
            assist.extract_json("لا شيء هنا")

    def test_apply(self):
        st = {"items": [], "list": {"text": "0:01:00 مزح\n0:02:00 - 0:02:10 كلام", "remaining": False}}
        an = analysis()
        items.merge_list(st, an)
        a, b = st["items"]
        b["edited"] = True
        props = [{"id": a["id"], "start": "0:00:55.1", "end": "0:01:05.4", "confidence": "high", "reason": "x"},
                 {"id": b["id"], "start": "0:02:00", "end": "0:02:20"},
                 {"id": "new-1", "label": "سكتة", "start": "0:04:00", "end": "0:04:03"},
                 {"id": "nope", "start": "1", "end": "2"}]
        rep = assist.apply_proposals(st, an, props)
        self.assertEqual(len(rep["applied"]), 2)
        self.assertEqual(rep["skipped_edited"], [b["id"]])
        self.assertEqual(len(rep["errors"]), 1)
        a2 = next(i for i in st["items"] if i["id"] == a["id"])
        self.assertEqual((a2["start"], a2["end"], a2["status"]), (55.12, 65.48, "proposed"))

    def test_format_window(self):
        words = [{"w": "بسم", "s": 1.0, "e": 1.4, "p": 0.9}, {"w": "الله", "s": 1.45, "e": 1.9, "p": 0.3},
                 {"w": "نكمل", "s": 5.0, "e": 5.5, "p": 0.9}]
        out = assist.format_window(words, analysis(), None, 0, 10)
        self.assertEqual(out[0], "[0:00:01.0–0:00:01.9] بسم الله؟")
        self.assertIn("فجوة 3.1", out[1])


class Render(unittest.TestCase):
    def test_render_cuts(self):
        from tanqih import config, render
        folder = Path(TMP) / "rec"
        folder.mkdir(exist_ok=True)
        src = folder / "lesson.wav"
        sr = 16000
        with wave.open(str(src), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(sr * 10)))
        st = {"duration": 10.0, "items": [
            {"id": "a", "status": "approved", "start": 2.0, "end": 3.5, "label": "x", "category": "y"},
            {"id": "b", "status": "approved", "start": 3.0, "end": 4.0, "label": "x", "category": "y"},
            {"id": "c", "status": "kept", "start": 6.0, "end": 7.0, "label": "x", "category": "y"}]}
        config.save_settings({"output_format": "wav"})
        res = render.render(src, st, lambda *a: None)
        with wave.open(res["file"]) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 8.0, places=2)
        self.assertEqual(res["cuts"], 1)
        self.assertTrue(Path(res["log"]).exists())


if __name__ == "__main__":
    unittest.main()


def tone_file(path, seconds=20, sr=16000, silent=()):
    """A test recording: a tone with optional silent ranges."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(sr * seconds):
            t = i / sr
            quiet = any(a <= t < b for a, b in silent)
            frames += struct.pack("<h", 0 if quiet else int(8000 * math.sin(i / 10)))
        w.writeframes(bytes(frames))


class Features(unittest.TestCase):
    def test_split_at_pauses(self):
        from tanqih.transcribe import split_at_pauses
        parts = split_at_pauses(0, 400, [[88, 89], [181, 183], [300, 300.5]])
        self.assertEqual(parts[0], [0, 88.5])
        self.assertEqual(parts[-1][1], 400)
        self.assertTrue(all(b - a < 140 for a, b in parts))

    def test_seam_repeat_and_in_word(self):
        import numpy as np
        from tanqih import seamcheck
        an = analysis(100.0)
        db = np.full(5000, -60.0, dtype=np.float32)
        db[int(20.0 / 0.02):int(20.6 / 0.02)] = -10   # speech across the start edge (20.3)
        txt = "وحكى ابن التين عن قوم الحمد لله وحكى ابن التين عن قوم أنه"
        ws, t = [], 10.0
        for w in txt.split():
            ws.append({"w": w, "s": t, "e": t + 0.4, "p": 0.9})
            t += 0.5 if w != "قوم" else 3.0
        tr = {"words": ws, "windows": [[0, 100]]}
        # cut only the sneeze words: the first reading stays -> repeated after the join
        st = {"items": [{"id": "a", "status": "approved", "start": 13.0, "end": 15.95, "label": "عطاس"}]}
        w = seamcheck.check(st, an, tr, db)
        rep = [x for x in w if x["kind"] == "repeat"]
        self.assertEqual(len(rep), 1)
        self.assertLess(rep[0]["fix"]["start"], 10.0)
        st = {"items": [{"id": "b", "status": "approved", "start": 20.3, "end": 30.0, "label": "x"}]}
        tr2 = {"words": [{"w": "كلمة", "s": 20.0, "e": 20.6, "p": 0.9}], "windows": [[0, 100]]}
        self.assertEqual([x["kind"] for x in seamcheck.check(st, an, tr2, db)], ["in_word"])

    def test_suggest(self):
        import numpy as np
        from tanqih import items as I
        an = analysis(600.0)
        an["pauses"].append([300.0, 309.0])
        an["pauses"].sort()
        db = np.full(30000, -40.0, dtype=np.float32)
        db[int(400 / 0.02):int(400.4 / 0.02)] = 5      # +25 dB above speech: a sneeze
        st = {"items": [], "duration": 600.0}
        I.suggest(st, an, db)
        cats = sorted(i["category"] for i in st["items"])
        self.assertEqual(cats, ["سكتة", "صوت مرتفع"])
        sil = next(i for i in st["items"] if i["category"] == "سكتة")
        self.assertEqual((sil["start"], sil["end"], sil["confidence"]), (300.35, 308.65, "high"))
        sil["status"] = "approved"
        I.suggest(st, an, db)          # running again neither duplicates nor drops the decision
        self.assertEqual(len(st["items"]), 2)

    def test_prepare_markers_benefits(self):
        from tanqih import config, render, worker
        folder = Path(TMP) / "rec2"
        folder.mkdir(exist_ok=True)
        src = folder / "درس 01.wav"
        tone_file(src, 20, silent=[(5, 11)])
        st = items.load_state(src)
        st["list"]["text"] = "0:00:14 - 0:00:16 فائدة جميلة\n0:00:02 - 0:00:03 مزح"
        items.save_state(src, st)
        config.save_settings({"output_format": "wav", "suggest_silence": 5})
        worker.prepare(src, transcribe=False)
        st = items.load_state(src)
        self.assertEqual(sorted(i["category"] for i in st["items"]), ["سكتة", "فائدة", "مزح"])
        for i in st["items"]:
            i["status"] = "approved" if i["category"] != "فائدة" else "kept"
        res = render.render(src, st, lambda *a: None)
        aud, edl = res["markers"]
        lines = Path(aud).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("2."))
        self.assertIn("|C:ResolveColorYellow |M:فائدة: فائدة جميلة |D:", Path(edl).read_text(encoding="utf-8"))
        with wave.open(res["benefits"]) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 2.8, delta=0.1)


@unittest.skipUnless(os.environ.get("TANQIH_TEST_WHISPER"), "set TANQIH_TEST_WHISPER=1 to load a speech model")
class Whisper(unittest.TestCase):
    def test_tiny_model_runs(self):
        from tanqih import config, transcribe
        folder = Path(TMP) / "rec3"
        folder.mkdir(exist_ok=True)
        src = folder / "tone.wav"
        tone_file(src, 8)
        config.save_settings({"whisper_model": "tiny"})
        words, segs = transcribe.transcribe_range(src, 0, 8, duration=8)
        self.assertIsInstance(words, list)
