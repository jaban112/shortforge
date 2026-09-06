"""Tiny HTTP wrapper: one session, honest User-Agent, bounded retries, no silent failures."""
from __future__ import annotations

import time
from typing import Any

import requests


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.url, self.status, self.body = url, status, body


class Http:
    def __init__(self, user_agent: str, timeout: float = 30.0, retries: int = 3):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent
        self.timeout = timeout
        self.retries = retries

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        r = self._get(url, params=params, headers=headers)
        return r.json()

    def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        return self._get(url, headers=headers).content

    def _get(self, url: str, **kw: Any) -> requests.Response:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = self.s.get(url, timeout=self.timeout, **kw)
            except requests.RequestException as e:  # network-level
                last = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                last = HttpError(url, r.status_code, r.text)
                time.sleep(2.0 * (attempt + 1))
                continue
            raise HttpError(url, r.status_code, r.text)
        assert last is not None
        raise last
