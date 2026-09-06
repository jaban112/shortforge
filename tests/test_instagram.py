import json
import time

import requests

import shortforge.__main__ as cli
from shortforge.ledger import Ledger
from shortforge.upload import igtoken
from shortforge.upload.instagram import Instagram, InstagramError


class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._b = body
        self.text = json.dumps(body)
        self.headers = headers or {}

    def json(self):
        return self._b


def _script(monkeypatch, statuses):
    """Fake graph.instagram.com + rupload endpoints; records every call."""
    calls = []
    st = iter(statuses)

    def post(url, data=None, headers=None, timeout=None, **kw):
        calls.append(("POST", url, data, headers))
        if url.endswith("/media") and not url.endswith("media_publish"):
            assert data["media_type"] == "REELS" and data["upload_type"] == "resumable"
            return Resp(200, {"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v26.0/C1"})
        if "rupload.facebook.com" in url:
            assert headers["Authorization"] == "OAuth TOK" and headers["offset"] == "0" and headers["file_size"] == "7"
            return Resp(200, {"success": True})
        if url.endswith("/media_publish"):
            assert data["creation_id"] == "C1"
            return Resp(200, {"id": "M9"})
        raise AssertionError(url)

    def get(url, params=None, timeout=None, **kw):
        calls.append(("GET", url, params, None))
        if url.endswith("/C1"):
            return Resp(200, {"status_code": next(st)})
        if url.endswith("/M9"):
            return Resp(200, {"permalink": "https://www.instagram.com/reel/abc/"})
        if url.endswith("/me"):
            return Resp(200, {"id": "177", "username": "todayinhistory", "account_type": "BUSINESS"})
        if url.endswith("/content_publishing_limit"):
            return Resp(200, {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]})
        if url.endswith("/refresh_access_token"):
            return Resp(200, {"access_token": "NEW", "expires_in": 5184000})
        raise AssertionError(url)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    return calls


def test_reel_resumable_flow(monkeypatch, tmp_path):
    calls = _script(monkeypatch, ["IN_PROGRESS", "FINISHED"])
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"1234567")
    ig = Instagram("TOK", "177", poll_every=0, log=lambda *_: None)
    res = ig.upload_reel(mp4, "cap", share_to_feed=True)
    assert res.container_id == "C1" and res.media_id == "M9" and res.permalink.endswith("/reel/abc/")
    kinds = [(m, u.split("/")[-1]) for m, u, _, _ in calls]
    assert kinds == [("POST", "media"), ("POST", "C1"), ("GET", "C1"), ("GET", "C1"), ("POST", "media_publish"), ("GET", "M9")]
    assert calls[0][1].startswith("https://graph.instagram.com/v26.0/177/")


def test_reel_error_status_raises(monkeypatch, tmp_path):
    _script(monkeypatch, ["ERROR"])
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"1234567")
    ig = Instagram("TOK", "177", poll_every=0, log=lambda *_: None)
    try:
        ig.upload_reel(mp4, "cap")
    except InstagramError as e:
        assert "ERROR" in str(e)
    else:
        raise AssertionError("expected InstagramError")


def test_me_limit_refresh(monkeypatch):
    _script(monkeypatch, [])
    ig = Instagram("TOK", "177", log=lambda *_: None)
    assert ig.me()["username"] == "todayinhistory"
    assert ig.publishing_limit() == {"used": 3, "limit": 100}
    assert ig.refresh_token() == ("NEW", 5184000)


def test_igtoken_precedence(tmp_path):
    key = "secret"
    path = tmp_path / "ig.enc"
    env = "ENVTOKEN"
    t0 = igtoken.resolve(env, path, key)
    assert t0.token == env and t0.origin == igtoken.digest(env)
    # a refreshed descendant of env wins over env
    igtoken.save(path, igtoken.IgToken("REFRESHED", time.time(), time.time() + 100, igtoken.digest(env)), key)
    assert igtoken.resolve(env, path, key).token == "REFRESHED"
    # human rotated the secret -> env wins
    assert igtoken.resolve("ROTATED", path, key).token == "ROTATED"
    # wrong key -> file unreadable -> env
    assert igtoken.resolve(env, path, "other").token == env
    # no env: file only
    assert igtoken.resolve(None, path, key).token == "REFRESHED"
    assert igtoken.resolve(None, path, "other") is None


def test_multi_uploader_dispatch_records_per_platform(cfg, monkeypatch, tmp_path):
    cfg.uploaders = ["youtube", "instagram"]
    monkeypatch.setattr(cli, "_post_youtube", lambda c, m, meta: ("Y1", "https://youtube.com/shorts/Y1"))

    def boom(c, m, meta):
        raise RuntimeError("ig down")

    monkeypatch.setattr(cli, "_post_instagram", boom)
    monkeypatch.setitem(cli.POSTERS, "youtube", cli._post_youtube)
    monkeypatch.setitem(cli.POSTERS, "instagram", cli._post_instagram)
    L = Ledger(cfg.ledger_path)
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"0")
    L.record_video("k", mp4, 10.0, {"title": "t"})
    out = cli._upload_all(cfg, L, "k", mp4, {"title": "t #Shorts", "description": "d"})
    assert out == {"youtube": "Y1", "instagram": None}
    assert L.uploaded()[0][1] == "Y1"                       # legacy column kept for stats
    assert [k for k, _, _ in L.pending_for("instagram")] == ["k"]
    assert L.pending_for("youtube") == []
    p = {pl: (rid, err) for _, pl, rid, _, _, err in L.posts()}
    assert p["youtube"][0] == "Y1" and p["instagram"][1] == "ig down"


def test_ig_caption_limits():
    meta = {"title": "Sept 6, 1620: Mayflower #Shorts", "description": "x " * 10 + " ".join(f"#t{i}" for i in range(40))}
    cap = cli._ig_caption(meta)
    assert cap.startswith("Sept 6, 1620: Mayflower\n\n")
    assert len([w for w in cap.split() if w.startswith("#")]) == 30
    assert len(cap) <= 2200


def test_ig_refresh_cli_paths(cfg, monkeypatch, capsys):
    cfg.ig_token_key = "k"
    cfg.ig_user_id = "177"
    monkeypatch.setattr(cli.config, "load", lambda: cfg)
    # fresh token (<24h): nothing to do
    cfg.ig_access_token = "ENV"
    assert cli.main(["ig-refresh"]) == 0
    # old token, few days left -> refresh and persist
    igtoken.save(cfg.ig_token_path, igtoken.IgToken("ENV", time.time() - 40 * 86400, time.time() + 20 * 86400, igtoken.digest("ENV")), "k")
    _script(monkeypatch, [])
    assert cli.main(["ig-refresh"]) == 0
    assert igtoken.load_file(cfg.ig_token_path, "k").token == "NEW"
