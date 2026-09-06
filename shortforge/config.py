"""Configuration: environment variables + paths. No hidden defaults that affect money.

Every knob is listed here with its env var so the GitHub Actions secrets list
and the local .env are the single source of truth.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v is not None else default


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v is not None else default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # --- paths ---
    root: Path = field(default_factory=lambda: Path(_env("SHORTFORGE_ROOT", os.getcwd())))
    workdir: Path = field(default_factory=lambda: Path(_env("SHORTFORGE_WORKDIR", "work")))
    outdir: Path = field(default_factory=lambda: Path(_env("SHORTFORGE_OUTDIR", "out")))
    ledger_path: Path = field(default_factory=lambda: Path(_env("SHORTFORGE_LEDGER", "state/ledger.sqlite")))
    models_dir: Path = field(default_factory=lambda: Path(_env("SHORTFORGE_MODELS", "models")))

    # --- content ---
    pack: str = field(default_factory=lambda: _env("SHORTFORGE_PACK", "onthisday"))
    language: str = field(default_factory=lambda: _env("SHORTFORGE_LANG", "en"))
    channel_name: str = field(default_factory=lambda: _env("SHORTFORGE_CHANNEL_NAME", "Today in History"))
    videos_per_run: int = field(default_factory=lambda: _env_int("SHORTFORGE_VIDEOS_PER_RUN", 1))
    target_seconds: float = field(default_factory=lambda: _env_float("SHORTFORGE_TARGET_SECONDS", 45.0))
    max_seconds: float = field(default_factory=lambda: _env_float("SHORTFORGE_MAX_SECONDS", 58.0))

    # --- writer ---
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("SHORTFORGE_ANTHROPIC_MODEL", "claude-haiku-4-5"))
    llm_attempts: int = field(default_factory=lambda: _env_int("SHORTFORGE_LLM_ATTEMPTS", 3))

    # --- tts ---
    tts_voice: str = field(default_factory=lambda: _env("SHORTFORGE_VOICE", "am_michael"))
    tts_speed: float = field(default_factory=lambda: _env_float("SHORTFORGE_SPEED", 1.05))

    # --- render ---
    width: int = 1080
    height: int = 1920
    fps: int = 30
    font_path: str = field(default_factory=lambda: _env("SHORTFORGE_FONT", ""))
    music_gain_db: float = field(default_factory=lambda: _env_float("SHORTFORGE_MUSIC_GAIN_DB", -22.0))
    music_enabled: bool = field(default_factory=lambda: _env_bool("SHORTFORGE_MUSIC", True))

    # --- upload ---
    upload: bool = field(default_factory=lambda: _env_bool("SHORTFORGE_UPLOAD", False))
    uploader: str = field(default_factory=lambda: _env("SHORTFORGE_UPLOADER", "youtube"))  # youtube | uploadpost
    uploadpost_api_key: str | None = field(default_factory=lambda: _env("UPLOAD_POST_API_KEY"))
    uploadpost_users: list[str] = field(default_factory=lambda: [u.strip() for u in (_env("UPLOAD_POST_USERS", "") or "").split(",") if u.strip()])
    uploadpost_platforms: list[str] = field(default_factory=lambda: [u.strip() for u in (_env("UPLOAD_POST_PLATFORMS", "youtube") or "").split(",") if u.strip()])
    uploadpost_header: str = field(default_factory=lambda: _env("UPLOAD_POST_HEADER_SCHEME", "Apikey"))
    yt_client_id: str | None = field(default_factory=lambda: _env("YT_CLIENT_ID"))
    yt_client_secret: str | None = field(default_factory=lambda: _env("YT_CLIENT_SECRET"))
    yt_refresh_token: str | None = field(default_factory=lambda: _env("YT_REFRESH_TOKEN"))
    yt_privacy: str = field(default_factory=lambda: _env("SHORTFORGE_PRIVACY", "public"))
    yt_category_id: str = field(default_factory=lambda: _env("SHORTFORGE_CATEGORY", "27"))  # 27 = Education

    # --- misc ---
    nasa_api_key: str = field(default_factory=lambda: _env("NASA_API_KEY", "DEMO_KEY"))
    user_agent: str = field(
        default_factory=lambda: _env(
            "SHORTFORGE_USER_AGENT",
            "shortforge/1.0 (https://github.com/; automated educational shorts)",
        )
    )
    seed: int | None = field(default_factory=lambda: (int(_env("SHORTFORGE_SEED")) if _env("SHORTFORGE_SEED") else None))

    def resolve(self) -> "Config":
        """Make relative paths absolute against root and create directories."""
        for attr in ("workdir", "outdir", "ledger_path", "models_dir"):
            p = getattr(self, attr)
            if not p.is_absolute():
                p = self.root / p
            setattr(self, attr, p)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def upload_ready(self) -> bool:
        if self.uploader == "uploadpost":
            return bool(self.uploadpost_api_key and self.uploadpost_users and self.uploadpost_platforms)
        return bool(self.yt_client_id and self.yt_client_secret and self.yt_refresh_token)


def load() -> Config:
    """Load .env (if present, without overriding real env) then build Config."""
    env_file = Path(os.environ.get("SHORTFORGE_ENV_FILE", ".env"))
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)
    return Config().resolve()
