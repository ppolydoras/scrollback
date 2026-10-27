"""Offline tests. Run from the project folder: python3 -m unittest discover tests"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import mock_wiki  # noqa: E402

MOCK_PORT = 18999
APP_PORT = 18765


def start_mock():
    mock_wiki.H.port = MOCK_PORT
    srv = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), mock_wiki.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["SCROLLBACK_DATA_DIR"] = cls.tmp.name
        os.environ["SCROLLBACK_WIKI_BASE"] = f"http://127.0.0.1:{MOCK_PORT}"
        os.environ["SCROLLBACK_CRAWL_DELAY"] = "0"
        # text to speech runs against a stand-in Piper module
        os.environ["SCROLLBACK_PIPER_PYTHON"] = sys.executable
        os.environ["PYTHONPATH"] = str(ROOT / "tests" / "fake_piper")
        voices = Path(cls.tmp.name) / "voices"
        voices.mkdir(parents=True, exist_ok=True)
        (voices / "en_GB-fake-medium.onnx").write_bytes(b"fake")
        (voices / "en_GB-fake-medium.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 16000}}))
        cls.mock = start_mock()
        from scrollback_core import db
        from scrollback_core.server import App, serve
        db.init()
        cls.app = App(crawl=True)
        cls.app.start_workers()
        cls.httpd = serve("127.0.0.1", APP_PORT, cls.app)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        # wait for the crawler to finish all 366 days
        for _ in range(200):
            if cls.app.crawler.status["state"] == "complete":
                break
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.app.tts.shutdown()
        cls.httpd.shutdown()
        cls.mock.shutdown()

    # ---------------------------------------------------------------- helpers

    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{APP_PORT}{path}")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def post(self, path, body, header=True):
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-Scrollback"] = "1"
        req = urllib.request.Request(f"http://127.0.0.1:{APP_PORT}{path}", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    # ---------------------------------------------------------------- tests

    def test_01_ingest(self):
        st = self.get("/api/status")
        self.assertEqual(st["days_fetched"], 366)
        self.assertEqual(st["facts"], len(mock_wiki.EVENTS))

    def test_02_main_page_skips_places(self):
        from scrollback_core import db
        row = db.query_one("SELECT * FROM facts WHERE year = 1881")
        self.assertEqual(row["title"], "Gunfight at the O.K. Corral")
        self.assertEqual(row["era"], "19th century")
        self.assertTrue(row["image"].endswith(".jpg"))
        row = db.query_one("SELECT * FROM facts WHERE year = -44")
        self.assertEqual(row["era"], "Ancient")
        self.assertIn("Politics and power", json.loads(row["topics"]) + ["Politics and power"])

    def test_03_feed_and_view(self):
        data = self.get("/api/feed?n=6")
        facts = [i["fact"] for i in data["items"] if i["type"] == "fact"]
        self.assertEqual(len(facts), 6)
        ids = [f["id"] for f in facts]
        self.assertEqual(len(set(ids)), 6)
        prog = self.post("/api/view", {"id": ids[0], "ms": 9000})
        self.assertEqual(prog["cards"], 1)
        self.post("/api/view", {"id": ids[0], "ms": 9000})
        self.assertEqual(self.get("/api/status")["progress"]["cards"], 1, "a post counts once")
        more = self.get("/api/feed?n=6")
        more_ids = [i["fact"]["id"] for i in more["items"] if i["type"] == "fact"]
        self.assertFalse(set(ids) & set(more_ids), "served posts are not repeated")

    def test_04_reactions_and_collections(self):
        fid = self.get("/api/feed?n=1")["items"][0]["fact"]["id"]
        f = self.post("/api/react", {"id": fid, "action": "like"})
        self.assertTrue(f["liked"])
        f = self.post("/api/react", {"id": fid, "action": "save"})
        self.assertTrue(f["saved"])
        saved = self.get("/api/collection?which=saved")
        self.assertEqual(saved["items"][0]["id"], fid)
        from scrollback_core import feed
        self.assertGreater(feed.affinities().get(f"era:{f['era']}", 0), 0)

    def test_05_csrf_header_required(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/react", {"id": 1, "action": "like"}, header=False)
        self.assertEqual(ctx.exception.code, 403)

    def test_06_quiz_year_options(self):
        from scrollback_core.quiz import year_options
        import random
        rng = random.Random(4)
        for year in (-490, -44, 79, 1066, 1453, 1789, 1912, 2019, date.today().year):
            opts = year_options(year, rng)
            self.assertEqual(len(opts), 4)
            self.assertIn(year, opts)
            self.assertNotIn(0, opts)
            self.assertTrue(all(o <= date.today().year for o in opts))

    def test_07_quiz_answer(self):
        from scrollback_core import db, quiz
        row = db.query_one("SELECT id, year FROM facts WHERE year = 1066")
        db.ensure_interaction(row["id"])
        res = quiz.answer("year", row["id"], 1066)
        self.assertTrue(res["correct"])
        state = db.query_one("SELECT * FROM quiz_state WHERE fact_id = ?", (row["id"],))
        self.assertEqual(state["box"], 0)
        res = quiz.answer("year", row["id"], 1067)
        self.assertFalse(res["correct"])
        self.assertEqual(res["label"], "1066")

    def test_08_quiz_card_in_feed(self):
        from scrollback_core import db
        # make several posts eligible: seen long enough ago with real dwell
        db.execute("UPDATE interactions SET seen_at = '2020-01-01T00:00:00', dwell_ms = 9000 WHERE seen_at IS NOT NULL")
        for r in db.query("SELECT id FROM facts LIMIT 5"):
            db.ensure_interaction(r["id"])
            db.execute("UPDATE interactions SET seen_at = '2020-01-01T00:00:00', dwell_ms = 9000 WHERE fact_id = ?", (r["id"],))
        self.app.feed.since_quiz = 100
        items = self.get("/api/feed?n=2")["items"]
        kinds = [i["type"] for i in items]
        self.assertIn("quiz", kinds)
        q = next(i["quiz"] for i in items if i["type"] == "quiz")
        self.assertNotIn("answer", q)

    def test_09_explore_and_stats(self):
        ex = self.get("/api/explore?era=Ancient&sort=chrono")
        self.assertEqual([i["year"] for i in ex["items"]], [-44, -31])
        ex = self.get("/api/explore?q=Bastille")
        self.assertEqual(ex["total"], 1)
        st = self.get("/api/stats")
        self.assertEqual(len(st["days"]), 14)
        self.assertEqual(len(st["eras"]), 7)

    def test_10_llm_parse(self):
        from scrollback_core.llm import parse_output
        good = json.dumps({
            "title": "A thirty-second shootout",
            "story": "On a cold afternoon in Tombstone, lawmen led by Virgil Earp faced members of the outlaw group known as the Cowboys. "
                     "The fight lasted about thirty seconds and became the best known shootout of the Old West.",
            "quiz": {"question": "Who led the lawmen?", "options": ["Virgil Earp", "A", "B", "C"], "answer": 0},
            "topics": ["War and conflict", "Nonsense"],
        })
        out = parse_output("Here you go:\n" + good)
        self.assertEqual(out["quiz"]["answer"], 0)
        self.assertEqual(out["topics"], ["War and conflict"])
        self.assertIsNone(parse_output("not json"))

    def test_11_storyteller_end_to_end(self):
        from scrollback_core import db, llm
        for provider, base in (("openai", f"http://127.0.0.1:{MOCK_PORT}/v1"), ("anthropic", f"http://127.0.0.1:{MOCK_PORT}")):
            os.environ["SCROLLBACK_LLM_PROVIDER"] = provider
            os.environ["SCROLLBACK_LLM_BASE_URL"] = base
            os.environ["SCROLLBACK_LLM_MODEL"] = "mock-model"
            os.environ["SCROLLBACK_LLM_API_KEY"] = "test-key"
            try:
                row = db.query_one("SELECT id FROM facts WHERE story IS NULL LIMIT 1")
                self.app.storyteller.enqueue([row["id"]])
                for _ in range(50):
                    fact = db.get_fact(row["id"])
                    if fact["story"]:
                        break
                    time.sleep(0.1)
                self.assertTrue(fact["story"], provider)
                self.assertEqual(fact["story_model"], "mock-model")
                self.assertEqual(fact["topics"], ["Politics and power"])
                res = __import__("scrollback_core.quiz", fromlist=["answer"]).answer("mcq", row["id"], 0)
                self.assertTrue(res["correct"])
            finally:
                for k in ("SCROLLBACK_LLM_PROVIDER", "SCROLLBACK_LLM_BASE_URL", "SCROLLBACK_LLM_MODEL", "SCROLLBACK_LLM_API_KEY"):
                    os.environ.pop(k, None)

    def test_12_speech_text(self):
        from scrollback_core.speech import speech_text, year_words
        self.assertEqual(year_words(1805), "eighteen oh five")
        self.assertEqual(year_words(-44), "forty-four B.C.")
        self.assertEqual(year_words(2026), "twenty twenty-six")
        text = speech_text({"year": 1066, "month": 10, "day": 14, "kind": "event",
                            "text": "The Normans win at Hastings.",
                            "extract": "It was fought on 14 October 1066 (Old English: ᚻ; x)."})
        self.assertTrue(text.startswith("The fourteenth of October, ten sixty-six."))
        self.assertIn("the fourteenth of October, ten sixty-six", text.split("\n\n")[-1])
        self.assertNotIn("ᚻ", text)

    def test_13_tts_audio(self):
        st = self.get("/api/tts/status")
        self.assertTrue(st["available"], st)
        self.assertEqual(st["voice"], "en_GB-fake-medium")
        fid = self.get("/api/feed?n=1")["items"][0]["fact"]["id"]
        url = f"http://127.0.0.1:{APP_PORT}/api/tts/fact/{fid}.wav"
        with urllib.request.urlopen(url) as r:
            self.assertEqual(r.headers["Content-Type"], "audio/wav")
            body = r.read()
        self.assertEqual(body[:4], b"RIFF")
        req = urllib.request.Request(url, headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(len(r.read()), 100)
        # slower speed gives a different, longer file
        self.post("/api/settings", {"tts_speed": "0.8"})
        with urllib.request.urlopen(url) as r:
            self.assertGreater(len(r.read()), len(body))
        self.post("/api/settings", {"tts_speed": "1.0"})
        self.post("/api/tts/prefetch", {"ids": [fid]})
        self.assertTrue(self.get("/api/tts/status")["worker_running"])

    def test_14_tts_errors(self):
        from scrollback_core import tts
        with self.assertRaises(RuntimeError):
            self.app.tts.synthesize("Please FAIL now.")
        self.assertIn("asked to fail", self.app.tts.status()["last_error"])
        # the worker keeps working after a failed request
        self.assertTrue(self.app.tts.synthesize("Back to normal.").exists())
        with self.assertRaises(ValueError):
            self.app.tts.start_download("../../etc/passwd")
        self.assertEqual(tts.voice_urls("en_GB-cori-medium")[0],
                         "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/cori/medium/"
                         "en_GB-cori-medium.onnx?download=true")

    def test_15_moves_old_annals_install(self):
        import subprocess
        home = Path(self.tmp.name) / "legacy-home"
        old = home / "share" / "annals"
        (old / "voices").mkdir(parents=True)
        (old / "annals.db").write_bytes(b"db")
        (old / "voices" / "en_GB-x-medium.onnx").write_bytes(b"v")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SCROLLBACK_", "ANNALS_"))}
        env.update({"XDG_DATA_HOME": str(home / "share"), "ANNALS_LLM_MODEL": "old-name-model"})
        code = ("from scrollback_core import config; import os; "
                "print(config.db_path()); print(os.environ.get('SCROLLBACK_LLM_MODEL'))")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                             capture_output=True, text=True, check=True).stdout.split()
        new = home / "share" / "scrollback"
        self.assertEqual(out[0], str(new / "scrollback.db"))
        self.assertEqual(out[1], "old-name-model")
        self.assertFalse(old.exists())
        self.assertEqual((new / "scrollback.db").read_bytes(), b"db")
        self.assertTrue((new / "voices" / "en_GB-x-medium.onnx").exists())

    def test_16_static(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/") as r:
            self.assertIn(b"<title>", r.read())

    def test_16b_allowed_hosts(self):
        from scrollback_core.server import host_allowed
        old = os.environ.pop("SCROLLBACK_ALLOWED_HOSTS", None)
        try:
            self.assertTrue(host_allowed("localhost:8765"))
            self.assertTrue(host_allowed("[::1]:8765"))
            self.assertFalse(host_allowed("homeserver.local:8765"))
            os.environ["SCROLLBACK_ALLOWED_HOSTS"] = "HomeServer.local, pi"
            self.assertTrue(host_allowed("homeserver.local:8765"))
            self.assertTrue(host_allowed("pi"))
            self.assertFalse(host_allowed("evil.example"))
            self.assertFalse(host_allowed(""))
            os.environ["SCROLLBACK_ALLOWED_HOSTS"] = "*"
            self.assertTrue(host_allowed("anything:1"))
        finally:
            os.environ.pop("SCROLLBACK_ALLOWED_HOSTS", None)
            if old is not None:
                os.environ["SCROLLBACK_ALLOWED_HOSTS"] = old

    def test_17_demo_build(self):
        import subprocess
        out = Path(self.tmp.name) / "site"
        code = ("import sys; from scrollback_core import demo_build; "
                "print(demo_build.build_all_days(sys.argv[1], url='https://example.org/demo'))")
        res = subprocess.run([sys.executable, "-c", code, str(out)], cwd=str(ROOT), env=os.environ.copy(),
                             capture_output=True, text=True, check=True)
        count = int(res.stdout.strip())
        self.assertGreater(count, 20)
        html = (out / "index.html").read_text()
        self.assertLess(html.index("static/demo.js"), html.index("static/app.js"))
        self.assertIn('content="https://example.org/demo/social-preview.png"', html)
        self.assertNotIn('"/static/', html)
        data = json.loads((out / "static" / "demo-data.json").read_text())
        self.assertEqual(len(data["facts"]), count)
        years = [f["year"] for f in data["facts"]]
        self.assertEqual(years, sorted(years))
        fact = next(f for f in data["facts"] if f["year"] == 1805)
        self.assertIn("eighteen oh five", fact["speech"])
        for name in ("app.js", "app.css", "demo.js", "icon.svg"):
            self.assertTrue((out / "static" / name).is_file())
        self.assertTrue(any((out / "static" / "fonts").glob("*.woff2")))

    def test_18_demo_select_balances_eras(self):
        from scrollback_core import demo_build
        items = []
        for era, year in (("Ancient", -200), ("Medieval", 1200), ("21st century", 2010)):
            for k in range(40):
                items.append({"source_key": f"{era}{k}", "extract": "x" * 200, "featured": 0, "image": None,
                              "era": era, "year": year, "month": 1, "day": 2 + k % 20})
        chosen = demo_build.select(items, near_days=set(), total=30, seed=1)
        self.assertEqual(len(chosen), 30)
        for era in ("Ancient", "Medieval", "21st century"):
            self.assertEqual(sum(1 for c in chosen if c["era"] == era), 10)


if __name__ == "__main__":
    unittest.main()
