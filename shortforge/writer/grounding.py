"""Grounding gate: a script passes only if every number and every capitalized
name in it also appears in the source text (or in the explicitly allowed set).

This is a deterministic check, not a similarity score. A rejected script is
never patched — it is regenerated, and after N failures the deterministic
template writer takes over, which is grounded by construction.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from ..sources.base import Item

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000,
    "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "dozen": 12,
}
# Deliberately NOT numeric: "one" ("one day"), "first"/"second" (ordinal/temporal), "half".
_NUM_WORDS.pop("one", None)

# Capitalized words that may legally appear without being in the source.
ALWAYS_ALLOWED = {
    "i", "today", "tonight", "history", "follow", "subscribe", "shorts", "youtube", "wikipedia", "nasa",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "earth", "sun", "moon", "space", "universe", "a", "an", "the", "and", "but", "or", "so", "yet", "for",
    "nor", "in", "on", "at", "by", "to", "of", "from", "with", "as", "it", "its", "this", "that", "these",
    "those", "here", "there", "then", "now", "when", "where", "why", "how", "what", "who", "which",
    "he", "she", "they", "we", "you", "his", "her", "their", "our", "your", "him", "them", "us", "me",
    "was", "were", "is", "are", "be", "been", "being", "had", "has", "have", "did", "do", "does", "will",
    "would", "could", "should", "can", "may", "might", "must", "not", "no", "yes", "if", "because",
    "before", "after", "during", "until", "while", "since", "once", "again", "still", "just", "only",
    "even", "ever", "never", "always", "almost", "nearly", "exactly", "about", "over", "under", "more",
    "most", "less", "least", "very", "too", "also", "all", "any", "some", "each", "every", "both",
    "few", "many", "much", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "imagine", "picture", "think", "listen", "look", "watch", "meet", "remember", "back", "fast",
    "forward", "years", "year", "day", "days", "night", "morning", "later", "earlier", "meanwhile",
    "instead", "nobody", "everyone", "everything", "nothing", "something", "somewhere", "somehow",
    "why?", "and,", "but,", "so,", "yet", "true", "real", "story", "stories", "fact", "facts", "days",
    "century", "centuries", "decade", "decades", "world", "people", "man", "woman", "king", "queen",
    "president", "empire", "war", "city", "country", "ago", "there's", "here's", "it's", "that's",
    "what's", "who's", "let's", "don't", "didn't", "isn't", "wasn't", "weren't", "can't", "couldn't",
}


@dataclass
class Verdict:
    ok: bool
    bad_numbers: list[str] = field(default_factory=list)
    bad_names: list[str] = field(default_factory=list)

    def reason(self) -> str:
        parts = []
        if self.bad_numbers:
            parts.append("numbers not in source: " + ", ".join(self.bad_numbers))
        if self.bad_names:
            parts.append("names not in source: " + ", ".join(self.bad_names))
        return "; ".join(parts) or "ok"


def _numbers_in(text: str) -> set[str]:
    nums: set[str] = set()
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        raw = m.group(0).replace(",", "")
        nums.add(raw)
        if "." in raw:
            nums.add(raw.split(".")[0])
    for w in re.findall(r"[A-Za-z]+", text):
        v = _NUM_WORDS.get(w.lower())
        if v is not None:
            nums.add(str(v))
    return nums


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", text)}


def derived_numbers(item: Item, today: dt.date | None = None) -> set[str]:
    """Numbers a writer may legitimately derive: years-ago from the item year."""
    today = today or dt.date.today()
    out: set[str] = set()
    if item.year is not None:
        ago = today.year - item.year
        out.update({str(ago), str(ago // 10 * 10), str(ago // 100 * 100), str(item.year), str(item.year // 100 + 1)})
    if item.date_label:
        out.update(_numbers_in(item.date_label))
    return out


def check(script_lines: list[str], item: Item, extra_allowed: set[str] | None = None, today: dt.date | None = None) -> Verdict:
    source = item.source_text + " " + item.title
    src_nums = _numbers_in(source) | derived_numbers(item, today)
    src_words = _words(source) | ALWAYS_ALLOWED | {w.lower() for w in (extra_allowed or set())}
    bad_numbers: list[str] = []
    bad_names: list[str] = []
    for line in script_lines:
        for n in sorted(_numbers_in(line)):
            if n not in src_nums:
                bad_numbers.append(n)
        for tok in re.findall(r"[A-Za-z][A-Za-z'\-]*", line):
            if not tok[0].isupper():
                continue
            low = tok.lower().rstrip("'")
            if low in src_words:
                continue
            # possessive / plural forms of a source word
            if low.endswith("'s") and low[:-2] in src_words:
                continue
            if low.endswith("s") and low[:-1] in src_words:
                continue
            bad_names.append(tok)
    return Verdict(ok=not bad_numbers and not bad_names, bad_numbers=sorted(set(bad_numbers)), bad_names=sorted(set(bad_names)))
