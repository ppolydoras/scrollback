"""Turns a post into text that reads well aloud.

Piper reads digits through espeak-ng, which says 1805 as "one thousand eight
hundred and five". People say "eighteen oh five", so years and dates are
spelled out here first. English only: other languages get the text as is.
"""
from __future__ import annotations

import re

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
        "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
_ORD_ONES = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"]
_ORD_TEENS = {10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
              15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth"}


def _under_100(n: int) -> str:
    if n < 20:
        return ONES[n]
    tens, ones = divmod(n, 10)
    return TENS[tens] + (f"-{ONES[ones]}" if ones else "")


def _under_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{ONES[hundreds]} hundred")
    if rest:
        parts.append(("and " if hundreds else "") + _under_100(rest))
    return " ".join(parts) or "zero"


def number_words(n: int) -> str:
    if n < 1000:
        return _under_1000(n)
    thousands, rest = divmod(n, 1000)
    out = f"{_under_1000(thousands)} thousand"
    if rest:
        out += (" and " if rest < 100 else " ") + _under_1000(rest)
    return out


def ordinal_words(n: int) -> str:
    if n in _ORD_TEENS:
        return _ORD_TEENS[n]
    if n < 10:
        return _ORD_ONES[n]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return TENS[tens][:-1] + "ieth"  # twentieth, thirtieth
    return f"{TENS[tens]}-{_ORD_ONES[ones]}"


def year_words(year: int) -> str:
    if year < 0:
        return f"{number_words(-year)} B.C."
    if year < 1000:
        return f"A.D. {number_words(year)}"
    if year % 1000 == 0 or 2000 <= year < 2010:
        return number_words(year)
    high, low = divmod(year, 100)
    if low == 0:
        return f"{_under_100(high)} hundred"
    if low < 10:
        return f"{_under_100(high)} oh {ONES[low]}"
    return f"{_under_100(high)} {_under_100(low)}"


_MONTH_RX = "|".join(MONTHS)
_NON_LATIN = re.compile(r"[^\u0000-ɏ -⁯]")


def _drop_parentheses(text: str) -> str:
    """Remove bracketed asides that only make sense on the page:
    pronunciation guides, other scripts, lists separated by semicolons."""
    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if ";" in inner or _NON_LATIN.search(inner) or len(inner) > 80:
            return ""
        return m.group(0)
    text = re.sub(r"\s*\(([^()]*)\)", repl, text)
    return re.sub(r"\s{2,}", " ", text)


def _spell_dates(text: str) -> str:
    # 21 October 1805 / 21 October
    def dmy(m: re.Match) -> str:
        out = f"the {ordinal_words(int(m.group(1)))} of {m.group(2)}"
        if m.group(3):
            out += f", {year_words(int(m.group(3)))}"
        return out
    text = re.sub(rf"\b([1-9]|[12]\d|3[01]) ({_MONTH_RX})(?: (\d{{3,4}})\b(?! ?(?:BC|BCE)))?", dmy, text)

    # October 26, 1881 / October 26
    def mdy(m: re.Match) -> str:
        out = f"{m.group(1)} the {ordinal_words(int(m.group(2)))}"
        if m.group(3):
            out += f", {year_words(int(m.group(3)))}"
        return out
    text = re.sub(rf"\b({_MONTH_RX}) ([1-9]|[12]\d|3[01])\b(?:, (\d{{3,4}})\b)?", mdy, text)

    # 44 BC, AD 79
    text = re.sub(r"\b(\d{1,4}) ?(?:BC|BCE)\b", lambda m: year_words(-int(m.group(1))), text)
    text = re.sub(r"\b(?:AD|CE) (\d{1,4})\b", lambda m: year_words(int(m.group(1))), text)
    text = re.sub(r"\b(\d{1,4}) (?:AD|CE)\b", lambda m: year_words(int(m.group(1))), text)

    # a four-digit year after a word that usually introduces one
    lead = r"(?:in|of|on|by|until|till|since|from|to|between|and|before|after|during|year|summer|winter|spring|autumn|" + _MONTH_RX + ")"
    text = re.sub(rf"\b({lead}) (1\d{{3}}|20\d\d)\b(?![,.]?\d)",
                  lambda m: f"{m.group(1)} {year_words(int(m.group(2)))}", text, flags=re.IGNORECASE)
    # decades: the 1960s
    def decade(m: re.Match) -> str:
        words = year_words(int(m.group(1) + m.group(2) + "0"))
        if m.group(2) == "0":
            return words + "s"  # the nineteen hundreds
        head, _, tail = words.rpartition(" ")
        return f"{head} {tail[:-1]}ies"  # nineteen sixties
    text = re.sub(r"\b(1\d|20)(\d)0s\b", decade, text)
    return text


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "."


def speech_text(fact: dict, lang: str = "en") -> str:
    """The script read aloud for one post."""
    headline = fact.get("story_title") or fact.get("text") or ""
    if fact.get("story"):
        body = fact["story"]
        lede = fact.get("text") or ""
    else:
        body = fact.get("extract") or fact.get("description") or ""
        lede = ""
    body = _trim(_drop_parentheses(body), 1100)
    lede = _drop_parentheses(lede)

    if not lang.startswith("en"):
        parts = [str(fact.get("year", "")), headline, body]
        return "\n\n".join(p.strip() for p in parts if p and p.strip())

    year = int(fact["year"])
    when = f"The {ordinal_words(int(fact['day']))} of {MONTHS[int(fact['month']) - 1]}, {year_words(year)}."
    if fact.get("kind") == "birth":
        when = "Born on " + when[0].lower() + when[1:]
    elif fact.get("kind") == "death":
        when = "Died on " + when[0].lower() + when[1:]

    parts = [when, _spell_dates(headline)]
    if lede and lede.strip() != headline.strip():
        parts.append(_spell_dates(lede))
    if body:
        parts.append(_spell_dates(body))
    out = []
    for p in parts:
        p = p.strip()
        if p and p[-1] not in ".!?":
            p += "."
        if p:
            out.append(p)
    return "\n\n".join(out)
