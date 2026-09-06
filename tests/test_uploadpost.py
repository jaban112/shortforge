import json

import requests

from shortforge.upload.uploadpost import UploadPost


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._b = body
        self.text = json.dumps(body)

    def json(self):
        return self._b


def test_uploadpost_form_fields(monkeypatch, tmp_path):
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"0" * 100)
    seen = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        seen.update(url=url, headers=headers, data=data, files=files)
        return Resp(200, {"success": True, "request_id": "req_1"})

    monkeypatch.setattr(requests, "post", fake_post)
    up = UploadPost("KEY", log=lambda *_: None)
    res = up.upload(mp4, "main", ["youtube", "tiktok"], "T #Shorts", "D", ["a", "b"], privacy="unlisted")
    assert res.request_id == "req_1" and res.user == "main"
    assert seen["url"] == "https://api.upload-post.com/api/upload"
    assert seen["headers"]["Authorization"] == "Apikey KEY"
    d = seen["data"]
    assert ("user", "main") in d
    assert [v for k, v in d if k == "platform[]"] == ["youtube", "tiktok"]
    assert ("youtube_privacy_status", "UNLISTED") in d
    assert ("containsSyntheticMedia", "true") in d
    assert ("selfDeclaredMadeForKids", "false") in d
    assert [v for k, v in d if k == "tags[]"] == ["a", "b"]
    assert seen["files"]["video"][0] == "v.mp4"


def test_cli_uploadpost_fanout(cfg, monkeypatch, tmp_path):
    import shortforge.__main__ as cli
    from shortforge.ledger import Ledger
    from shortforge.upload import uploadpost as upm

    cfg.uploader = "uploadpost"
    cfg.uploadpost_api_key = "k"
    cfg.uploadpost_users = ["main", "second"]
    cfg.uploadpost_platforms = ["youtube", "tiktok"]
    calls = []

    class FakeUP:
        def __init__(self, *a, **k):
            pass

        def upload(self, mp4, user, platforms, title, description, tags=None, **kw):
            calls.append((user, tuple(platforms)))
            if user == "second":
                raise RuntimeError("quota")
            return upm.UploadPostResult(user=user, platforms=platforms, request_id="r1", raw={})

    monkeypatch.setattr(upm, "UploadPost", FakeUP)
    L = Ledger(cfg.ledger_path)
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"0")
    L.record_video("k1", mp4, 10.0, {"title": "t"})
    out = cli._upload_one(cfg, L, "k1", mp4, {"title": "t", "description": "d", "tags": ["x"]})
    assert out == "up:main=r1"
    assert calls == [("main", ("youtube", "tiktok")), ("second", ("youtube", "tiktok"))]
    row = L.db.execute("SELECT youtube_id, upload_error FROM videos WHERE video_key='k1'").fetchone()
    assert row[0] == "up:main=r1" and "second: quota" in row[1]
    assert L.pending_uploads() == []


def test_upload_ready_by_uploader(cfg):
    cfg.uploader = "uploadpost"
    assert not cfg.upload_ready
    cfg.uploadpost_api_key = "k"
    cfg.uploadpost_users = ["a"]
    assert cfg.upload_ready
