"""Script model + sentence splitting + metadata builders."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..sources.base import Item

_ABBREV = {"mr", "mrs", "ms", "dr", "st", "mt", "no", "vs", "jr", "sr", "gen", "col", "lt", "u.s", "u.k", "e.g", "i.e", "ca", "c", "approx"}


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence splitter: break on . ! ? followed by whitespace and an
    uppercase/quote/digit, unless the token before the period is a known abbreviation
    or a single capital letter (initials)."""
    text = re.sub(r"\s+", " ", text.strip())
    out: list[str] = []
    start = 0
    for m in re.finditer(r"([.!?])(\"|'|\))?\s+(?=[\"'(A-Z0-9])", text):
        end = m.end()
        candidate = text[start:m.start() + 1 + (1 if m.group(2) else 0)]
        prev = candidate.rstrip(".!?\"')").split(" ")[-1].lower() if candidate.strip() else ""
        if m.group(1) == "." and (prev in _ABBREV or re.fullmatch(r"[a-z]", prev) or re.fullmatch(r"[a-z]\.[a-z]", prev)):
            continue
        out.append(candidate.strip())
        start = end
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


@dataclass
class Script:
    hook: str
    sentences: list[str]
    cta: str
    header: str                  # on-screen top text, e.g. "SEPTEMBER 6 · 1620"
    title: str                   # YouTube title (<=100 chars)
    description: str
    tags: list[str] = field(default_factory=list)
    writer: str = "template"     # "template" | "llm"

    @property
    def spoken(self) -> list[str]:
        """Sentences in speaking order: hook, body, cta. Each is one TTS unit."""
        return [self.hook, *self.sentences, self.cta]

    @property
    def word_count(self) -> int:
        return sum(len(s.split()) for s in self.spoken)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Script":
        return Script(**d)


CTA_BY_PACK = {
    "onthisday": "Follow for one true story from history, every single day.",
    "apod": "Follow for one real picture from space, every single day.",
}


def build_header(item: Item) -> str:
    if item.pack == "onthisday" and item.year is not None:
        return f"{item.date_label.upper()} · {item.year}"
    if item.pack == "apod":
        return "NASA · PICTURE OF THE DAY"
    return item.date_label.upper()


def build_title(item: Item, hook: str) -> str:
    if item.pack == "onthisday" and item.year is not None:
        base = f"{item.date_label}, {item.year}: {item.title}"
    else:
        base = item.title
    base = base.strip()
    suffix = " #Shorts"
    if len(base) + len(suffix) > 100:
        base = base[: 100 - len(suffix) - 1].rstrip() + "…"
    return base + suffix


def build_description(item: Item, spoken: list[str], channel_name: str) -> str:
    lines = [" ".join(spoken), ""]
    lines.append(f"Source: {item.source_label} — {item.source_url}")
    if item.image:
        lines.append(f"{item.image.credit} — {item.image.page_url}")
    lines.append("")
    lines.append("Narration is a synthetic (AI) voice. Every fact in this video comes from the linked source; nothing is invented.")
    lines.append(f"{channel_name} — one short, sourced story a day.")
    lines.append("")
    tags = build_tags(item)
    lines.append(" ".join("#" + t.replace(" ", "") for t in tags[:8]))
    desc = "\n".join(lines)
    return desc[:4900]


def build_tags(item: Item) -> list[str]:
    tags = ["shorts", "history", "on this day", "today in history", "facts", "education"]
    if item.pack == "apod":
        tags = ["shorts", "space", "astronomy", "nasa", "apod", "science", "facts", "education"]
    if item.year:
        tags.append(str(item.year))
    words = [w.strip(",.;:()") for w in item.title.split() if w[:1].isupper() and len(w) > 2]
    tags.extend(w.lower() for w in words[:4])
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:20]
