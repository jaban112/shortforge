"""Register the browser-driven posters ("youtube-web", "instagram-web") in the CLI's POSTERS
table so `_upload_all` and `upload-pending` treat them like any other platform."""
from __future__ import annotations

from pathlib import Path


def register_web_posters(app) -> None:
    from .. import __main__ as cli
    from .ig_web import InstagramWeb
    from .yt_web import YouTubeWeb

    def _page():
        b = app._browser()
        ctx = b.connect()
        return b, ctx.new_page()

    def post_youtube_web(cfg, mp4: Path, meta: dict):
        b, page = _page()
        try:
            vid = YouTubeWeb(page, cfg.workdir / "ui", log=app.log).upload(mp4, meta["title"], meta["description"], privacy=cfg.yt_privacy, synthetic=True)
            return vid, (f"https://youtube.com/shorts/{vid}" if not vid.startswith("web-") else "https://studio.youtube.com/channel/videos")
        finally:
            try:
                page.close()
            finally:
                b.close_connection()

    def post_instagram_web(cfg, mp4: Path, meta: dict):
        b, page = _page()
        try:
            rid = InstagramWeb(page, cfg.workdir / "ui", log=app.log).upload(mp4, cli._ig_caption(meta))
            return rid, "https://www.instagram.com/"
        finally:
            try:
                page.close()
            finally:
                b.close_connection()

    cli.POSTERS["youtube-web"] = post_youtube_web
    cli.POSTERS["instagram-web"] = post_instagram_web
