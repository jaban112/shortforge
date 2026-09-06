"""Whole local loop, offline: fixture item → real Kokoro TTS → real ffmpeg render → browser posters
driving the look-alike Studio/Instagram pages inside a Playwright context → ledger posts.
Slow (~2 min, real TTS). Skipped when the TTS model is not present."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
FAKES = Path(__file__).parent / "fakes"

pytestmark = pytest.mark.skipif(not (ROOT / "models" / "kokoro-v1.0.onnx").exists(), reason="TTS model not downloaded")


def test_local_runner_end_to_end(cfg, item, monkeypatch):
    import shortforge.sources as sources
    from shortforge.ledger import Ledger
    from shortforge.local.app import LocalApp

    class Pack:
        name = "onthisday"

        def fetch(self, http, cfg, day=None):
            return [item]

    monkeypatch.setattr(sources, "get_pack", lambda name: Pack())
    cfg.max_seconds = 25
    cfg.target_seconds = 15

    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context()

        def handler(route, request):
            url = request.url
            if "youtube.com" in url:
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=(FAKES / "studio.html").read_text(encoding="utf-8"))
            elif "instagram.com" in url:
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=(FAKES / "instagram.html").read_text(encoding="utf-8"))
            else:
                route.abort()
        ctx.route("**/*", handler)

        class FakeBrowser:
            exe = "fake"

            def ensure_running(self):
                pass

            def connect(self):
                return ctx

            def close_connection(self):
                pass

        app = LocalApp(cfg, browser=FakeBrowser())
        app.state["settings"].update({"privacy": "unlisted", "videos_per_run": 1})
        app._default_runner(["youtube", "instagram"], 1)
        b.close()

    L = Ledger(cfg.ledger_path)
    posts = {pl: (rid, url, err) for _, pl, rid, url, _, err in L.posts()}
    assert posts["youtube-web"][0] == "abcDEF12345" and posts["youtube-web"][1].endswith("/shorts/abcDEF12345")
    assert posts["instagram-web"][0].startswith("web-")
    vids = L.db.execute("SELECT video_key, path FROM videos").fetchall()
    assert len(vids) == 1 and Path(vids[0][1]).exists() and Path(vids[0][1]).with_suffix(".jpg").exists()
    meta = json.loads(Path(vids[0][1]).with_suffix(".json").read_text())
    assert meta["thumb"] and meta["contains_synthetic_media"] is True
    assert L.pending_for("youtube-web") == [] and L.pending_for("instagram-web") == []
    assert L.recent_runs()[0][4] == 1
    L.close()
    snap = app.snapshot()
    assert snap["videos"][0]["thumb"] is True and snap["pending"] == {"youtube": []}
