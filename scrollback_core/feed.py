"""Personalised feed: candidate sampling, scoring, reactions and learned affinity."""
from __future__ import annotations

import json
import math
import random
import threading
import time
from collections import deque
from datetime import date
from typing import Callable

from . import db, quiz

TAG_LIMIT = 30.0

REACTION_WEIGHTS = {
    "like": 3.0, "unlike": -3.0,
    "save": 4.0, "unsave": -4.0,
    "hide": -4.0, "unhide": 4.0,
    "expand": 1.0, "open": 1.0,
}


def tags_of(era: str, topics: list[str], kind: str) -> list[str]:
    return [f"era:{era}", f"kind:{kind}"] + [f"topic:{t}" for t in topics]


def adjust_affinity(tags: list[str], delta: float) -> None:
    if not delta:
        return
    with db.WRITE_LOCK:
        conn = db.connect()
        for tag in tags:
            conn.execute(
                "INSERT INTO affinity(tag, score) VALUES(?, ?) ON CONFLICT(tag) DO UPDATE SET "
                "score = MAX(?, MIN(?, score + excluded.score))",
                (tag, delta, -TAG_LIMIT, TAG_LIMIT),
            )
        conn.commit()


def affinities() -> dict[str, float]:
    return {r["tag"]: r["score"] for r in db.query("SELECT tag, score FROM affinity")}


def _fact_tags(fact_id: int) -> list[str]:
    row = db.query_one("SELECT era, topics, kind FROM facts WHERE id = ?", (fact_id,))
    if row is None:
        return []
    return tags_of(row["era"], json.loads(row["topics"] or "[]"), row["kind"])


