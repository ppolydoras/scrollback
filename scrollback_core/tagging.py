"""Era and topic tagging. Keyword rules, no network, no model needed."""
from __future__ import annotations

import re

# (label, first year inclusive, last year inclusive)
ERAS = [
    ("Ancient", -10000, 499),
    ("Medieval", 500, 1499),
    ("Early modern", 1500, 1799),
    ("19th century", 1800, 1899),
    ("1900 to 1945", 1900, 1945),
    ("1946 to 1999", 1946, 1999),
    ("21st century", 2000, 9999),
]
ERA_NAMES = [e[0] for e in ERAS]


def era_for(year: int) -> str:
    for name, lo, hi in ERAS:
        if lo <= year <= hi:
            return name
    return ERAS[-1][0]


_TOPIC_WORDS = {
    "War and conflict": [
        "battle", "war", "wars", "siege", "besieg*", "invade", "invasion", "army", "armies",
        "troops", "military", "navy", "naval", "rebellion", "revolt", "uprising", "massacre",
        "bomb*", "offensive", "occupation", "occupied", "coup", "surrender",
        "ceasefire", "armistice", "guerrilla", "militia", "insurgen*", "fleet", "warship",
        "artillery", "raid", "garrison",
    ],
    "Politics and power": [
        "king", "queen", "emperor", "empress", "president", "prime minister", "parliament",
        "election", "elected", "crowned", "coronation", "independence", "constitution",
        "treaty", "government", "republic", "monarchy", "dynasty", "sultan", "chancellor",
        "referendum", "annex*", "abdicat*", "assassinat*", "senate", "congress", "throne",
        "regime", "dictator", "parliamentary", "pharaoh", "caliph", "tsar", "czar", "shah",
    ],
    "Science and technology": [
        "discover*", "invent*", "patent", "scientist", "physicist", "chemist", "telescope",
        "vaccine", "theory", "experiment", "chemical element", "comet", "planet", "laboratory",
        "computer", "electric", "engine", "radio", "television", "internet", "mathematic*",
        "medicine", "medical", "surgery", "x-ray", "nuclear", "atomic", "microscope",
        "steam", "telegraph", "telephone", "aircraft", "airplane", "flight", "locomotive",
    ],
    "Space": [
        "spacecraft", "satellite", "orbit", "astronaut", "cosmonaut", "lunar", "the moon",
        "mars", "nasa", "space station", "space shuttle", "rocket", "space probe", "apollo",
        "sputnik", "soyuz",
    ],
    "Exploration": [
        "expedition", "explorer", "voyage", "circumnavigat*", "first ascent", "antarctic",
        "arctic", "north pole", "south pole", "colony", "coloni*", "landfall", "sighted",
        "navigator", "charted",
    ],
    "Disasters": [
        "earthquake", "erupt*", "volcan*", "tsunami", "flood", "hurricane",
        "typhoon", "cyclone", "great fire", "fire", "famine", "plague", "epidemic", "pandemic",
        "shipwreck", "sinks", "sank", "explosion", "crash", "crashes", "derail*", "disaster",
        "collapse", "avalanche", "outbreak",
    ],
    "Arts and culture": [
        "premiere", "premieres", "opera", "novel", "poem", "poet", "painting", "painter",
        "album", "film", "theatre", "theater", "symphony", "composer", "museum", "published",
        "book", "broadcast", "festival", "exhibition", "ballet", "sculpture", "cathedral",
        "architect", "musical", "song", "literature", "play by",
    ],
    "Religion": [
        "pope", "church", "saint", "bishop", "monastery", "crusade", "mosque", "temple",
        "religious", "christian", "islam", "muslim", "jewish", "buddhis*", "papal", "heresy",
        "reformation", "archbishop", "council of", "patriarch", "orthodox", "catholic",
    ],
    "Economy and trade": [
        "bank", "stock exchange", "stock market", "currency", "trade", "company", "founded",
        "corporation", "market", "railway", "railroad", "canal", "tariff", "economic",
        "gold rush", "merchant", "east india", "bankrupt*", "inflation",
    ],
    "Sport": [
        "olympic", "world cup", "championship", "tournament", "football", "baseball",
        "cricket", "marathon", "grand prix", "medal", "world record", "chess",
    ],
    "Rights and society": [
        "slavery", "slave*", "abolition*", "abolish*", "suffrage", "civil rights", "protest",
        "women's", "strike", "emancipat*", "segregation", "rights", "labour", "labor union",
        "riot", "demonstrat*",
    ],
}
TOPIC_NAMES = list(_TOPIC_WORDS)


def _compile(words: list[str]) -> re.Pattern:
    """Words ending in * are stems and match any continuation. Other words
    match as whole words, with an optional plural ending."""
    parts = []
    for w in words:
        if w.endswith("*"):
            parts.append(r"\b" + re.escape(w[:-1]))
        else:
            parts.append(r"\b" + re.escape(w) + r"(?:s|es)?\b")
    return re.compile("|".join(parts), re.IGNORECASE)


_TOPIC_RE = {name: _compile(words) for name, words in _TOPIC_WORDS.items()}


def topics_for(text: str, description: str = "", extract: str = "") -> list[str]:
    """Score topics on the headline (weight 3), description (2), extract opening (1)."""
    first_sentences = " ".join(re.split(r"(?<=[.!?])\s+", extract or "")[:2])
    scored = []
    for name, rx in _TOPIC_RE.items():
        score = (
            3 * len(rx.findall(text or ""))
            + 2 * len(rx.findall(description or ""))
            + len(rx.findall(first_sentences))
        )
        if score >= 2:
            scored.append((score, name))
    scored.sort(reverse=True)
    return [name for _, name in scored[:3]]


def format_year(year: int) -> str:
    if year < 0:
        return f"{-year} BC"
    if year < 1000:
        return f"AD {year}"
    return str(year)
