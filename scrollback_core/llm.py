"""Optional storyteller: rewrites upcoming posts into short stories with a model.

Off by default. Supports the Anthropic Messages API and any OpenAI-compatible
chat endpoint (Ollama, LiteLLM, vLLM, OpenAI). The model only gets the
Wikipedia text for the post and is told to use nothing else.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import urllib.error
import urllib.request

from . import config, db
from .tagging import TOPIC_NAMES, format_year

log = logging.getLogger("scrollback.llm")

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": ""}
DEFAULT_BASE = {"anthropic": "https://api.anthropic.com", "openai": "http://localhost:11434/v1"}

SYSTEM_PROMPT = """You write short history posts for a personal reading feed.

Rules:
- Use only facts stated in the source text you are given. Do not add names, dates, numbers, places, causes or consequences that the source does not state.
- Plain international English. No idioms, no hype words, no emojis, no rhetorical questions.
- Write for a curious adult. Be concrete.

Return one JSON object and nothing else, with these keys:
  "title": a specific headline of at most 9 words,
  "story": 70 to 110 words telling what happened and why it is interesting, using only the source,
  "quiz": an object {"question": str, "options": [4 short strings], "answer": index 0-3} whose answer is stated in the source and whose question does not ask for the year, or null if the source is too thin,
  "topics": a list of up to 3 items chosen from: %s
""" % ", ".join(TOPIC_NAMES)


def provider_config() -> dict:
    provider = (db.get_setting("llm_provider") or "none").strip().lower()
    if provider not in ("anthropic", "openai"):
        return {"provider": "none", "ready": False, "reason": "Storyteller is off"}
    model = db.get_setting("llm_model").strip() or DEFAULT_MODELS[provider]
    base = (db.get_setting("llm_base_url").strip() or DEFAULT_BASE[provider]).rstrip("/")
    key = config.llm_api_key(provider)
    reason = None
    if not model:
        reason = "Set a model name"
    elif provider == "anthropic" and not key:
        reason = "Set ANTHROPIC_API_KEY before starting Scrollback"
    return {"provider": provider, "model": model, "base": base, "key": key,
            "ready": reason is None, "reason": reason}


def _post(url: str, headers: dict, body: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def complete(cfg: dict, user_text: str) -> str:
    if cfg["provider"] == "anthropic":
        data = _post(
            f"{cfg['base']}/v1/messages",
            {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"},
            {
                "model": cfg["model"],
                "max_tokens": 700,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_text}],
            },
        )
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
    data = _post(
        f"{cfg['base']}/chat/completions",
        headers,
        {
            "model": cfg["model"],
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        },
    )
    return data["choices"][0]["message"]["content"]


def build_source(fact) -> str:
    related = json.loads(fact["related"] or "[]")
    lines = [
        f"Year: {format_year(fact['year'])}",
        f"Date: {fact['day']} {['January','February','March','April','May','June','July','August','September','October','November','December'][fact['month'] - 1]}",
        f"Summary: {fact['text']}",
    ]
    if fact["title"]:
        lines.append(f"Main article: {fact['title']} ({fact['description'] or 'no description'})")
    if fact["extract"]:
        lines.append(f"Article extract: {fact['extract'][:1800]}")
    for r in related[:4]:
        if r.get("description"):
            lines.append(f"Related article: {r['title']}: {r['description']}")
    return "Source text:\n" + "\n".join(lines)


def parse_output(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    title = str(data.get("title") or "").strip()
    story = str(data.get("story") or "").strip()
    if not title or len(story) < 80:
        return None
    q = data.get("quiz")
    quiz = None
    if isinstance(q, dict):
        opts = q.get("options")
        ans = q.get("answer")
        if (isinstance(opts, list) and len(opts) == 4 and all(isinstance(o, str) and o.strip() for o in opts)
                and isinstance(ans, int) and 0 <= ans < 4 and str(q.get("question") or "").strip()):
            quiz = {"question": q["question"].strip(), "options": [o.strip() for o in opts], "answer": ans}
    topics = [t for t in (data.get("topics") or []) if t in TOPIC_NAMES][:3]
    return {"title": title[:120], "story": story[:1200], "quiz": quiz, "topics": topics}


class Storyteller(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="storyteller", daemon=True)
        self.jobs: queue.Queue[int] = queue.Queue()
        self.pending: set[int] = set()
        self.lock = threading.Lock()
        self.status = {"done": 0, "failed": 0, "last_error": None}

    def enqueue(self, ids: list[int]) -> None:
        if not provider_config()["ready"]:
            return
        with self.lock:
            for i in ids:
                if i not in self.pending:
                    self.pending.add(i)
                    self.jobs.put(i)

    def run(self) -> None:
        failures = 0
        while True:
            fid = self.jobs.get()
            try:
                cfg = provider_config()
                if not cfg["ready"]:
                    continue
                fact = db.query_one("SELECT * FROM facts WHERE id = ?", (fid,))
                if fact is None or fact["story"]:
                    continue
                out = parse_output(complete(cfg, build_source(fact)))
                if out is None:
                    self.status["failed"] += 1
                    continue
                topics = out["topics"] or json.loads(fact["topics"] or "[]")
                db.execute(
                    "UPDATE facts SET story_title = ?, story = ?, quiz = ?, topics = ?, "
                    "story_model = ?, enriched_at = ? WHERE id = ?",
                    (out["title"], out["story"], json.dumps(out["quiz"]) if out["quiz"] else None,
                     json.dumps(topics), cfg["model"], db.now_iso(), fid),
                )
                self.status["done"] += 1
                self.status["last_error"] = None
                failures = 0
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as err:
                failures += 1
                self.status["failed"] += 1
                detail = err.read().decode("utf-8", "replace")[:300] if isinstance(err, urllib.error.HTTPError) else ""
                self.status["last_error"] = f"{err} {detail}".strip()
                log.warning("storyteller failed: %s", self.status["last_error"])
                if failures >= 3:
                    # stop hammering a broken endpoint; drop the backlog
                    with self.lock:
                        while not self.jobs.empty():
                            self.pending.discard(self.jobs.get_nowait())
                    failures = 0
            finally:
                with self.lock:
                    self.pending.discard(fid)
