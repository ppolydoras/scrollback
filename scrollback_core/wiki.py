"""Wikipedia "On this day" ingestion.

Uses the Wikifeeds REST endpoint on the wiki itself, with the older
api.wikimedia.org path as a fallback:
  https://{lang}.wikipedia.org/api/rest_v1/feed/onthisday/{type}/{MM}/{DD}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from . import HOMEPAGE, __version__, db
from .tagging import era_for, topics_for

log = logging.getLogger("scrollback.wiki")

PLACE_HINTS = re.compile(
    r"\b(city|town|village|capital|country|state|province|region|county|municipality|"
    r"island|river|sea|ocean|continent|commune|district|kingdom of|sovereign state|"
    r"metropolis|port city|largest city|federal subject)\b",
    re.IGNORECASE,
)
EVENT_HINTS = re.compile(
    r"\b(battle|war|siege|treaty|incident|disaster|earthquake|election|expedition|"
    r"revolution|uprising|massacre|attack|bombing|crisis|mission|flight|launch|accident|"
    r"conference|coup|riot|fire|eruption|act|campaign|offensive|operation|coronation|"
    r"assassination|shipwreck|sinking|crash|discovery|premiere|first)\b",
    re.IGNORECASE,
)

# Wikimedia serves thumbnails in fixed width steps
THUMB_STEPS = [330, 500, 960, 1280]


def user_agent() -> str:
    contact = db.get_setting("contact").strip()
    tail = f"; {contact}" if contact else ""
    return f"Scrollback/{__version__} (+{HOMEPAGE}; personal history reader{tail})"


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent(),
        "Accept": "application/json",
        "Api-User-Agent": user_agent(),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_day(lang: str, month: int, day: int, kind: str) -> dict:
    mm, dd = f"{month:02d}", f"{day:02d}"
    test_base = os.environ.get("SCROLLBACK_WIKI_BASE")  # used by the test suite
    if test_base:
        return _get_json(f"{test_base.rstrip('/')}/{lang}/feed/onthisday/{kind}/{mm}/{dd}")
    urls = [
        f"https://{lang}.wikipedia.org/api/rest_v1/feed/onthisday/{kind}/{mm}/{dd}",
        f"https://api.wikimedia.org/feed/v1/wikipedia/{lang}/onthisday/{kind}/{mm}/{dd}",
    ]
    last_err: Exception | None = None
    for url in urls:
        try:
            return _get_json(url)
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            last_err = err
            log.debug("fetch failed %s: %s", url, err)
    raise RuntimeError(f"Could not fetch {kind} for {mm}-{dd}: {last_err}")


# ---------------------------------------------------------------- parsing

def _page_title(page: dict) -> str:
    titles = page.get("titles") or {}
    return (
        titles.get("normalized")
        or page.get("normalizedtitle")
        or (page.get("title") or "").replace("_", " ")
    )


def _page_url(page: dict, lang: str) -> str:
    urls = (page.get("content_urls") or {}).get("desktop") or {}
    if urls.get("page"):
        return urls["page"]
    title = (page.get("title") or _page_title(page)).replace(" ", "_")
    return f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}"


def _best_image(page: dict) -> str | None:
    orig = page.get("originalimage") or {}
    thumb = page.get("thumbnail") or {}
    src_w = orig.get("width") or 0
    if orig.get("source") and src_w and src_w <= 1280 and not orig["source"].lower().endswith(".tif"):
        return orig["source"]
    tsrc = thumb.get("source")
    if not tsrc:
        return orig.get("source")
    m = re.search(r"/(\d+)px-", tsrc)
    if m and src_w:
        target = max([s for s in THUMB_STEPS if s <= src_w] or [int(m.group(1))])
        target = min(target, 1280)  # full-screen posts
        return tsrc.replace(f"/{m.group(1)}px-", f"/{target}px-", 1)
    return tsrc


def _is_placeish(page: dict) -> bool:
    desc = page.get("description") or ""
    title = _page_title(page)
    if re.fullmatch(r"\d{1,4}( BC)?|AD \d+|\d{1,4}s|\d+(st|nd|rd|th) century( BC)?", title):
        return True
    return bool(PLACE_HINTS.search(desc)) and not EVENT_HINTS.search(desc)


def choose_main_page(pages: list[dict], kind: str) -> dict | None:
    if not pages:
        return None
    if kind in ("birth", "death"):
        return pages[0]  # the person is always the first link
    best, best_score = None, -1e9
    for idx, page in enumerate(pages):
        desc = page.get("description") or ""
        extract = page.get("extract") or ""
        score = -0.25 * idx
        if _is_placeish(page):
            score -= 3
        if EVENT_HINTS.search(desc) or EVENT_HINTS.search(_page_title(page)):
            score += 2
        if page.get("thumbnail"):
            score += 0.5
        if len(extract) > 250:
            score += 0.5
        if score > best_score:
            best, best_score = page, score
    return best


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def source_key(lang: str, year: int, text: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return hashlib.sha1(f"{lang}|{year}|{norm}".encode()).hexdigest()


def parse_items(payload: dict, lang: str, month: int, day: int, include_people: bool) -> list[dict]:
    groups = [("selected", "event", 1), ("events", "event", 0)]
    if include_people:
        groups += [("births", "birth", 0), ("deaths", "death", 0)]
    out: dict[str, dict] = {}
    for key, kind, featured in groups:
        for item in payload.get(key) or []:
            year = item.get("year")
            text = _clean(item.get("text", ""))
            if not isinstance(year, int) or not text:
                continue
            pages = item.get("pages") or []
            main = choose_main_page(pages, kind)
            if kind in ("birth", "death") and (not main or len(main.get("extract") or "") < 200):
                continue  # skip thin biographies
            skey = source_key(lang, year, text)
            if skey in out:
                out[skey]["featured"] = max(out[skey]["featured"], featured)
                continue
            image = _best_image(main) if main else None
            if not image:
                for p in pages:
                    if p is not main and not _is_placeish(p) and (p.get("thumbnail") or p.get("originalimage")):
                        image = _best_image(p)
                        break
            related = [
                {"title": _page_title(p), "url": _page_url(p, lang), "description": p.get("description") or ""}
                for p in pages if p is not main
            ][:5]
            description = (main or {}).get("description") or ""
            extract = _clean((main or {}).get("extract") or "")
            out[skey] = {
                "source_key": skey,
                "lang": lang,
                "kind": kind,
                "featured": featured,
                "year": year,
                "month": month,
                "day": day,
                "text": text,
                "title": _page_title(main) if main else None,
                "description": description,
                "extract": extract,
                "image": image,
                "url": _page_url(main, lang) if main else None,
                "related": json.dumps(related),
                "era": era_for(year),
                "topics": json.dumps(topics_for(text, description, extract)),
            }
    return list(out.values())


def store_items(items: list[dict]) -> int:
    if not items:
        return 0
    cols = list(items[0].keys()) + ["created_at"]
    marks = ",".join("?" * len(cols))
    now = db.now_iso()
    added = 0
    with db.WRITE_LOCK:
        conn = db.connect()
        for it in items:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO facts({','.join(cols)}) VALUES({marks})",
                [it[c] for c in cols[:-1]] + [now],
            )
            if cur.rowcount:
                added += 1
            elif it["featured"]:
                conn.execute("UPDATE facts SET featured = 1 WHERE source_key = ?", (it["source_key"],))
        conn.commit()
    return added


def ingest_day(month: int, day: int) -> int:
    lang = db.get_setting("language") or "en"
    include_people = db.get_setting("include_people") == "1"
    kinds = ["all"] if include_people else ["selected", "events"]
    payload: dict = {}
    for kind in kinds:
        data = fetch_day(lang, month, day, kind)
        payload.update(data)
    items = parse_items(payload, lang, month, day, include_people)
    added = store_items(items)
    db.execute(
        "INSERT INTO ingest_state(lang, mmdd, fetched_at, items) VALUES(?,?,?,?) "
        "ON CONFLICT(lang, mmdd) DO UPDATE SET fetched_at = excluded.fetched_at, items = excluded.items",
        (lang, f"{month:02d}-{day:02d}", db.now_iso(), len(items)),
    )
    log.log(logging.INFO if added else logging.DEBUG,
            "fetched %02d-%02d: %d posts, %d new", month, day, len(items), added)
    return added


# ---------------------------------------------------------------- background crawler

def all_days() -> list[tuple[int, int]]:
    d = date(2024, 1, 1)  # leap year, so 29 February is included
    out = []
    while d.year == 2024:
        out.append((d.month, d.day))
        d += timedelta(days=1)
    return out


class Crawler(threading.Thread):
    """Fetches today first, then every other day of the year, slowly."""

    def __init__(self, delay: float | None = None, refresh_days: int = 180):
        super().__init__(name="wiki-crawler", daemon=True)
        self.delay = delay if delay is not None else float(os.environ.get("SCROLLBACK_CRAWL_DELAY", "2.5"))
        self.refresh_days = refresh_days
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.status = {"state": "starting", "last_error": None, "last_ok": None}

    def _due_days(self) -> list[tuple[int, int]]:
        lang = db.get_setting("language") or "en"
        cutoff = (datetime.now() - timedelta(days=self.refresh_days)).isoformat()
        fresh = {
            r["mmdd"] for r in db.query(
                "SELECT mmdd FROM ingest_state WHERE lang = ? AND fetched_at >= ?", (lang, cutoff)
            )
        }
        today = date.today()
        priority = [today + timedelta(days=k) for k in (0, 1, -1, 2, 3)]
        order = [(d.month, d.day) for d in priority]
        rest = all_days()
        random.shuffle(rest)
        order += [d for d in rest if d not in order]
        return [(m, d) for (m, d) in order if f"{m:02d}-{d:02d}" not in fresh]

    def run(self) -> None:
        backoff = 30.0
        while not self.stop_event.is_set():
            due = self._due_days()
            if not due:
                self.status["state"] = "complete"
                self.wake_event.wait(3600)
                self.wake_event.clear()
                continue
            month, day = due[0]
            self.status["state"] = f"fetching {month:02d}-{day:02d}"
            try:
                ingest_day(month, day)
                self.status["last_ok"] = db.now_iso()
                self.status["last_error"] = None
                backoff = 30.0
                self.stop_event.wait(self.delay)
            except Exception as err:  # network down, rate limit, bad JSON
                self.status["state"] = "waiting for network"
                self.status["last_error"] = str(err)
                log.warning("ingest failed: %s (retry in %.0fs)", err, backoff)
                self.wake_event.wait(backoff)
                self.wake_event.clear()
                backoff = min(backoff * 2, 900)

    def nudge(self) -> None:
        self.wake_event.set()
