import json
import urllib.parse

import requests

from shortforge.upload import auth, youtube


class Resp:
    def __init__(self, status, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        return self._body


def test_auth_url_has_offline_consent_and_scopes():
    u = auth.build_auth_url("cid", "http://127.0.0.1:8765", "st")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert "youtube.upload" in q["scope"][0] and "youtube.readonly" in q["scope"][0]
    assert q["state"] == ["st"]


def test_refresh_error_explains_testing_mode(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(400, {"error": "invalid_grant"}))
    try:
        auth.refresh_access_token("a", "b", "c")
    except RuntimeError as e:
        assert "7 days" in str(e)
    else:
        raise AssertionError("expected failure")


def test_resumable_upload_chunks_and_resumes(monkeypatch, tmp_path):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"x" * (youtube.CHUNK + 1000))
    posts = []
    puts = []

    def fake_post(url, params=None, headers=None, data=None, timeout=None, **kw):
        if "oauth2.googleapis.com" in url:
            return Resp(200, {"access_token": "tok", "expires_in": 3600})
        posts.append((url, params, headers, json.loads(data)))
        return Resp(200, {}, {"Location": "https://upload.session/1"})

    def fake_put(url, headers=None, data=None, timeout=None, **kw):
        puts.append(headers["Content-Range"])
        if len(puts) == 1:
            return Resp(308, headers={"Range": f"bytes=0-{youtube.CHUNK - 1}"})
        return Resp(200, {"id": "abc123"})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "put", fake_put)
    yt = youtube.YouTube("cid", "sec", "ref", log=lambda *_: None)
    vid = yt.upload(mp4, "T", "D", ["a"], privacy="public")
    assert vid == "abc123"
    body = posts[0][3]
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["snippet"]["categoryId"] == "27"
    assert posts[0][2]["X-Upload-Content-Length"] == str(youtube.CHUNK + 1000)
    assert puts == [f"bytes 0-{youtube.CHUNK - 1}/{youtube.CHUNK + 1000}", f"bytes {youtube.CHUNK}-{youtube.CHUNK + 999}/{youtube.CHUNK + 1000}"]


def test_video_stats_parses(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(200, {"access_token": "tok"}))
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(200, {"items": [{"id": "v", "statistics": {"viewCount": "12", "likeCount": "3"}, "status": {"privacyStatus": "public"}}]}))
    yt = youtube.YouTube("a", "b", "c", log=lambda *_: None)
    s = yt.video_stats(["v"])
    assert s["v"]["views"] == 12 and s["v"]["likes"] == 3 and s["v"]["comments"] == 0
