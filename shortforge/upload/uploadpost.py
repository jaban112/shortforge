"""Upload-Post (api.upload-post.com) adapter — no Google Cloud project needed.

You connect each channel/account ONCE in Upload-Post's dashboard ("Login with
Google → Allow", same for TikTok/Instagram/Facebook), grouped under a *profile*
(`user`). Then one multipart POST per profile fans the video out to every
platform in `platform[]`. Free tier: 10 uploads/month; paid from ~$16/mo.

Built from their OpenAPI spec (api.upload-post.com/api, POST /upload,
GET /uploadposts/status). NOT exercised live from the build container (no egress);
`shortforge doctor` checks reachability and the key in your environment.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import requests

BASE = "https://api.upload-post.com/api"


class UploadPostError(RuntimeError):
    pass


@dataclass
class UploadPostResult:
    user: str
    platforms: list[str]
    request_id: str | None
    raw: dict


class UploadPost:
    def __init__(self, api_key: str, header_scheme: str = "Apikey", log=print, timeout: float = 600.0):
        self.api_key = api_key
        self.scheme = header_scheme
        self.log = log
        self.timeout = timeout

    def _h(self) -> dict:
        return {"Authorization": f"{self.scheme} {self.api_key}"}

    def upload(self, mp4: Path, user: str, platforms: list[str], title: str, description: str,
               tags: list[str] | None = None, privacy: str = "public", category_id: str = "27",
               synthetic: bool = True, async_upload: bool = True) -> UploadPostResult:
        data: list[tuple[str, str]] = [
            ("user", user),
            ("title", title[:100]),
            ("description", description[:5000]),
            ("youtube_title", title[:100]),
            ("youtube_description", description[:5000]),
            ("youtube_privacy_status", privacy.upper()),
            ("categoryId", category_id),
            ("selfDeclaredMadeForKids", "false"),
            ("containsSyntheticMedia", "true" if synthetic else "false"),
            ("embeddable", "true"),
            ("async_upload", "true" if async_upload else "false"),
        ]
        for p in platforms:
            data.append(("platform[]", p))
        for t in (tags or [])[:30]:
            data.append(("tags[]", t))
        with open(mp4, "rb") as f:
            r = requests.post(f"{BASE}/upload", headers=self._h(), data=data,
                              files={"video": (mp4.name, f, "video/mp4")}, timeout=self.timeout)
        if r.status_code not in (200, 202):
            raise UploadPostError(f"upload-post {r.status_code}: {r.text[:400]}")
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:400]}
        rid = body.get("request_id") or body.get("job_id")
        self.log(f"[upload-post] {mp4.name} -> user={user} platforms={platforms} request_id={rid}")
        return UploadPostResult(user=user, platforms=platforms, request_id=rid, raw=body)

    def status(self, request_id: str) -> dict:
        r = requests.get(f"{BASE}/uploadposts/status", headers=self._h(), params={"request_id": request_id}, timeout=60)
        if r.status_code != 200:
            raise UploadPostError(f"status {r.status_code}: {r.text[:300]}")
        return r.json()

    def wait(self, request_id: str, timeout_s: float = 900.0, every: float = 15.0) -> dict:
        t0 = time.time()
        last: dict = {}
        while time.time() - t0 < timeout_s:
            last = self.status(request_id)
            st = str(last.get("status") or last.get("state") or "").lower()
            if st in {"completed", "success", "done", "failed", "error"}:
                return last
            time.sleep(every)
        return last
