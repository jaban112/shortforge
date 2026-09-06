import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shortforge.sources.base import Item  # noqa: E402


@pytest.fixture
def item() -> Item:
    d = json.loads((ROOT / "fixtures" / "otd_1620.json").read_text(encoding="utf-8"))
    return Item(**d)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTFORGE_ROOT", str(tmp_path))
    monkeypatch.setenv("SHORTFORGE_MODELS", str(ROOT / "models"))
    monkeypatch.setenv("SHORTFORGE_ENV_FILE", str(tmp_path / "nonexistent.env"))
    for k in ("ANTHROPIC_API_KEY", "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from shortforge import config

    return config.load()


class FakeHttp:
    """Scripted responses keyed by URL substring."""

    def __init__(self, json_by_substr=None, bytes_by_substr=None):
        self.json_by_substr = json_by_substr or {}
        self.bytes_by_substr = bytes_by_substr or {}
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params))
        for k, v in self.json_by_substr.items():
            if k in url or (params and k in json.dumps(params)):
                return v() if callable(v) else v
        raise RuntimeError(f"unexpected GET {url} {params}")

    def get_bytes(self, url, headers=None):
        self.calls.append((url, None))
        for k, v in self.bytes_by_substr.items():
            if k in url:
                return v
        raise RuntimeError(f"unexpected GET bytes {url}")


@pytest.fixture
def fake_http():
    return FakeHttp
