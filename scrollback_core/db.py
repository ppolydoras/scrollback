"""SQLite storage. One connection per thread, WAL mode, a global write lock."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

_local = threading.local()
WRITE_LOCK = threading.RLock()
_DB_PATH: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY,
    source_key    TEXT UNIQUE NOT NULL,
    lang          TEXT NOT NULL,
    kind          TEXT NOT NULL,          -- event | birth | death
    featured      INTEGER NOT NULL DEFAULT 0,
    year          INTEGER NOT NULL,       -- negative for BC
    month         INTEGER NOT NULL,
    day           INTEGER NOT NULL,
    text          TEXT NOT NULL,          -- one-line summary from Wikipedia
    title         TEXT,                   -- main linked article
    description   TEXT,
    extract       TEXT,
    image         TEXT,
    url           TEXT,
    related       TEXT,                   -- JSON list of {title, url, description}
    era           TEXT NOT NULL,
    topics        TEXT NOT NULL DEFAULT '[]',
    story_title   TEXT,                   -- optional LLM rewrite
    story         TEXT,
    quiz          TEXT,                   -- optional LLM quiz (JSON)
    story_model   TEXT,
    enriched_at   TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_date ON facts(month, day);
CREATE INDEX IF NOT EXISTS facts_era ON facts(era);
CREATE INDEX IF NOT EXISTS facts_year ON facts(year);

CREATE TABLE IF NOT EXISTS interactions (
    fact_id    INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    seen_at    TEXT,
    seen_count INTEGER NOT NULL DEFAULT 0,
    dwell_ms   INTEGER NOT NULL DEFAULT 0,
    liked      INTEGER NOT NULL DEFAULT 0,
    liked_at   TEXT,
    saved      INTEGER NOT NULL DEFAULT 0,
    saved_at   TEXT,
    hidden     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS inter_saved ON interactions(saved, saved_at);

CREATE TABLE IF NOT EXISTS affinity (
    tag   TEXT PRIMARY KEY,
    score REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quiz_state (
    fact_id  INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    box      INTEGER NOT NULL DEFAULT 0,
    due_at   TEXT NOT NULL,
    correct  INTEGER NOT NULL DEFAULT 0,
    wrong    INTEGER NOT NULL DEFAULT 0,
    last_at  TEXT
);

CREATE TABLE IF NOT EXISTS daily (
    day          TEXT PRIMARY KEY,        -- local date, YYYY-MM-DD
    cards        INTEGER NOT NULL DEFAULT 0,
    quiz_total   INTEGER NOT NULL DEFAULT 0,
    quiz_correct INTEGER NOT NULL DEFAULT 0,
    xp           INTEGER NOT NULL DEFAULT 0,
    goal_met     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ingest_state (
    lang       TEXT NOT NULL,
    mmdd       TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    items      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lang, mmdd)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init(path: Path | None = None) -> None:
    global _DB_PATH
    _DB_PATH = Path(path) if path else config.db_path()
    conn = connect()
    with WRITE_LOCK:
        conn.executescript(SCHEMA)
        conn.commit()
    try:
        os.chmod(_DB_PATH, 0o600)
    except OSError:
        pass


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == _DB_PATH:
        return conn
    if _DB_PATH is None:
        raise RuntimeError("db.init() has not been called")
    conn = sqlite3.connect(str(_DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    _local.conn = conn
    _local.path = _DB_PATH
    return conn


def close_thread() -> None:
    """Close this thread's connection. Request threads call this when done."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        _local.conn = None
        conn.close()


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def today_str() -> str:
    return date.today().isoformat()


