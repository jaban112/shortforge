"""Instagram Reels via the Instagram Platform API ("Instagram API with Instagram
Login", host graph.instagram.com) — resumable upload, so NO public video URL is
needed: the bytes go straight to rupload.facebook.com.

Flow (all documented by Meta, content-publishing guide):
  1. POST /{ig_user_id}/media  media_type=REELS upload_type=resumable caption=...  -> {id, uri}
  2. POST {uri}  headers: Authorization: OAuth <token>, offset: 0, file_size: N  body: bytes
  3. GET  /{container_id}?fields=status_code  until FINISHED (ERROR/EXPIRED -> fail)
  4. POST /{ig_user_id}/media_publish  creation_id=<container_id>            -> {id}
Limits: 100 API-published posts per rolling 24h (GET /{ig_user_id}/content_publishing_limit).
Tokens: long-lived (60 days), refreshed with GET /refresh_access_token?grant_type=ig_refresh_token
(only if the token is older than 24h and not yet expired).

Video spec we already satisfy: MP4, H.264 4:2:0, AAC ≤48 kHz, 23–60 fps, ≤1920 columns,
9:16, moov atom in front (+faststart), no edit lists (-use_editlist 0), 3 s–15 min, ≤300 MB.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import requests

DEFAULT_HOST = "graph.instagram.com"
DEFAULT_VERSION = "v26.0"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload/{version}/{container_id}"


class InstagramError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None, subcode: int | None = None):
        super().__init__(msg)
        self.code, self.subcode = code, subcode


@dataclass
class ReelResult:
    container_id: str
    media_id: str
    permalink: str | None


def _err(r: requests.Response, what: str) -> InstagramError:
    try:
        e = r.json().get("error", {})
    except ValueError:
        e = {}
    msg = e.get("message") or r.text[:300]
    return InstagramError(f"{what}: HTTP {r.status_code} code={e.get('code')} subcode={e.get('error_subcode')} {msg}",
                          code=e.get("code"), subcode=e.get("error_subcode"))


class Instagram:
    def __init__(self, access_token: str, ig_user_id: str, host: str = DEFAULT_HOST, version: str = DEFAULT_VERSION,
                 log=print, poll_every: float = 10.0, poll_timeout: float = 900.0):
        self.token = access_token
        self.uid = ig_user_id
        self.base = f"https://{host}/{version}"
        self.version = version
        self.log = log
        self.poll_every = poll_every
        self.poll_timeout = poll_timeout

    # ---- account ----
    def me(self) -> dict:
        r = requests.get(f"{self.base}/me", params={"fields": "id,username,account_type", "access_token": self.token}, timeout=30)
        if r.status_code != 200:
            raise _err(r, "me")
        return r.json()

    def publishing_limit(self) -> dict:
        r = requests.get(f"{self.base}/{self.uid}/content_publishing_limit",
                         params={"fields": "quota_usage,config", "access_token": self.token}, timeout=30)
        if r.status_code != 200:
            raise _err(r, "content_publishing_limit")
        data = (r.json().get("data") or [{}])[0]
        return {"used": int(data.get("quota_usage", 0)), "limit": int((data.get("config") or {}).get("quota_total", 100))}

    # ---- publish ----
    def upload_reel(self, mp4: Path, caption: str, share_to_feed: bool = True, thumb_offset_ms: int = 1000) -> ReelResult:
        size = mp4.stat().st_size
        if size > 300 * 1024 * 1024:
            raise InstagramError(f"{mp4.name} is {size} bytes; Reels API max is 300 MB")
        # 1. container
        r = requests.post(f"{self.base}/{self.uid}/media", data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption[:2200],
            "share_to_feed": "true" if share_to_feed else "false",
            "thumb_offset": str(thumb_offset_ms),
            "access_token": self.token,
        }, timeout=60)
        if r.status_code != 200:
            raise _err(r, "create container")
        body = r.json()
        cid = str(body["id"])
        uri = body.get("uri") or RUPLOAD.format(version=self.version, container_id=cid)
        self.log(f"[ig] container {cid}; uploading {size} bytes")
        # 2. bytes
        with open(mp4, "rb") as f:
            up = requests.post(uri, headers={
                "Authorization": f"OAuth {self.token}",
                "offset": "0",
                "file_size": str(size),
                "Content-Type": "application/octet-stream",
            }, data=f, timeout=600)
        if up.status_code != 200:
            raise _err(up, "rupload")
        try:
            if not up.json().get("success", True):
                raise InstagramError(f"rupload reported failure: {up.text[:300]}")
        except ValueError:
            pass
        # 3. wait
        self._wait_finished(cid)
        # 4. publish
        p = requests.post(f"{self.base}/{self.uid}/media_publish", data={"creation_id": cid, "access_token": self.token}, timeout=60)
        if p.status_code != 200:
            raise _err(p, "media_publish")
        media_id = str(p.json()["id"])
        permalink = None
        try:
            g = requests.get(f"{self.base}/{media_id}", params={"fields": "permalink", "access_token": self.token}, timeout=30)
            if g.status_code == 200:
                permalink = g.json().get("permalink")
        except requests.RequestException:
            pass
        self.log(f"[ig] published media {media_id} {permalink or ''}")
        return ReelResult(container_id=cid, media_id=media_id, permalink=permalink)

    def _wait_finished(self, cid: str) -> None:
        t0 = time.time()
        while True:
            r = requests.get(f"{self.base}/{cid}", params={"fields": "status_code,status", "access_token": self.token}, timeout=30)
            if r.status_code != 200:
                raise _err(r, "container status")
            j = r.json()
            st = j.get("status_code")
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise InstagramError(f"container {cid} {st}: {j.get('status')}")
            if time.time() - t0 > self.poll_timeout:
                raise InstagramError(f"container {cid} still {st} after {self.poll_timeout:.0f}s")
            time.sleep(self.poll_every)

    # ---- tokens ----
    def refresh_token(self) -> tuple[str, int]:
        """Returns (new_token, expires_in_seconds). Meta requires the token be >24h old."""
        r = requests.get(f"https://{self.base.split('/')[2]}/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": self.token}, timeout=30)
        if r.status_code != 200:
            raise _err(r, "refresh_access_token")
        j = r.json()
        return j["access_token"], int(j.get("expires_in", 60 * 86400))


def exchange_short_lived(short_token: str, app_secret: str, host: str = DEFAULT_HOST) -> tuple[str, int]:
    """Short-lived (1h) -> long-lived (60d). Only needed if you did the OAuth code flow yourself;
    the App Dashboard 'Generate token' button already gives a long-lived token."""
    r = requests.get(f"https://{host}/access_token",
                     params={"grant_type": "ig_exchange_token", "client_secret": app_secret, "access_token": short_token}, timeout=30)
    if r.status_code != 200:
        raise _err(r, "ig_exchange_token")
    j = r.json()
    return j["access_token"], int(j.get("expires_in", 60 * 86400))