class Feed:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: deque[int] = deque()
        self.served: dict[int, float] = {}
        self.since_quiz = 0
        self.on_upcoming: Callable[[list[int]], None] | None = None

    # -------------------------------------------------------------- selection

    def _candidates(self) -> list:
        lang = db.get_setting("language") or "en"
        today = date.today()
        base = (
            "SELECT f.id, f.year, f.month, f.day, f.era, f.topics, f.kind, f.featured, "
            "f.image IS NOT NULL AS has_image, length(COALESCE(f.extract, '')) AS elen "
            "FROM facts f LEFT JOIN interactions i ON i.fact_id = f.id "
            "WHERE f.lang = ? AND (? = '1' OR f.kind = 'event') "
            "AND (i.fact_id IS NULL OR (i.seen_at IS NULL AND i.hidden = 0)) "
        )
        people = db.get_setting("include_people")
        rows = db.query(base + "ORDER BY random() LIMIT 400", (lang, people))
        rows += db.query(
            base + "AND f.month = ? AND f.day = ? ORDER BY random() LIMIT 60",
            (lang, people, today.month, today.day),
        )
        recent = time.time() - 6 * 3600
        self.served = {k: t for k, t in self.served.items() if t > recent}
        seen_ids: set[int] = set()
        out = []
        for r in rows:
            if r["id"] in seen_ids or self.served.get(r["id"], 0) > recent or r["id"] in self.queue:
                continue
            seen_ids.add(r["id"])
            out.append(r)
        return out

    def _score(self, row, aff: dict[str, float], today: date) -> float:
        def t(tag: str) -> float:
            return math.tanh(aff.get(tag, 0.0) / 8.0)

        topics = json.loads(row["topics"] or "[]")
        s = 0.6 * row["featured"] + 0.4 * row["has_image"] + (0.25 if row["elen"] > 300 else 0)
        if row["month"] == today.month and row["day"] == today.day:
            s += 1.2
        s += 1.4 * t(f"era:{row['era']}") + 0.5 * t(f"kind:{row['kind']}")
        if topics:
            s += 1.0 * sum(t(f"topic:{x}") for x in topics) / len(topics)
        return s + random.gauss(0, 0.55)

    def _refill(self, target: int) -> None:
        if len(self.queue) >= target:
            return
        rows = self._candidates()
        if not rows:
            return
        aff = affinities()
        today = date.today()
        scored = [(self._score(r, aff, today), r) for r in rows]
        picked = []
        last_eras: list[str] = []
        last_topics: set[str] = set()
        while scored and len(self.queue) + len(picked) < target:
            if random.random() < 0.12:
                idx = random.randrange(len(scored))  # exploration pick
            else:
                best, idx = -1e9, 0
                for i, (s, r) in enumerate(scored):
                    penalty = 0.6 * last_eras[-2:].count(r["era"])
                    penalty += 0.3 * len(last_topics & set(json.loads(r["topics"] or "[]")))
                    if s - penalty > best:
                        best, idx = s - penalty, i
            _, row = scored.pop(idx)
            picked.append(row["id"])
            last_eras.append(row["era"])
            last_topics = set(json.loads(row["topics"] or "[]"))
        self.queue.extend(picked)

    def reset(self) -> None:
        with self.lock:
            self.queue.clear()
            self.served.clear()

    def next_items(self, n: int = 8) -> list[dict]:
        n = max(1, min(n, 20))
        quiz_every = max(3, db.get_int("quiz_every", 7))
        with self.lock:
            self._refill(n + 16)
            ids = []
            while self.queue and len(ids) < n:
                fid = self.queue.popleft()
                row = db.query_one(
                    "SELECT seen_at, hidden FROM interactions WHERE fact_id = ?", (fid,)
                )
                if row is not None and (row["seen_at"] or row["hidden"]):
                    continue
                ids.append(fid)
                self.served[fid] = time.time()
            self._refill(n + 16)
            upcoming = list(self.queue)[:16]
        if self.on_upcoming:
            self.on_upcoming(upcoming)

        items: list[dict] = []
        for fact in db.get_facts(ids):
            items.append({"type": "fact", "fact": fact})
            self.since_quiz += 1
            if self.since_quiz >= quiz_every:
                q = quiz.make_quiz(exclude=set(ids))
                if q:
                    items.append({"type": "quiz", "quiz": q})
                    self.since_quiz = 0
        return items

    # -------------------------------------------------------------- feedback

    def record_view(self, fact_id: int, dwell_ms: int) -> dict:
        db.ensure_interaction(fact_id)
        row = db.query_one("SELECT seen_at FROM interactions WHERE fact_id = ?", (fact_id,))
        first = row is None or row["seen_at"] is None
        db.execute(
            "UPDATE interactions SET seen_at = COALESCE(seen_at, ?), seen_count = seen_count + 1, "
            "dwell_ms = dwell_ms + ? WHERE fact_id = ?",
            (db.now_iso(), max(0, int(dwell_ms)), fact_id),
        )
        if dwell_ms < 1500:
            delta = -0.3
        elif dwell_ms < 6000:
            delta = 0.0
        elif dwell_ms < 20000:
            delta = 0.5
        else:
            delta = 1.0
        adjust_affinity(_fact_tags(fact_id), delta)
        if first and dwell_ms >= 1500:
            return db.bump_daily(cards=1, xp=1)
        return db.progress()

    def react(self, fact_id: int, action: str) -> dict:
        if action not in REACTION_WEIGHTS:
            raise ValueError(f"unknown action {action}")
        db.ensure_interaction(fact_id)
        now = db.now_iso()
        updates = {
            "like": ("liked = 1, liked_at = ?", (now,)),
            "unlike": ("liked = 0, liked_at = NULL", ()),
            "save": ("saved = 1, saved_at = ?", (now,)),
            "unsave": ("saved = 0, saved_at = NULL", ()),
            "hide": ("hidden = 1", ()),
            "unhide": ("hidden = 0", ()),
        }
        if action in updates:
            sql, params = updates[action]
            db.execute(f"UPDATE interactions SET {sql} WHERE fact_id = ?", (*params, fact_id))
        adjust_affinity(_fact_tags(fact_id), REACTION_WEIGHTS[action])
        if action == "hide":
            # rebuild the queue so the lower affinity applies to the next posts
            with self.lock:
                self.queue.clear()
        return db.get_fact(fact_id) or {}
