"""Pack: On This Day — Wikimedia feed API + Commons license check.

Facts come verbatim from Wikipedia's curated "selected" events for the date
(plus the linked article's summary extract). Images only if Commons reports a
reusable license; the credit line is built from Commons' own metadata.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from urllib.parse import unquote

from .base import Image, Item, license_allowed

FEED = "https://api.wikimedia.org/feed/v1/wikipedia/{lang}/onthisday/{kind}/{mm}/{dd}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_TAG.sub("", s or "")).strip()


def commons_filename(url: str) -> str | None:
    """upload.wikimedia.org URL -> 'File:Name.jpg' (handles /thumb/ URLs)."""
    m = re.search(r"/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)", url)
    if not m:
        return None
    name = unquote(m.group(1)).replace("_", " ")
    return f"File:{name}"


def commons_license(http, filename: str, width: int = 1080) -> Image | None:
    """Query Commons for license + attribution. Returns Image only if reusable."""
    data = http.get_json(
        COMMONS_API,
        params={
            "action": "query",
            "titles": filename,
            "prop": "imageinfo",
            "iiprop": "extmetadata|url",
            "iiurlwidth": str(width),
            "format": "json",
        },
    )
    pages = data.get("query", {}).get("pages", {})
    for _pid, page in pages.items():
        if "missing" in page:
            return None  # not on Commons (e.g. en-wiki fair use) -> not reusable
        infos = page.get("imageinfo") or []
        if not infos:
            return None
        info = infos[0]
        meta = info.get("extmetadata", {})
        lic = (meta.get("License", {}) or {}).get("value") or (meta.get("LicenseShortName", {}) or {}).get("value")
        if not license_allowed(lic):
            return None
        artist = _strip_html((meta.get("Artist", {}) or {}).get("value", ""))
        short = _strip_html((meta.get("LicenseShortName", {}) or {}).get("value", lic or ""))
        credit = f"Image: {artist or 'Wikimedia Commons'} / Wikimedia Commons ({short})"
        return Image(
            url=info.get("thumburl") or info.get("url"),
            license=lic,
            credit=credit[:140],
            page_url=info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{filename}",
        )
    return None


class OnThisDay:
    name = "onthisday"

    def fetch(self, http, cfg, day: dt.date | None = None) -> list[Item]:
        day = day or dt.date.today()
        mm, dd = f"{day.month:02d}", f"{day.day:02d}"
        data = http.get_json(
            FEED.format(lang=cfg.language, kind="selected", mm=mm, dd=dd),
            headers={"Api-User-Agent": cfg.user_agent},
        )
        items: list[Item] = []
        for ev in data.get("selected", []):
            items.append(self._to_item(http, ev, day))
        # deterministic order: with-image first, then richer context first
        items.sort(key=lambda it: (it.image is None, -len(it.source_text)))
        return items

    def _to_item(self, http, ev: dict, day: dt.date) -> Item:
        text = (ev.get("text") or "").strip()
        year = ev.get("year")
        pages = ev.get("pages") or []
        page = pages[0] if pages else {}
        extract = (page.get("extract") or "").strip()
        title = page.get("titles", {}).get("normalized") or page.get("title") or text[:60]
        page_url = (page.get("content_urls", {}).get("desktop", {}) or {}).get("page") or "https://en.wikipedia.org/"
        source_text = f"{text}\n\n{extract}".strip()
        image = None
        for p in pages:
            src = (p.get("originalimage") or p.get("thumbnail") or {}).get("source")
            if not src:
                continue
            fn = commons_filename(src)
            if not fn:
                continue
            try:
                image = commons_license(http, fn)
            except Exception:
                image = None
            if image:
                break
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        return Item(
            id=f"otd:{day.month:02d}-{day.day:02d}:{year}:{digest}",
            pack=self.name,
            title=title,
            source_text=source_text,
            source_url=page_url,
            source_label="Wikipedia",
            year=year,
            date_label=day.strftime("%B %-d"),
            image=image,
            extra={"event_text": text},
        )
