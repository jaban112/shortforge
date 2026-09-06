"""End-to-end test of the single-file setup page against a mock GitHub/Google/Instagram API,
driven by headless Chromium from a file:// URL (the way the user will open it).

Verifies: repo create -> Actions permission -> full code push via git-data API (blob count ==
manifest) -> Google code exchange + channel check -> secrets sealed with the repo public key
(decrypted here with PyNaCl and compared) -> variables -> Instagram validation -> workflow dispatch
-> status rendering.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

nacl = pytest.importorskip("nacl.public")
playwright = pytest.importorskip("playwright.sync_api")

from web.build import build, manifest  # noqa: E402


class Mock:
    def __init__(self):
        self.priv = nacl.PrivateKey.generate()
        self.repo_exists = False
        self.blobs = {}
        self.trees = []
        self.commits = []
        self.ref = "base"
        self.secrets = {}
        self.variables = {}
        self.dispatches = []
        self.perm = None
        self.status_md = "# shortforge status\n\n- uploaded videos: **2**\n- subscribers: **7** / YPP target 1,000\n\n## Posts by platform\n\n| posted | platform | video | link / error |\n|---|---|---|---|\n| 2026-09-06 | youtube | otd:1 | [Y1](https://youtube.com/shorts/Y1) |\n"

    def public_key_b64(self):
        return base64.b64encode(bytes(self.priv.public_key)).decode()

    def open(self, name):
        return nacl.SealedBox(self.priv).decrypt(base64.b64decode(self.secrets[name]["encrypted_value"])).decode()


def make_handler(m: Mock):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=None, ctype="application/json"):
            self.send_response(code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
            if body is not None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_header("Content-Length", "0")
                self.end_headers()

        def do_OPTIONS(self):
            self._send(204)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            ct = self.headers.get("Content-Type", "")
            if "json" in ct:
                return json.loads(raw or b"{}")
            if "form" in ct:
                from urllib.parse import parse_qs
                return {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            return raw

        def do_GET(self):
            p = self.path.split("?")[0]
            q = self.path.split("?")[1] if "?" in self.path else ""
            if p == "/user":
                return self._send(200, {"login": "jiwan"})
            if p == "/repos/jiwan/shortforge":
                return self._send(200, {"name": "shortforge", "full_name": "jiwan/shortforge", "private": True, "default_branch": "main"}) if m.repo_exists else self._send(404, {"message": "Not Found"})
            if p.endswith("/git/ref/heads/main"):
                return self._send(200, {"object": {"sha": m.ref}})
            if "/git/commits/" in p:
                return self._send(200, {"tree": {"sha": "t0"}})
            if p.endswith("/actions/secrets/public-key"):
                return self._send(200, {"key_id": "k1", "key": m.public_key_b64()})
            if p.endswith("/actions/variables"):
                return self._send(200, {"variables": [{"name": k, "value": v} for k, v in m.variables.items()]})
            if p.endswith("/actions/runs"):
                return self._send(200, {"workflow_runs": [{"name": "shortforge daily", "status": "completed", "conclusion": "success", "created_at": "2026-09-06T13:00:00Z", "html_url": "https://github.com/x"}]})
            if p.endswith("/contents/status/README.md"):
                return self._send(200, {"content": base64.b64encode(m.status_md.encode()).decode()})
            if p == "/yt/channels":
                return self._send(200, {"items": [{"snippet": {"title": "Today in History"}, "statistics": {"subscriberCount": "7", "videoCount": "2"}}]})
            if p == "/ig/me":
                return self._send(200, {"id": "177", "username": "todayinhistory", "account_type": "MEDIA_CREATOR"})
            if p == "/ig/177/content_publishing_limit":
                return self._send(200, {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]})
            return self._send(404, {"message": f"no route {p}"})

        def do_POST(self):
            p = self.path.split("?")[0]
            b = self._body()
            if p == "/user/repos":
                m.repo_exists = True
                return self._send(201, {"name": b["name"], "full_name": f"jiwan/{b['name']}", "private": b["private"], "default_branch": "main"})
            if p.endswith("/git/blobs"):
                sha = hashlib.sha1(b["content"].encode()).hexdigest()
                m.blobs[sha] = b
                return self._send(201, {"sha": sha})
            if p.endswith("/git/trees"):
                m.trees.append(b)
                return self._send(201, {"sha": "t1"})
            if p.endswith("/git/commits"):
                m.commits.append(b)
                return self._send(201, {"sha": "c1"})
            if p.endswith("/actions/variables"):
                if b["name"] in m.variables:
                    return self._send(409, {"message": "exists"})
                m.variables[b["name"]] = b["value"]
                return self._send(201, {})
            if p.endswith("/dispatches"):
                m.dispatches.append(b)
                return self._send(204)
            if p == "/gtoken":
                assert b["grant_type"] == "authorization_code" and b["code"] == "CODE1"
                return self._send(200, {"access_token": "AT", "refresh_token": "RT1", "expires_in": 3600})
            return self._send(404, {"message": f"no route {p}"})

        def do_PUT(self):
            p = self.path.split("?")[0]
            b = self._body()
            if p.endswith("/actions/permissions/workflow"):
                m.perm = b
                return self._send(204)
            if "/actions/secrets/" in p:
                m.secrets[p.rsplit("/", 1)[1]] = b
                return self._send(201)
            return self._send(404, {"message": f"no route {p}"})

        def do_PATCH(self):
            p = self.path.split("?")[0]
            b = self._body()
            if p.endswith("/git/refs/heads/main"):
                m.ref = b["sha"]
                return self._send(200, {})
            if "/actions/variables/" in p:
                m.variables[b["name"]] = b["value"]
                return self._send(204)
            return self._send(404, {"message": f"no route {p}"})

    return H


@pytest.fixture(scope="module")
def mock():
    m = Mock()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(m))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    m.port = srv.server_port
    yield m
    srv.shutdown()


@pytest.fixture(scope="module")
def page_path(tmp_path_factory):
    out = tmp_path_factory.mktemp("web") / "shortforge-setup.html"
    build(out)
    return out


def test_setup_page_end_to_end(mock, page_path):
    base = f"http://127.0.0.1:{mock.port}"
    url = f"file://{page_path}?api={base}&gtoken={base}/gtoken&ytapi={base}/yt&igapi={base}/ig"
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.wait_for_function("window.sodiumReady !== undefined")
        # 1. GitHub connect + push
        pg.fill("#gh-token", "ghp_test")
        pg.click("#gh-connect")
        pg.wait_for_function("document.getElementById('c-gh').textContent === 'jiwan/shortforge'", timeout=15000)
        assert mock.repo_exists and mock.perm == {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": False}
        pg.click("#gh-push")
        pg.wait_for_function("document.getElementById('gh-log').textContent.includes('푸시 완료')", timeout=60000)
        man = manifest()
        assert len(mock.trees[0]["tree"]) == len(man)  # identical empty files share a blob sha
        tree_paths = sorted(e["path"] for e in mock.trees[0]["tree"])
        assert tree_paths == sorted(man)
        assert ".github/workflows/daily.yml" in tree_paths and "shortforge/upload/instagram.py" in tree_paths
        assert mock.commits[0]["parents"] == ["base"] and mock.ref == "c1"
        # 2. YouTube: exchange pasted redirect URL
        pg.fill("#yt-cid", "cid.apps.googleusercontent.com")
        pg.fill("#yt-csec", "GOCSPX-x")
        pg.evaluate("sessionStorage.setItem('sf.ytstate','ST')")
        pg.fill("#yt-redirect", "http://127.0.0.1:8765/?state=ST&code=CODE1&scope=x")
        pg.evaluate("document.getElementById('yt-exchange').disabled=false")
        pg.click("#yt-exchange")
        pg.wait_for_function("document.getElementById('c-yt').textContent === 'Today in History'", timeout=15000)
        pg.click("#yt-save")
        pg.wait_for_function("document.getElementById('yt-log').textContent.includes('secrets 3개 저장')", timeout=15000)
        assert mock.open("YT_CLIENT_ID") == "cid.apps.googleusercontent.com"
        assert mock.open("YT_CLIENT_SECRET") == "GOCSPX-x"
        assert mock.open("YT_REFRESH_TOKEN") == "RT1"
        assert mock.secrets["YT_REFRESH_TOKEN"]["key_id"] == "k1"
        assert mock.variables["SHORTFORGE_UPLOADER"] == "youtube"
        # 3. Instagram
        pg.fill("#ig-token", "IGAAtoken")
        pg.click("#ig-check")
        pg.wait_for_function("document.getElementById('c-ig').textContent === '@todayinhistory'", timeout=15000)
        pg.click("#ig-save")
        pg.wait_for_function("document.getElementById('ig-log').textContent.includes('IG_USER_ID 저장')", timeout=15000)
        assert mock.open("IG_ACCESS_TOKEN") == "IGAAtoken"
        assert len(mock.open("IG_TOKEN_KEY")) >= 32
        assert mock.variables["IG_USER_ID"] == "177"
        assert mock.variables["SHORTFORGE_UPLOADER"] == "youtube,instagram"
        # 4. settings
        pg.select_option("#cfg-privacy", "unlisted")
        pg.fill("#cfg-anthropic", "sk-ant-test")
        pg.click("#cfg-save")
        pg.wait_for_function("document.getElementById('cfg-log').textContent.includes('variables 5개 저장')", timeout=15000)
        assert mock.variables["SHORTFORGE_PRIVACY"] == "unlisted" and mock.variables["SHORTFORGE_PACK"] == "onthisday"
        assert mock.open("ANTHROPIC_API_KEY") == "sk-ant-test"
        # 5. run + status
        pg.click("#run-now")
        pg.wait_for_function("document.getElementById('run-log').textContent.includes('실행 요청됨')", timeout=15000)
        assert mock.dispatches[0]["ref"] == "main" and mock.dispatches[0]["inputs"]["upload"] == "true"
        pg.click("#run-refresh")
        pg.wait_for_function("document.getElementById('run-kv').textContent.includes('uploaded videos')", timeout=15000)
        assert "success" in pg.inner_text("#run-status")
        assert "Y1" in pg.inner_text("#run-log")
        assert errors == [], errors
        b.close()


def test_manifest_excludes_secrets_and_binaries():
    m = manifest()
    names = set(m)
    assert "pyproject.toml" in names and "shortforge/__main__.py" in names and ".github/workflows/daily.yml" in names
    assert not any(n.startswith(("out/", "work/", "models/", ".git/")) for n in names)
    assert "web/app.html" in names and "web/build.py" in names and "web/sodium.bundle.js" not in names
    assert not any(n.endswith((".mp4", ".onnx", ".sqlite", ".enc", ".pyc")) for n in names)
    assert ".env" not in names and ".env.example" in names
