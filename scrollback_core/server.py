"""HTTP server: static files and a small JSON API. Binds to localhost unless told otherwise."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, db, quiz, tts
from .feed import Feed
from .llm import Storyteller, provider_config
from .tagging import ERA_NAMES, TOPIC_NAMES
from .wiki import Crawler

log = logging.getLogger("scrollback.server")

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


class App:
    def __init__(self, crawl: bool = True) -> None:
        self.feed = Feed()
        self.storyteller = Storyteller()
        self.crawler = Crawler()
        self.tts = tts.Engine()
        self.feed.on_upcoming = self.storyteller.enqueue
        self.crawl = crawl

    def start_workers(self) -> None:
        self.storyteller.start()
        if self.crawl:
            self.crawler.start()


# ------------------------------------------------------------------ queries

def library_status(app: App) -> dict:
    lang = db.get_setting("language") or "en"
    total = db.query_one("SELECT COUNT(*) AS n FROM facts WHERE lang = ?", (lang,))["n"]
    unseen = db.query_one(
        "SELECT COUNT(*) AS n FROM facts f LEFT JOIN interactions i ON i.fact_id = f.id "
        "WHERE f.lang = ? AND (i.fact_id IS NULL OR (i.seen_at IS NULL AND i.hidden = 0))", (lang,)
    )["n"]
    days = db.query_one("SELECT COUNT(*) AS n FROM ingest_state WHERE lang = ?", (lang,))["n"]
    llm = provider_config()
    return {
        "facts": total,
        "unseen": unseen,
        "days_fetched": days,
        "days_total": 366,
        "crawler": app.crawler.status if app.crawl else {"state": "disabled"},
        "storyteller": {
            "provider": llm["provider"],
            "model": llm.get("model"),
            "ready": llm["ready"],
            "reason": llm["reason"],
            **app.storyteller.status,
        },
        "progress": db.progress(),
    }


def stats() -> dict:
    lang = db.get_setting("language") or "en"
    read = db.query_one(
        "SELECT COALESCE(SUM(dwell_ms >= 1500),0) AS n, COALESCE(SUM(liked),0) AS liked, "
        "COALESCE(SUM(saved),0) AS saved, COALESCE(SUM(hidden),0) AS hidden, "
        "COALESCE(SUM(dwell_ms),0) AS dwell FROM interactions"
    )
    q = db.query_one(
        "SELECT COALESCE(SUM(correct),0) AS correct, COALESCE(SUM(wrong),0) AS wrong, "
        "COUNT(*) AS facts, COALESCE(SUM(box >= 3),0) AS learned FROM quiz_state"
    )
    eras = []
    for era in ERA_NAMES:
        row = db.query_one(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(i.dwell_ms >= 1500),0) AS read, COALESCE(SUM(i.liked),0) AS liked "
            "FROM facts f LEFT JOIN interactions i ON i.fact_id = f.id WHERE f.lang = ? AND f.era = ?",
            (lang, era),
        )
        eras.append({"era": era, "total": row["total"], "read": row["read"], "liked": row["liked"]})
    aff = db.query("SELECT tag, score FROM affinity WHERE tag LIKE 'topic:%' ORDER BY score DESC")
    topics = [{"topic": r["tag"][6:], "score": round(r["score"], 1)} for r in aff]
    days = []
    for k in range(13, -1, -1):
        d = (date.today() - timedelta(days=k)).isoformat()
        row = db.query_one("SELECT cards, goal_met FROM daily WHERE day = ?", (d,))
        days.append({"day": d, "cards": row["cards"] if row else 0, "goal_met": bool(row and row["goal_met"])})
    return {
        "read": read["n"], "liked": read["liked"], "saved": read["saved"], "hidden": read["hidden"],
        "minutes": round(read["dwell"] / 60000),
        "quiz": {"correct": q["correct"], "wrong": q["wrong"], "facts": q["facts"], "learned": q["learned"]},
        "eras": eras, "topics": topics, "days": days, "progress": db.progress(),
    }


LIST_COLUMNS = (
    "f.id, f.year, f.month, f.day, f.text, f.title, f.image, f.era, f.topics, f.kind, "
    "COALESCE(i.liked,0) AS liked, COALESCE(i.saved,0) AS saved, i.seen_at IS NOT NULL AS seen"
)


def _list_row(r) -> dict:
    d = dict(r)
    d["topics"] = json.loads(d["topics"] or "[]")
    d["liked"], d["saved"], d["seen"] = bool(d["liked"]), bool(d["saved"]), bool(d["seen"])
    return d


def explore(params: dict) -> dict:
    lang = db.get_setting("language") or "en"
    where = ["f.lang = ?", "COALESCE(i.hidden, 0) = 0", "(? = '1' OR f.kind = 'event')"]
    args: list = [lang, db.get_setting("include_people")]
    era = params.get("era")
    if era in ERA_NAMES:
        where.append("f.era = ?")
        args.append(era)
    topic = params.get("topic")
    if topic in TOPIC_NAMES:
        where.append("f.topics LIKE ?")
        args.append(f'%"{topic}"%')
    q = (params.get("q") or "").strip()
    if q:
        where.append("(f.text LIKE ? OR f.title LIKE ? OR f.extract LIKE ?)")
        args += [f"%{q}%"] * 3
    month, day = params.get("month", ""), params.get("day", "")
    if month.isdigit() and 1 <= int(month) <= 12:
        where.append("f.month = ?")
        args.append(int(month))
    if day.isdigit() and 1 <= int(day) <= 31:
        where.append("f.day = ?")
        args.append(int(day))
    if params.get("unseen") == "1":
        where.append("i.seen_at IS NULL")
    order = {"chrono": "f.year ASC, f.month, f.day", "recent": "f.year DESC, f.month, f.day"}.get(
        params.get("sort", "chrono"), "random()"
    )
    offset = max(0, int(params.get("offset", 0) or 0))
    limit = 30
    sql_where = " AND ".join(where)
    total = db.query_one(
        f"SELECT COUNT(*) AS n FROM facts f LEFT JOIN interactions i ON i.fact_id = f.id WHERE {sql_where}", args
    )["n"]
    rows = db.query(
        f"SELECT {LIST_COLUMNS} FROM facts f LEFT JOIN interactions i ON i.fact_id = f.id "
        f"WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
        args + [limit, offset],
    )
    return {"total": total, "offset": offset, "items": [_list_row(r) for r in rows]}


def collection(which: str, offset: int) -> dict:
    col = "saved" if which == "saved" else "liked"
    rows = db.query(
        f"SELECT {LIST_COLUMNS} FROM facts f JOIN interactions i ON i.fact_id = f.id "
        f"WHERE i.{col} = 1 ORDER BY i.{col}_at DESC LIMIT 50 OFFSET ?",
        (max(0, offset),),
    )
    total = db.query_one(f"SELECT COUNT(*) AS n FROM interactions WHERE {col} = 1")["n"]
    return {"total": total, "items": [_list_row(r) for r in rows]}


# ------------------------------------------------------------------ HTTP

ALLOWED_HOSTS = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")


def extra_hosts() -> set[str]:
    """Host names allowed besides localhost, from SCROLLBACK_ALLOWED_HOSTS
    (comma separated, * for any). For a home server reached by its own name."""
    raw = os.environ.get("SCROLLBACK_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def host_allowed(host: str) -> bool:
    if ALLOWED_HOSTS.match(host):
        return True
    extra = extra_hosts()
    if "*" in extra:
        return True
    name = re.sub(r":\d+$", "", host.strip().lower())
    return bool(name) and name in extra


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by serve()
    server_version = "Scrollback"

    def finish(self):
        try:
            super().finish()
        finally:
            db.close_thread()

    def log_message(self, fmt, *args):  # quieter console
        log.debug("%s %s", self.address_string(), fmt % args)

    # -------------------------------------------------------------- helpers

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        return host_allowed(self.headers.get("Host", ""))

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _static(self, rel: str) -> None:
        rel = rel.lstrip("/") or "index.html"
        path = (config.STATIC_DIR / rel).resolve()
        if config.STATIC_DIR.resolve() not in path.parents or not path.is_file():
            self._error(404, "not found")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        cache = "public, max-age=31536000, immutable" if rel.startswith("fonts/") else "no-cache"
        self._send(200, path.read_bytes(), ctype, {"Cache-Control": cache})

    def _audio(self, path) -> None:
        """Serve a WAV file, with byte ranges so the browser can seek."""
        data = path.read_bytes()
        size = len(data)
        rng = self.headers.get("Range", "")
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start, end = max(0, size - int(m.group(2))), size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self._send(416, b"", "audio/wav", {"Content-Range": f"bytes */{size}"})
                return
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._send(206, data[start:end + 1], "audio/wav", headers)
        else:
            self._send(200, data, "audio/wav", headers)

    # -------------------------------------------------------------- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            self._error(403, "forbidden host")
            return
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        try:
            if p == "/" or p == "/index.html":
                self._static("index.html")
            elif p.startswith("/static/"):
                self._static(p[len("/static/"):])
            elif p == "/api/feed":
                self._json({"items": self.app.feed.next_items(int(params.get("n", 8))),
                            "status": library_status(self.app)})
            elif p == "/api/status":
                self._json(library_status(self.app))
            elif re.fullmatch(r"/api/fact/\d+", p):
                fact = db.get_fact(int(p.rsplit("/", 1)[1]))
                self._json(fact) if fact else self._error(404, "no such post")
            elif p == "/api/explore":
                self._json(explore(params))
            elif p == "/api/collection":
                self._json(collection(params.get("which", "saved"), int(params.get("offset", 0) or 0)))
            elif p == "/api/stats":
                self._json(stats())
            elif p == "/api/settings":
                self._json({"settings": db.all_settings(), "eras": ERA_NAMES, "topics": TOPIC_NAMES})
            elif p == "/api/meta":
                self._json({"eras": ERA_NAMES, "topics": TOPIC_NAMES})
            elif p == "/api/tts/status":
                self._json(self.app.tts.status())
            elif p == "/api/tts/catalog":
                self._json({"voices": self.app.tts.catalog()})
            elif p == "/api/tts/sample.wav":
                try:
                    path = self.app.tts.synthesize(
                        "This is how Scrollback sounds with this voice. On the fourteenth of October, ten sixty-six, "
                        "the Normans defeated the English at the Battle of Hastings.",
                        voice=params.get("voice") or None,
                        speed=float(params["speed"]) if params.get("speed") else None)
                except RuntimeError as err:
                    self._error(503, str(err))
                    return
                self._audio(path)
            elif re.fullmatch(r"/api/tts/fact/\d+\.wav", p):
                try:
                    path = self.app.tts.audio_for_fact(int(p.rsplit("/", 1)[1][:-4]))
                except RuntimeError as err:
                    self._error(503, str(err))
                    return
                self._audio(path)
            else:
                self._error(404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser stopped loading, for example audio skipped
        except (ValueError, KeyError) as err:
            self._error(400, str(err))
        except Exception as err:  # keep the server alive
            log.exception("GET %s failed", p)
            self._error(500, f"internal error: {err}")

    def do_POST(self):
        # A custom header forces a CORS preflight, which this server never
        # answers, so other websites cannot post to it from your browser.
        if not self._host_ok() or self.headers.get("X-Scrollback") != "1":
            self._error(403, "forbidden")
            return
        p = urlparse(self.path).path
        try:
            body = self._body()
            if p == "/api/view":
                self._json(self.app.feed.record_view(int(body["id"]), int(body.get("ms", 0))))
            elif p == "/api/react":
                self._json(self.app.feed.react(int(body["id"]), str(body["action"])))
            elif p == "/api/quiz":
                other = body.get("other_id")
                self._json(quiz.answer(str(body["kind"]), int(body["fact_id"]), body["choice"],
                                       int(other) if other is not None else None))
            elif p == "/api/settings":
                self._save_settings(body)
            elif p == "/api/refresh":
                self.app.crawler.nudge()
                self._json({"ok": True})
            elif p == "/api/tts/prefetch":
                self.app.tts.prefetch([int(i) for i in body.get("ids", [])])
                self._json({"ok": True})
            elif p == "/api/tts/download":
                self.app.tts.start_download(str(body["voice"]))
                self._json(self.app.tts.status())
            elif p == "/api/tts/remove":
                tts.remove_voice(str(body["voice"]))
                if db.get_setting("tts_voice") == body["voice"]:
                    db.set_setting("tts_voice", "")
                self._json(self.app.tts.status())
            elif p == "/api/tts/recheck":
                self.app.tts.find_python(force=True)
                self._json(self.app.tts.status())
            else:
                self._error(404, "not found")
        except (ValueError, KeyError, TypeError) as err:
            self._error(400, str(err))
        except Exception as err:
            log.exception("POST %s failed", p)
            self._error(500, f"internal error: {err}")

    def _save_settings(self, body: dict) -> None:
        allowed = set(config.DEFAULT_SETTINGS)
        changed = []
        for key, value in body.items():
            if key not in allowed:
                continue
            value = str(value).strip()
            if key in ("daily_goal", "quiz_every"):
                n = int(value)
                if not (1 <= n <= 500):
                    raise ValueError(f"{key} out of range")
                value = str(n)
            if key == "language" and not re.fullmatch(r"[a-z]{2,3}(-[a-z]+)?", value):
                raise ValueError("language must be a Wikipedia code such as en or de")
            if key == "llm_provider" and value not in ("none", "anthropic", "openai"):
                raise ValueError("provider must be none, anthropic or openai")
            if key == "tts_speed":
                value = f"{min(1.6, max(0.6, float(value))):.2f}"
            if key == "tts_voice" and value and not tts.VOICE_RX.match(value):
                raise ValueError("voice names look like en_GB-cori-medium")
            if db.get_setting(key) != value:
                db.set_setting(key, value)
                changed.append(key)
        if "include_people" in changed and db.get_setting("include_people") == "1":
            db.execute("DELETE FROM ingest_state")  # fetch every day again, with people
        if {"language", "include_people"} & set(changed):
            self.app.feed.reset()
            self.app.crawler.nudge()
        self._json({"settings": db.all_settings(), "changed": changed})


def serve(host: str, port: int, app: App) -> ThreadingHTTPServer:
    Handler.app = app
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
