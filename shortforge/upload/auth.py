"""Google OAuth (installed-app / loopback) with no Google client library.

`shortforge auth` prints a URL, you open it in ANY browser, approve, and either
the local loopback server catches the redirect or you paste the redirect URL
back. The refresh token it prints goes into .env / GitHub secrets.

IMPORTANT: the OAuth consent screen must be set to "In production" (publishing
status). In "Testing" status Google expires refresh tokens after 7 days, which
would silently kill an unattended uploader.
"""
from __future__ import annotations

import http.server
import secrets
import threading
import urllib.parse
from dataclasses import dataclass

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_in: int


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    q = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> Tokens:
    r = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"token exchange failed {r.status_code}: {r.text[:300]}")
    d = r.json()
    return Tokens(d["access_token"], d.get("refresh_token"), int(d.get("expires_in", 3600)))


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    r = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"refresh failed {r.status_code}: {r.text[:300]} — if this says invalid_grant, the refresh token "
            "was revoked or expired (consent screen in Testing status expires tokens after 7 days). Run `shortforge auth` again."
        )
    return r.json()["access_token"]


class _Catcher(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.code = (q.get("code") or [None])[0]
        _Catcher.state = (q.get("state") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h2>shortforge: authorized. You can close this tab.</h2>")

    def log_message(self, *a):  # silence
        pass


def interactive_auth(client_id: str, client_secret: str, port: int = 8765, prompt=input, log=print) -> Tokens:
    redirect_uri = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(16)
    url = build_auth_url(client_id, redirect_uri, state)
    srv = None
    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), _Catcher)
        threading.Thread(target=srv.handle_request, daemon=True).start()
    except OSError:
        srv = None
    log("\nOpen this URL in a browser and approve:\n\n" + url + "\n")
    log("If the browser ends on a 'site can't be reached' page (normal when the browser runs outside this VM),")
    log("copy the FULL address bar URL (http://127.0.0.1:...?code=...) and paste it here.\n")
    code: str | None = None
    pasted = prompt("Paste redirect URL here (or press Enter if the page said 'authorized'): ").strip()
    if pasted:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        code = (q.get("code") or [None])[0]
        if (q.get("state") or [None])[0] != state:
            raise RuntimeError("state mismatch — start over")
    elif _Catcher.code:
        if _Catcher.state != state:
            raise RuntimeError("state mismatch — start over")
        code = _Catcher.code
    if srv is not None:
        srv.server_close()
    if not code:
        raise RuntimeError("no authorization code received")
    tok = exchange_code(client_id, client_secret, code, redirect_uri)
    if not tok.refresh_token:
        raise RuntimeError("Google returned no refresh_token — revoke the app at myaccount.google.com/permissions and run auth again")
    return tok
