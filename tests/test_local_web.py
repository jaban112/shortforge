"""Local one-click app: browser-driven uploaders against look-alike pages (routed in-process,
no network), CDP attach to a real Chromium process, scheduler determinism, JSON API."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

FAKES = Path(__file__).parent / "fakes"


def _route_fakes(page):
    """Serve the look-alike pages for the real hostnames so the uploaders' URLs are exercised."""
    def handler(route, request):
        url = request.url
        if "youtube.com" in url:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=(FAKES / "studio.html").read_text(encoding="utf-8"))
        elif "instagram.com" in url:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=(FAKES / "instagram.html").read_text(encoding="utf-8"))
        else:
            route.abort()
    page.route("**/*", handler)


@pytest.fixture(scope="module")
def pw():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_youtube_web_upload_flow(pw, tmp_path):
    from shortforge.local.yt_web import YouTubeWeb

    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 1000)
    page = pw.new_page()
    _route_fakes(page)
    logs = []
    yt = YouTubeWeb(page, tmp_path / "ui", log=logs.append, timeout_ms=10_000)
    assert yt.logged_in()
    vid = yt.upload(mp4, "September 6, 1620: Mayflower #Shorts", "desc line\nSource: Wikipedia", privacy="unlisted", synthetic=True)
    assert vid == "abcDEF12345"
    f = page.evaluate("window.__fake")
    assert f["file"] == "clip.mp4"
    assert f["kids"] == "VIDEO_MADE_FOR_KIDS_NOT_MFK"
    assert f["altered"] == "VIDEO_HAS_ALTERED_CONTENT_YES"
    assert f["visibility"] == "UNLISTED"
    assert f["done"] and f["closed"]
    assert f["title"] == "September 6, 1620: Mayflower #Shorts"
    assert "Source: Wikipedia" in f["desc"]
    page.close()


def test_youtube_web_failure_screenshots(pw, tmp_path):
    from shortforge.local.yt_web import UiStep, YouTubeWeb

    page = pw.new_page()
    page.route("**/*", lambda route, req: route.fulfill(status=200, content_type="text/html", body="<html><body>nothing here</body></html>"))
    yt = YouTubeWeb(page, tmp_path / "ui", log=lambda *_: None, timeout_ms=1500)
    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"0")
    with pytest.raises(UiStep) as ei:
        yt.upload(mp4, "t", "d")
    assert ei.value.step == "file"
    assert ei.value.shot and ei.value.shot.exists() and ei.value.shot.name.startswith("yt-")
    page.close()


def test_instagram_web_upload_flow(pw, tmp_path):
    from shortforge.local.ig_web import InstagramWeb

    mp4 = tmp_path / "reel.mp4"
    mp4.write_bytes(b"\x00" * 1000)
    page = pw.new_page()
    _route_fakes(page)
    ig = InstagramWeb(page, tmp_path / "ui", log=lambda *_: None, timeout_ms=10_000)
    assert ig.logged_in()
    rid = ig.upload(mp4, "Mayflower\n\nSource: Wikipedia #shorts #history")
    assert rid.startswith("web-")
    f = page.evaluate("window.__fake")
    assert f["file"] == "reel.mp4" and f["ok"] and f["ratio"] == "9:16" and f["shared"]
    assert f["caption"].startswith("Mayflower")
    page.close()


