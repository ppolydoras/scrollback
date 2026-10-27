"""Builds the browser demo that GitHub Pages serves.

The demo is the normal front end plus static/demo.js, which answers the
app's /api/ calls inside the browser from a bundled set of events. This
module fetches those events from Wikipedia and writes the site:

    python3 scrollback.py --build-demo site/
"""
from __future__ import annotations

import json
import logging
import random
import shutil
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from . import HOMEPAGE, __version__, config, db
from .speech import speech_text
from .tagging import ERA_NAMES, TOPIC_NAMES
from .wiki import all_days, fetch_day, parse_items

log = logging.getLogger("scrollback.demo")

DEMO_URL = "https://ppolydoras.github.io/scrollback/"
NEAR = range(-1, 9)      # days around the build date, so "today" has posts all week
SPREAD = 26              # other days, spread across the year
PER_NEAR_DAY = 14
TOTAL = 600
MIN_EXTRACT = 120


def demo_days(today: date) -> list[tuple[int, int]]:
    near = [today + timedelta(days=k) for k in NEAR]
    step = 366 // SPREAD
    start = today + timedelta(days=NEAR.stop + step // 2)
    far = [start + timedelta(days=i * step) for i in range(SPREAD)]
    out: list[tuple[int, int]] = []
    for d in near + far:
        if (d.month, d.day) not in out:
            out.append((d.month, d.day))
    return out


def fetch(days: list[tuple[int, int]], delay: float, lang: str = "en") -> list[dict]:
    items: list[dict] = []
    for i, (month, day) in enumerate(days):
        payload: dict = {}
        try:
            for kind in ("selected", "events"):
                payload.update(fetch_day(lang, month, day, kind))
                time.sleep(delay)
        except Exception as err:  # one bad day should not stop the build
            log.warning("skipped %02d-%02d: %s", month, day, err)
            continue
        found = parse_items(payload, lang, month, day, include_people=False)
        items.extend(found)
        log.info("%2d/%d  %02d-%02d  %d events", i + 1, len(days), month, day, len(found))
    return items


def _quality(it: dict, rng: random.Random) -> float:
    return (2.0 * it["featured"] + (1.5 if it["image"] else 0.0)
            + min(len(it["extract"] or ""), 600) / 600 + rng.random() * 0.6)


def select(items: list[dict], near_days: set[tuple[int, int]], total: int = TOTAL,
           seed: int | None = None) -> list[dict]:
    """Keep the strongest events: a full week of "on this day" posts, then an
    even spread across eras for the rest."""
    rng = random.Random(seed)
    seen, pool = set(), []
    for it in items:
        if it["source_key"] in seen or len(it["extract"] or "") < MIN_EXTRACT:
            continue
        seen.add(it["source_key"])
        pool.append((_quality(it, rng), it))
    pool.sort(key=lambda p: p[0], reverse=True)

    chosen: list[dict] = []
    per_day: dict[tuple[int, int], int] = {}
    rest: dict[str, list[dict]] = {era: [] for era in ERA_NAMES}
    for _, it in pool:
        key = (it["month"], it["day"])
        if key in near_days and per_day.get(key, 0) < PER_NEAR_DAY:
            per_day[key] = per_day.get(key, 0) + 1
            chosen.append(it)
        else:
            rest.setdefault(it["era"], []).append(it)

    queues = [q for q in rest.values() if q]
    while len(chosen) < total and queues:
        for q in list(queues):
            if len(chosen) >= total:
                break
            chosen.append(q.pop(0))
            if not q:
                queues.remove(q)
    return chosen


def to_demo(it: dict, fid: int) -> dict:
    related = json.loads(it["related"]) if isinstance(it["related"], str) else it["related"]
    topics = json.loads(it["topics"]) if isinstance(it["topics"], str) else it["topics"]
    extract = it["extract"] or ""
    if len(extract) > 1400:
        cut = extract[:1400]
        extract = cut[: cut.rfind(". ") + 1] or cut
    fact = {
        "id": fid, "kind": it["kind"], "featured": it["featured"], "year": it["year"],
        "month": it["month"], "day": it["day"], "text": it["text"], "title": it["title"],
        "description": it["description"], "extract": extract, "image": it["image"],
        "url": it["url"], "related": related[:3], "era": it["era"], "topics": topics,
    }
    fact["speech"] = speech_text(fact)
    return fact


def _head_tags(url: str, count: int) -> str:
    desc = (f"A feed of real events from Wikipedia, one full screen at a time. "
            f"Browser demo with {count} events. The app runs on your own computer.")
    tags = [
        f'<meta name="description" content="{desc}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:title" content="Scrollback: history, one post at a time">',
        f'<meta property="og:description" content="{desc}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{url}social-preview.png">',
        '<meta property="og:image:width" content="1280">',
        '<meta property="og:image:height" content="640">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<link rel="canonical" href="{url}">',
    ]
    return "\n  ".join(tags)


def write_site(out: Path, facts: list[dict], url: str = DEMO_URL) -> None:
    if out.exists():
        shutil.rmtree(out)
    static = out / "static"
    static.mkdir(parents=True)
    for name in ("app.css", "app.js", "demo.js", "icon.svg"):
        shutil.copy2(config.STATIC_DIR / name, static / name)
    shutil.copytree(config.STATIC_DIR / "fonts", static / "fonts")
    preview = config.ROOT / "docs" / "social-preview.png"
    if preview.exists():
        shutil.copy2(preview, out / "social-preview.png")

    html = (config.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("<title>Scrollback</title>",
                        "<title>Scrollback demo</title>\n  " + _head_tags(url, len(facts)), 1)
    app_tag = '<script src="static/app.js"></script>'
    if app_tag not in html:
        raise RuntimeError("index.html no longer loads static/app.js")
    html = html.replace(app_tag, '<script src="static/demo.js"></script>\n  ' + app_tag, 1)
    (out / "index.html").write_text(html, encoding="utf-8")

    data = {
        "built": date.today().isoformat(),
        "version": __version__,
        "source": HOMEPAGE,
        "meta": {"eras": ERA_NAMES, "topics": TOPIC_NAMES},
        "facts": facts,
    }
    (static / "demo-data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                           encoding="utf-8")


def build(out_dir: str | Path, url: str = DEMO_URL, delay: float = 1.0,
          days: list[tuple[int, int]] | None = None, min_facts: int = 150) -> int:
    today = date.today()
    days = days if days is not None else demo_days(today)
    near = {(d.month, d.day) for d in (today + timedelta(days=k) for k in NEAR)}
    if not url.endswith("/"):
        url += "/"
    with tempfile.TemporaryDirectory() as tmp:
        db.init(Path(tmp) / "build.db")  # only for the User-Agent setting
        try:
            items = fetch(days, delay)
        finally:
            db.close_thread()
    chosen = select(items, near)
    chosen.sort(key=lambda it: (it["year"], it["month"], it["day"]))
    facts = [to_demo(it, i + 1) for i, it in enumerate(chosen)]
    if len(facts) < min_facts:
        raise RuntimeError(f"only {len(facts)} events found, expected at least {min_facts}")
    write_site(Path(out_dir), facts, url)
    eras = {e: sum(1 for f in facts if f["era"] == e) for e in ERA_NAMES}
    log.info("wrote %d events to %s (%s)", len(facts), out_dir,
             "; ".join(f"{k}: {v}" for k, v in eras.items()))
    return len(facts)


def build_all_days(out_dir: str | Path, url: str = DEMO_URL, min_facts: int = 1) -> int:
    """Every day of the year with no delay. For tests against the mock server."""
    return build(out_dir, url, delay=0, days=all_days(), min_facts=min_facts)
