"""Instagram token store for unattended runs.

GitHub Actions cannot rewrite its own secrets, but the token must be refreshed
every <60 days. So: the initial token comes from the IG_ACCESS_TOKEN secret; the
workflow refreshes it and writes state/ig_token.enc — Fernet-encrypted with the
IG_TOKEN_KEY secret — which is committed back with the ledger.

Precedence is deterministic: the file records `origin` = sha256 of the env token
it descends from. If the env token's hash matches, the file (newer, refreshed)
wins; if not, the env token was rotated by a human and wins.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


@dataclass
class IgToken:
    token: str
    obtained_at: float
    expires_at: float
    origin: str  # sha256 hex of the env token this descends from

    @property
    def age_s(self) -> float:
        return time.time() - self.obtained_at

    @property
    def remaining_s(self) -> float:
        return self.expires_at - time.time()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet(key: str) -> Fernet:
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def save(path: Path, tok: IgToken, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet(key).encrypt(json.dumps(tok.__dict__).encode("utf-8")))


def load_file(path: Path, key: str) -> IgToken | None:
    if not path.exists():
        return None
    try:
        return IgToken(**json.loads(_fernet(key).decrypt(path.read_bytes()).decode("utf-8")))
    except (InvalidToken, ValueError, TypeError):
        return None


def resolve(env_token: str | None, path: Path, key: str | None, env_expires_days: float = 60.0) -> IgToken | None:
    file_tok = load_file(path, key) if key else None
    if env_token:
        if file_tok and file_tok.origin == digest(env_token):
            return file_tok
        now = time.time()
        return IgToken(env_token, now, now + env_expires_days * 86400, digest(env_token))
    return file_tok
