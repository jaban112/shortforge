"""SQLite ledger: what was used, what was rendered, what was uploaded.

This is the memory of the unattended loop. It is committed back to the repo by
the GitHub Actions workflow so runs never repeat an item.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY,
  pack TEXT NOT NULL,
  title TEXT NOT NULL,
  used_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
  video_key TEXT PRIMARY KEY,      -- item_id
  path TEXT NOT NULL,
  seconds REAL NOT NULL,
  script_json TEXT NOT NULL,
  rendered_at REAL NOT NULL,
  youtube_id TEXT,
  uploaded_at REAL,
  upload_error TEXT
);
CREATE TABLE IF NOT EXISTS stats (
  youtube_id TEXT NOT NULL,
  fetched_at REAL NOT NULL,
  views INTEGER, likes INTEGER, comments INTEGER,
  PRIMARY KEY (youtube_id, fetched_at)
);
CREATE TABLE IF NOT EXISTS posts (
  video_key TEXT NOT NULL,
  platform TEXT NOT NULL,           -- youtube | instagram | uploadpost
  remote_id TEXT,
  url TEXT,
  posted_at REAL,
  error TEXT,
  PRIMARY KEY (video_key, platform)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at REAL NOT NULL,
  finished_at REAL,
  pack TEXT,
  ok INTEGER,
  note TEXT
);
"""


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript(SCHEMA)

    # ---- items ----
    def is_used(self, item_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM items WHERE item_id=?", (item_id,)).fetchone() is not None

    def mark_used(self, item_id: str, pack: str, title: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO items(item_id,pack,title,used_at) VALUES(?,?,?,?)",
            (item_id, pack, title, time.time()),
        )
        self.db.commit()

    # ---- videos ----
    def record_video(self, key: str, path: Path, seconds: float, script: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO videos(video_key,path,seconds,script_json,rendered_at) VALUES(?,?,?,?,?)",
            (key, str(path), seconds, json.dumps(script, ensure_ascii=False), time.time()),
        )
        self.db.commit()

    def record_upload(self, key: str, youtube_id: str | None, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE videos SET youtube_id=?, uploaded_at=?, upload_error=? WHERE video_key=?",
            (youtube_id, time.time() if youtube_id else None, error, key),
        )
        self.db.commit()

    def pending_uploads(self) -> list[tuple[str, str, str]]:
        """(key, path, script_json) for rendered-but-not-uploaded videos."""
        return self.db.execute(
            "SELECT video_key, path, script_json FROM videos WHERE youtube_id IS NULL ORDER BY rendered_at"
        ).fetchall()

    def uploaded(self) -> list[tuple[str, str, str, float]]:
        return self.db.execute(
            "SELECT video_key, youtube_id, script_json, uploaded_at FROM videos WHERE youtube_id IS NOT NULL ORDER BY uploaded_at DESC"
        ).fetchall()

    # ---- posts (per platform) ----
    def record_post(self, key: str, platform: str, remote_id: str | None, url: str | None = None, error: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO posts(video_key,platform,remote_id,url,posted_at,error) VALUES(?,?,?,?,?,?)",
            (key, platform, remote_id, url, time.time() if remote_id else None, error),
        )
        if platform == "youtube" and remote_id:
            self.db.execute("UPDATE videos SET youtube_id=?, uploaded_at=?, upload_error=NULL WHERE video_key=?", (remote_id, time.time(), key))
        self.db.commit()

    def pending_for(self, platform: str) -> list[tuple[str, str, str]]:
        """(key, path, script_json) rendered videos with no successful post on `platform`."""
        return self.db.execute(
            "SELECT v.video_key, v.path, v.script_json FROM videos v "
            "LEFT JOIN posts p ON p.video_key=v.video_key AND p.platform=? AND p.remote_id IS NOT NULL "
            "WHERE p.remote_id IS NULL ORDER BY v.rendered_at", (platform,)
        ).fetchall()

    def posts(self) -> list[tuple[str, str, str, str, float, str]]:
        return self.db.execute("SELECT video_key, platform, remote_id, url, posted_at, error FROM posts ORDER BY posted_at DESC").fetchall()

    # ---- stats ----
    def record_stats(self, youtube_id: str, views: int, likes: int, comments: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO stats(youtube_id,fetched_at,views,likes,comments) VALUES(?,?,?,?,?)",
            (youtube_id, time.time(), views, likes, comments),
        )
        self.db.commit()

    def latest_stats(self) -> dict[str, tuple[int, int, int, float]]:
        out: dict[str, tuple[int, int, int, float]] = {}
        for yid, t, v, l, c in self.db.execute(
            "SELECT youtube_id, MAX(fetched_at), views, likes, comments FROM stats GROUP BY youtube_id"
        ):
            out[yid] = (v or 0, l or 0, c or 0, t)
        return out

    # ---- runs ----
    def start_run(self, pack: str) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at,pack) VALUES(?,?)", (time.time(), pack))
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, ok: bool, note: str = "") -> None:
        self.db.execute("UPDATE runs SET finished_at=?, ok=?, note=? WHERE run_id=?", (time.time(), int(ok), note, run_id))
        self.db.commit()

    def recent_runs(self, n: int = 30) -> list[tuple]:
        return self.db.execute(
            "SELECT run_id, started_at, finished_at, pack, ok, note FROM runs ORDER BY run_id DESC LIMIT ?", (n,)
        ).fetchall()

    def close(self) -> None:
        self.db.close()
