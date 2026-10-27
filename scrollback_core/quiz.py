"""Quizzes on posts you have already read, scheduled with a Leitner box system."""
from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, timedelta

from . import db
from .tagging import format_year

# days until the next review after a correct answer, by box
BOX_DAYS = [1, 3, 7, 16, 35, 90]
RETRY_MINUTES = 20


def _due(box: int) -> str:
    return (datetime.now() + timedelta(days=BOX_DAYS[box])).replace(microsecond=0).isoformat()


def year_options(year: int, rng: random.Random | None = None) -> list[int]:
    rng = rng or random.Random()
    if year >= 1900:
        span = 15
    elif year >= 1700:
        span = 30
    elif year >= 1400:
        span = 60
    elif year >= 0:
        span = 150
    else:
        span = 300
    min_gap = max(2, span // 7)
    max_year = date.today().year
    opts = {year}
    for _ in range(400):
        if len(opts) == 4:
            break
        y = year + rng.choice((-1, 1)) * rng.randint(min_gap, span)
        if y == 0 or y > max_year:
            continue
        if any(abs(y - o) < min_gap for o in opts):
            continue
        opts.add(y)
    step = 1
    while len(opts) < 4:  # fallback for crowded ranges
        y = year - step * min_gap
        if y != 0 and y not in opts:
            opts.add(y)
        step += 1
    return sorted(opts)


def _year_in_text(year: int, text: str) -> bool:
    return bool(re.search(rf"\b{abs(year)}\b", text))


def _pick_fact(exclude: set[int]):
    now = db.now_iso()
    lang = db.get_setting("language") or "en"
    ex = ",".join(str(int(i)) for i in exclude) or "0"
    row = db.query_one(
        f"SELECT f.* FROM quiz_state q JOIN facts f ON f.id = q.fact_id "
        f"WHERE q.due_at <= ? AND f.lang = ? AND f.id NOT IN ({ex}) ORDER BY q.due_at LIMIT 1",
        (now, lang),
    )
    if row is not None:
        return row
    cutoff = (datetime.now() - timedelta(minutes=3)).replace(microsecond=0).isoformat()
    return db.query_one(
        f"SELECT f.* FROM facts f JOIN interactions i ON i.fact_id = f.id "
        f"LEFT JOIN quiz_state q ON q.fact_id = f.id "
        f"WHERE q.fact_id IS NULL AND i.seen_at IS NOT NULL AND i.seen_at <= ? AND i.hidden = 0 "
        f"AND i.dwell_ms >= 2500 AND f.lang = ? AND f.id NOT IN ({ex}) "
        f"ORDER BY (i.liked * 2 + i.saved * 3 + MIN(i.dwell_ms / 10000.0, 2)) DESC, random() LIMIT 1",
        (cutoff, lang),
    )


def make_quiz(exclude: set[int] | None = None) -> dict | None:
    fact = _pick_fact(exclude or set())
    if fact is None:
        return None
    base = {
        "fact_id": fact["id"],
        "image": fact["image"],
        "era": fact["era"],
    }
    llm_quiz = json.loads(fact["quiz"]) if fact["quiz"] else None
    if llm_quiz and random.random() < 0.5:
        return {**base, "kind": "mcq", "question": llm_quiz["question"], "options": llm_quiz["options"]}

    if not _year_in_text(fact["year"], fact["text"]):
        opts = year_options(fact["year"])
        return {
            **base,
            "kind": "year",
            "question": "When did this happen?",
            "prompt": fact["text"],
            "options": opts,
            "labels": [format_year(y) for y in opts],
        }

    # The headline gives the year away, so ask which of two events came first.
    other = db.query_one(
        "SELECT f.id, f.text, f.year FROM facts f JOIN interactions i ON i.fact_id = f.id "
        "WHERE i.seen_at IS NOT NULL AND f.id != ? AND ABS(f.year - ?) >= 5 "
        "ORDER BY random() LIMIT 1",
        (fact["id"], fact["year"]),
    )
    if other is None:
        return None
    pair = [{"id": fact["id"], "text": fact["text"]}, {"id": other["id"], "text": other["text"]}]
    random.shuffle(pair)
    return {**base, "kind": "order", "question": "Which happened first?", "pair": pair}


def answer(kind: str, fact_id: int, choice, other_id: int | None = None) -> dict:
    fact = db.query_one("SELECT * FROM facts WHERE id = ?", (fact_id,))
    if fact is None:
        raise ValueError("unknown fact")
    if kind == "year":
        correct = int(choice) == fact["year"]
        reveal = {"answer": fact["year"], "label": format_year(fact["year"])}
    elif kind == "order":
        other = db.query_one("SELECT id, year FROM facts WHERE id = ?", (other_id,))
        if other is None:
            raise ValueError("unknown fact")
        first = fact if fact["year"] < other["year"] else other
        correct = int(choice) == first["id"]
        reveal = {
            "answer": first["id"],
            "years": {str(fact["id"]): format_year(fact["year"]), str(other["id"]): format_year(other["year"])},
        }
    elif kind == "mcq":
        q = json.loads(fact["quiz"] or "null")
        if not q:
            raise ValueError("no quiz for this fact")
        correct = int(choice) == int(q["answer"])
        reveal = {"answer": int(q["answer"])}
    else:
        raise ValueError(f"unknown quiz kind {kind}")

    state = db.query_one("SELECT * FROM quiz_state WHERE fact_id = ?", (fact_id,))
    box = state["box"] if state else -1
    if correct:
        box = min(box + 1, len(BOX_DAYS) - 1)
        due = _due(box)
    else:
        box = 0
        due = (datetime.now() + timedelta(minutes=RETRY_MINUTES)).replace(microsecond=0).isoformat()
    db.execute(
        "INSERT INTO quiz_state(fact_id, box, due_at, correct, wrong, last_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(fact_id) DO UPDATE SET box = excluded.box, due_at = excluded.due_at, "
        "correct = correct + excluded.correct, wrong = wrong + excluded.wrong, last_at = excluded.last_at",
        (fact_id, box, due, int(correct), int(not correct), db.now_iso()),
    )
    progress = db.bump_daily(xp=10 if correct else 2, quiz_total=1, quiz_correct=int(correct))
    return {
        "correct": correct,
        **reveal,
        "xp": 10 if correct else 2,
        "fact": db.get_fact(fact_id),
        "progress": progress,
    }
