"""CDP attach to a real Chromium process (its own module: sync_playwright cannot nest)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")


def test_browser_cdp_attach(tmp_path):
    from shortforge.local.browser import Browser, cdp_alive, find_browser

    exe = find_browser()
    assert exe, "no chromium available"
    b = Browser(tmp_path / "profile", port=9555, log=lambda *_: None, headless=True, exe=exe)
    try:
        b.ensure_running()
        assert cdp_alive(9555)
        ctx = b.connect()
        page = ctx.new_page()
        page.goto("data:text/html,<title>cdp-ok</title>")
        assert page.title() == "cdp-ok"
        page.close()
        b.close_connection()
        assert cdp_alive(9555)  # detaching must not kill the user's browser
        ctx2 = b.connect()      # re-attach works
        assert ctx2 is not None
        b.close_connection()
    finally:
        b.stop()


