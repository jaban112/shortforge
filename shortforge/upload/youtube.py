"""YouTube Data API v3 — resumable upload + stats, plain HTTP.

Quota: videos.insert costs 1,600 units; the default daily quota is 10,000, so at
most 6 uploads/day per project. videos.list / channels.list cost 1 unit each.

`status.containsSyntheticMedia = true` is set on every upload — YouTube's
altered/synthetic content disclosure — because the narration is AI-generated.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .auth import refresh_access_token

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
CHUNK = 8 * 1024 * 1024


class UploadError(RuntimeError):
    pass


class YouTube:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, log=print):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.log = log
        self._access: str | None = None
        self._access_at = 0.0

    def token(self) -> str:
        if self._access is None or time.time() - self._access_at > 3000:
            self._access = refresh_access_token(self.client_id, self.client_secret, self.refresh_token)
            self._access_at = time.time()
        return self._access

    def _h(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.token()}"}
        if extra:
            h.update(extra)
        return h

    # ---- upload ----
    def upload(self, mp4: Path, title: str, description: str, tags: list[str] | None, category_id: str = "27",
               privacy: str = "public", made_for_kids: bool = False, synthetic: bool = True) -> str:
        size = mp4.stat().st_size
        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": (tags or [])[:30],
                "categoryId": category_id,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": made_for_kids,
                "containsSyntheticMedia": synthetic,
                "license": "youtube",
                "embeddable": True,
            },
        }
        r = requests.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers=self._h({
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": "video/mp4",
            }),
            data=json.dumps(body),
            timeout=60,
        )
        if r.status_code != 200:
            raise UploadError(f"resumable session failed {r.status_code}: {r.text[:400]}")
        session = r.headers.get("Location")
        if not session:
            raise UploadError("no Location header for resumable session")
        return self._send(session, mp4, size)

    def _send(self, session: str, mp4: Path, size: int) -> str:
        sent = 0
        attempts = 0
        with open(mp4, "rb") as f:
            while sent < size:
                f.seek(sent)
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                try:
                    r = requests.put(
                        session,
                        headers=self._h({
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {sent}-{end}/{size}",
                            "Content-Type": "video/mp4",
                        }),
                        data=chunk,
                        timeout=300,
                    )
                except requests.RequestException as e:
                    attempts += 1
                    if attempts > 5:
                        raise UploadError(f"network failure during upload: {e}")
                    time.sleep(2 ** attempts)
                    sent = self._resume_offset(session, size)
                    continue
                if r.status_code in (200, 201):
                    vid = r.json().get("id")
                    if not vid:
                        raise UploadError(f"upload finished without id: {r.text[:300]}")
                    self.log(f"[yt] uploaded {mp4.name} -> https://youtube.com/shorts/{vid}")
                    return vid
                if r.status_code == 308:
                    rng = r.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else end + 1
                    continue
                if r.status_code in (500, 502, 503, 504):
                    attempts += 1
                    if attempts > 5:
                        raise UploadError(f"server error {r.status_code}: {r.text[:300]}")
                    time.sleep(2 ** attempts)
                    sent = self._resume_offset(session, size)
                    continue
                raise UploadError(f"upload failed {r.status_code}: {r.text[:400]}")
        raise UploadError("upload loop ended without a response")

    def _resume_offset(self, session: str, size: int) -> int:
        r = requests.put(session, headers=self._h({"Content-Length": "0", "Content-Range": f"bytes */{size}"}), timeout=60)
        if r.status_code == 308:
            rng = r.headers.get("Range")
            return int(rng.split("-")[1]) + 1 if rng else 0
        if r.status_code in (200, 201):
            return size
        return 0

    # ---- stats ----
    def video_stats(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            r = requests.get(VIDEOS_URL, params={"part": "statistics,status", "id": ",".join(batch)}, headers=self._h(), timeout=60)
            if r.status_code != 200:
                raise UploadError(f"videos.list failed {r.status_code}: {r.text[:300]}")
            for it in r.json().get("items", []):
                st = it.get("statistics", {})
                out[it["id"]] = {
                    "views": int(st.get("viewCount", 0)),
                    "likes": int(st.get("likeCount", 0)),
                    "comments": int(st.get("commentCount", 0)),
                    "privacy": it.get("status", {}).get("privacyStatus"),
                    "uploadStatus": it.get("status", {}).get("uploadStatus"),
                }
        return out

    def channel_stats(self) -> dict:
        r = requests.get(CHANNELS_URL, params={"part": "statistics,snippet", "mine": "true"}, headers=self._h(), timeout=60)
        if r.status_code != 200:
            raise UploadError(f"channels.list failed {r.status_code}: {r.text[:300]}")
        items = r.json().get("items", [])
        if not items:
            return {}
        c = items[0]
        st = c.get("statistics", {})
        return {
            "channel_id": c["id"],
            "title": c.get("snippet", {}).get("title"),
            "subscribers": int(st.get("subscriberCount", 0)),
            "views": int(st.get("viewCount", 0)),
            "videos": int(st.get("videoCount", 0)),
        }