def query(sql: str, params=()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def query_one(sql: str, params=()):
    return connect().execute(sql, params).fetchone()


def execute(sql: str, params=()) -> sqlite3.Cursor:
    with WRITE_LOCK:
        conn = connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


# ---------------------------------------------------------------- settings

def get_setting(key: str) -> str:
    env = config.ENV_OVERRIDES.get(key)
    if env and os.environ.get(env):
        return os.environ[env]
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is not None:
        return row["value"]
    return config.DEFAULT_SETTINGS.get(key, "")


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def all_settings() -> dict:
    out = {}
    for key in config.DEFAULT_SETTINGS:
        out[key] = get_setting(key)
    out["_env_locked"] = [k for k, env in config.ENV_OVERRIDES.items() if os.environ.get(env)]
    return out


# ---------------------------------------------------------------- facts

FACT_COLUMNS = (
    "id, kind, featured, year, month, day, text, title, description, extract, image, url, "
    "related, era, topics, story_title, story, story_model, quiz IS NOT NULL AS has_quiz"
)


def fact_to_dict(row: sqlite3.Row, inter: sqlite3.Row | None = None) -> dict:
    d = {k: row[k] for k in row.keys() if k not in ("related", "topics", "has_quiz")}
    d["related"] = json.loads(row["related"] or "[]")
    d["topics"] = json.loads(row["topics"] or "[]")
    if inter is not None:
        d["liked"] = bool(inter["liked"])
        d["saved"] = bool(inter["saved"])
        d["hidden"] = bool(inter["hidden"])
        d["seen"] = inter["seen_at"] is not None
    else:
        d["liked"] = d["saved"] = d["hidden"] = d["seen"] = False
    return d


def get_fact(fact_id: int) -> dict | None:
    row = query_one(f"SELECT {FACT_COLUMNS} FROM facts WHERE id = ?", (fact_id,))
    if row is None:
        return None
    inter = query_one("SELECT * FROM interactions WHERE fact_id = ?", (fact_id,))
    return fact_to_dict(row, inter)


def get_facts(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = query(f"SELECT {FACT_COLUMNS} FROM facts WHERE id IN ({marks})", ids)
    inters = {
        r["fact_id"]: r
        for r in query(f"SELECT * FROM interactions WHERE fact_id IN ({marks})", ids)
    }
    by_id = {r["id"]: fact_to_dict(r, inters.get(r["id"])) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def ensure_interaction(fact_id: int) -> None:
    execute("INSERT OR IGNORE INTO interactions(fact_id) VALUES(?)", (fact_id,))


# ---------------------------------------------------------------- daily progress

def bump_daily(cards: int = 0, xp: int = 0, quiz_total: int = 0, quiz_correct: int = 0) -> dict:
    day = today_str()
    goal = get_int("daily_goal", 20)
    with WRITE_LOCK:
        conn = connect()
        conn.execute("INSERT OR IGNORE INTO daily(day) VALUES(?)", (day,))
        conn.execute(
            "UPDATE daily SET cards = cards + ?, xp = xp + ?, quiz_total = quiz_total + ?, "
            "quiz_correct = quiz_correct + ? WHERE day = ?",
            (cards, xp, quiz_total, quiz_correct, day),
        )
        conn.execute(
            "UPDATE daily SET goal_met = 1 WHERE day = ? AND cards >= ?", (day, goal)
        )
        conn.commit()
    return progress()


LEVELS = [
    (0, "Novice"), (100, "Scribe"), (350, "Chronicler"), (800, "Historian"),
    (1600, "Archivist"), (3000, "Sage"), (5500, "Oracle"),
]


def level_for(xp: int) -> dict:
    current = LEVELS[0]
    nxt = None
    for i, (threshold, name) in enumerate(LEVELS):
        if xp >= threshold:
            current = (threshold, name)
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    return {
        "name": current[1],
        "number": LEVELS.index(current) + 1,
        "floor": current[0],
        "next": nxt[0] if nxt else None,
        "next_name": nxt[1] if nxt else None,
    }


def streak() -> int:
    days = {r["day"] for r in query("SELECT day FROM daily WHERE goal_met = 1")}
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)  # today still in progress: count from yesterday
    count = 0
    while d.isoformat() in days:
        count += 1
        d -= timedelta(days=1)
    return count


def progress() -> dict:
    day = today_str()
    row = query_one("SELECT * FROM daily WHERE day = ?", (day,))
    total_xp = query_one("SELECT COALESCE(SUM(xp), 0) AS xp FROM daily")["xp"]
    return {
        "today": day,
        "cards": row["cards"] if row else 0,
        "goal": get_int("daily_goal", 20),
        "goal_met": bool(row["goal_met"]) if row else False,
        "xp_today": row["xp"] if row else 0,
        "xp": total_xp,
        "level": level_for(total_xp),
        "streak": streak(),
    }
