"""Pack: NASA Astronomy Picture of the Day.

Only entries with media_type == "image" and NO `copyright` field are used — those
are NASA/public-domain images. Entries carrying a photographer copyright are
skipped entirely (no image, no text) because the explanation text is written
around that image.
"""
from __future__ import annotations

import datetime as dt

from .base import Image, Item

APOD = "https://api.nasa.gov/planetary/apod"
FIRST = dt.date(1995, 6, 16)


class Apod:
    name = "apod"

    def fetch(self, http, cfg, day: dt.date | None = None) -> list[Item]:
        day = day or dt.date.today()
        # today's picture first, then a spread of random archive entries
        entries: list[dict] = []
        try:
            entries.append(http.get_json(APOD, params={"api_key": cfg.nasa_api_key, "date": day.isoformat(), "thumbs": "false"}))
        except Exception:
            pass
        try:
            more = http.get_json(APOD, params={"api_key": cfg.nasa_api_key, "count": "12"})
            if isinstance(more, list):
                entries.extend(more)
        except Exception:
            pass
        items = [it for it in (self._to_item(e, day) for e in entries) if it is not None]
        items.sort(key=lambda it: -len(it.source_text))
        return items

    def _to_item(self, e: dict, day: dt.date) -> Item | None:
        if e.get("media_type") != "image":
            return None
        if e.get("copyright"):
            return None
        url = e.get("hdurl") or e.get("url")
        if not url:
            return None
        date = e.get("date", "")
        image = Image(
            url=url,
            license="pd-nasa",
            credit="Image: NASA / Astronomy Picture of the Day (public domain)",
            page_url=f"https://apod.nasa.gov/apod/ap{date.replace('-', '')[2:]}.html" if date else "https://apod.nasa.gov/",
        )
        return Item(
            id=f"apod:{date}",
            pack=self.name,
            title=e.get("title", "Astronomy Picture of the Day"),
            source_text=f"{e.get('title','')}\n\n{e.get('explanation','')}".strip(),
            source_url=image.page_url,
            source_label="NASA APOD",
            year=int(date[:4]) if date[:4].isdigit() else None,
            date_label=day.strftime("%B %-d"),
            image=image,
            extra={"apod_date": date},
        )
