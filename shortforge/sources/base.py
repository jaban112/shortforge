"""Source items: the only thing a script may be written from.

An Item carries verbatim source text (facts), a source URL (for the description),
and an optional licensed image with a credit line. If the image license is not
one we can legally reuse, `image` is None and the renderer draws a procedural
background instead — the video still ships.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


# Licenses we accept for reuse in a monetized video. Anything else -> no image.
ALLOWED_LICENSE_PREFIXES = (
    "cc0",
    "cc-by",          # cc-by-2.0, cc-by-3.0, cc-by-4.0
    "cc-by-sa",       # cc-by-sa-*
    "pd",             # public domain (Commons short names: pd, pd-us, pd-old, ...)
    "public domain",
)


def license_allowed(short_name: str | None) -> bool:
    """Deterministic license gate. Returns True only for CC0/CC-BY/CC-BY-SA/PD."""
    if not short_name:
        return False
    s = short_name.strip().lower().replace(" ", "-")
    parts = s.split("-")
    if "nc" in parts or "nd" in parts:
        return False
    if s == "cc0" or s.startswith("cc0-"):
        return True
    if s == "cc-by" or s.startswith("cc-by-"):
        return True
    if s == "pd" or s.startswith("pd-") or s.startswith("public-domain"):
        return True
    return False


@dataclass
class Image:
    url: str
    license: str          # short name as reported by the source, e.g. "cc-by-sa-4.0"
    credit: str           # "Photo: <author> / Wikimedia Commons (CC BY-SA 4.0)"
    page_url: str         # where a viewer can verify the license


@dataclass
class Item:
    id: str                     # stable id -> ledger dedupe key
    pack: str                   # source pack name
    title: str                  # short subject line (used in video title)
    source_text: str            # verbatim facts; the grounding gate checks against this
    source_url: str             # link for description
    source_label: str           # "Wikipedia" / "NASA APOD"
    year: int | None = None
    date_label: str = ""        # "September 6"
    image: Image | None = None
    extra: dict = field(default_factory=dict)


class Source(Protocol):
    name: str

    def fetch(self, http, cfg, day=None) -> list[Item]: ...