def test_scheduler_tick_is_once_per_slot(cfg):
    from shortforge.local.app import LocalApp

    fired = []
    app = LocalApp(cfg, runner=lambda platforms, n: fired.append((tuple(platforms), n)))
    app.state["schedule"].update({"enabled": True, "times": ["09:00", "21:00"], "platforms": ["youtube", "instagram"]})
    d = dt.datetime(2026, 9, 6)
    assert not app.scheduler_tick(d.replace(hour=8, minute=59))
    assert app.scheduler_tick(d.replace(hour=9, minute=0))
    time.sleep(0.2)
    assert not app.scheduler_tick(d.replace(hour=9, minute=1))       # same slot, same day: no
    assert not app.scheduler_tick(d.replace(hour=16, minute=0))      # 09:00 slot older than 6h: skipped, 21:00 not yet
    assert app.scheduler_tick(d.replace(hour=21, minute=3))
    time.sleep(0.2)
    assert app.scheduler_tick(d.replace(day=7, hour=9, minute=0))    # next day fires again
    time.sleep(0.2)
    assert fired == [(("youtube", "instagram"), 1)] * 3
    app.state["schedule"]["enabled"] = False
    assert not app.scheduler_tick(d.replace(day=8, hour=9, minute=0))
    # persisted
    saved = json.loads(app.state_path.read_text())
    assert "2026-09-06T09:00" in saved["schedule"]["last_run"]


def test_scheduler_does_not_double_start_while_running(cfg):
    from shortforge.local.app import LocalApp

    gate = threading.Event()
    app = LocalApp(cfg, runner=lambda platforms, n: gate.wait(5))
    app.state["schedule"].update({"enabled": True, "times": ["09:00", "09:01"]})
    d = dt.datetime(2026, 9, 6, 9, 1)
    assert app.scheduler_tick(d)
    time.sleep(0.1)
    assert app.running == "run"
    assert not app.scheduler_tick(d)  # second slot must wait while a job is in flight
    gate.set()
    time.sleep(0.3)
    assert app.running is None


def test_local_api(cfg):
    from shortforge.local.app import LocalApp

    calls = []
    app = LocalApp(cfg, runner=lambda platforms, n: calls.append((platforms, n)))
    app.port = 0
    # run the server on an ephemeral port in a thread
    from http.server import ThreadingHTTPServer

    orig = ThreadingHTTPServer.__init__

    def serve():
        app.serve(open_browser=False)

    class _Srv(ThreadingHTTPServer):
        pass

    # patch port after bind: simplest is to pick a free port ourselves
    import socket

    s = socket.socket(); s.bind(("127.0.0.1", 0)); app.port = s.getsockname()[1]; s.close()
    threading.Thread(target=serve, daemon=True).start()
    base = f"http://127.0.0.1:{app.port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/api/state", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
    assert "shortforge" in html and "자동화" in html
    assert post("/api/settings", {"pack": "apod", "privacy": "unlisted", "bogus": 1})[0] == 200
    st = json.loads(urllib.request.urlopen(base + "/api/state").read())
    assert st["state"]["settings"]["pack"] == "apod" and "bogus" not in st["state"]["settings"]
    code, j = post("/api/schedule", {"enabled": True, "times": ["21:00", "09:00", "bad"], "platforms": ["youtube"]})
    assert code == 200 and j["schedule"]["times"] == ["09:00", "21:00"]
    code, j = post("/api/run", {"platforms": ["youtube"], "n": 2})
    assert code == 200 and j["ok"]
    time.sleep(0.3)
    assert calls == [(["youtube"], 2)]
    assert post("/api/nope", {})[0] == 404
    # thumb/video routes decode the URL-encoded key
    from shortforge.ledger import Ledger as _Ledger
    mp4 = cfg.outdir / "v.mp4"; mp4.write_bytes(b"MP4!"); mp4.with_suffix(".jpg").write_bytes(b"\xff\xd8JPG")
    L = _Ledger(cfg.ledger_path); L.record_video("otd:09-06:1620:x", mp4, 1.0, {"title": "t"}); L.close()
    from urllib.parse import quote as _q
    key = _q("otd:09-06:1620:x", safe="")
    r = urllib.request.urlopen(base + "/api/thumb/" + key, timeout=5); assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg" and r.read() == b"\xff\xd8JPG"
    r = urllib.request.urlopen(base + "/api/video/" + key, timeout=5); assert r.status == 200 and r.read() == b"MP4!"
    try:
        urllib.request.urlopen(base + "/api/video/none", timeout=5); assert False
    except urllib.error.HTTPError as e:
        assert e.code == 404
