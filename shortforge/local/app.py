"""shortforge local — the one-click site. `python -m shortforge web` → http://127.0.0.1:8787

No API keys, no cloud projects: the page opens a real Chromium, you sign in to YouTube
and Instagram once, and from then on the built-in scheduler renders and posts on a timer
while this process runs (a systemd user service keeps it running on the Chromebook).

Pieces: JSON API over stdlib http.server · background runner (one job at a time) ·
scheduler thread (HH:MM slots, once per slot per day) · ring-buffer log · state/local.json.
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .. import config
from ..ledger import Ledger

UI_PATH = Path(__file__).with_name("ui.html")
DEFAULT_STATE = {
    "schedule": {"enabled": False, "times": ["09:00", "21:00"], "platforms": ["youtube"], "last_run": {}},
    "settings": {"pack": "onthisday", "privacy": "public", "channel_name": "Today in History", "videos_per_run": 1, "voice": "am_michael"},
    "accounts": {"youtube": {"ok": None, "checked_at": None}, "instagram": {"ok": None, "checked_at": None}},
}


class LocalApp:
    def __init__(self, cfg, port: int = 8787, browser=None, runner=None, log_path: Path | None = None):
        self.cfg = cfg
        self.port = port
        self.state_path = cfg.root / "state" / "local.json"
        self.state = self._load_state()
        self.logs: deque[str] = deque(maxlen=800)
        self.log_path = log_path or (cfg.root / "state" / "local.log")
        self.lock = threading.Lock()
        self.running: str | None = None  # description of the job in flight
        self.browser = browser  # injected for tests; created lazily otherwise
        self.runner = runner or self._default_runner
        self._stop = threading.Event()

    # ---------- state / log ----------
    def _load_state(self) -> dict:
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            s = {}
        merged = json.loads(json.dumps(DEFAULT_STATE))
        for k, v in s.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def log(self, msg: str) -> None:
        line = f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}"
        self.logs.append(line)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        print(line, flush=True)

    # ---------- browser ----------
    def _browser(self):
        if self.browser is None:
            from .browser import Browser

            self.browser = Browser(self.cfg.root / "state" / "chrome-profile", log=self.log)
        return self.browser

    def open_login(self, platform: str) -> str:
        url = {"youtube": "https://studio.youtube.com", "instagram": "https://www.instagram.com/accounts/login/"}[platform]
        b = self._browser()
        b.ensure_running()
        try:
            page = b.open_url(url)
            page.bring_to_front()
        finally:
            b.close_connection()
        self.log(f"[login] opened {url} — sign in in that window, then press '확인'")
        return url

    def check_accounts(self) -> dict:
        from .ig_web import InstagramWeb
        from .yt_web import YouTubeWeb

        b = self._browser()
        out = {}
        try:
            ctx = b.connect()
            page = ctx.new_page()
            try:
                out["youtube"] = bool(YouTubeWeb(page, self.cfg.workdir / "ui", log=self.log).logged_in())
            except Exception as e:
                self.log(f"[check] youtube: {e}")
                out["youtube"] = False
            try:
                out["instagram"] = bool(InstagramWeb(page, self.cfg.workdir / "ui", log=self.log).logged_in())
            except Exception as e:
                self.log(f"[check] instagram: {e}")
                out["instagram"] = False
            page.close()
        finally:
            b.close_connection()
        now = time.time()
        for k, v in out.items():
            self.state["accounts"][k] = {"ok": v, "checked_at": now}
        self.save_state()
        self.log(f"[check] youtube={'OK' if out.get('youtube') else 'NO'} instagram={'OK' if out.get('instagram') else 'NO'}")
        return out

    # ---------- jobs ----------
    def start_job(self, name: str, fn) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = name

        def wrap():
            try:
                fn()
            except Exception as e:
                self.log(f"[job] {name} FAILED: {e}")
            finally:
                with self.lock:
                    self.running = None

        threading.Thread(target=wrap, daemon=True).start()
        return True

    def _default_runner(self, platforms: list[str], n: int) -> None:
        """Render n videos with the normal pipeline and post each through the web uploaders."""
        from .. import __main__ as cli
        from ..http import Http
        from ..pipeline import produce
        from ..sources import get_pack
        from ..tts import KokoroTTS
        from .posters import register_web_posters

        cfg = self.cfg
        s = self.state["settings"]
        cfg.pack, cfg.yt_privacy, cfg.channel_name, cfg.tts_voice = s["pack"], s["privacy"], s["channel_name"], s["voice"]
        register_web_posters(self)
        ledger = Ledger(cfg.ledger_path)
        run_id = ledger.start_run(cfg.pack)
        made = 0
        try:
            # 1) anything rendered earlier but not yet posted (e.g. a UI hiccup last time) goes first
            for pl in [f"{x}-web" for x in platforms]:
                for key, path, _ in ledger.pending_for(pl):
                    mp = Path(path)
                    if mp.exists() and mp.with_suffix(".json").exists():
                        self.log(f"[run] retrying pending {key} → {pl}")
                        cli._upload_all(cfg, ledger, key, mp, json.loads(mp.with_suffix(".json").read_text(encoding="utf-8")), platforms=[pl])
            # 2) new material
            http = Http(cfg.user_agent)
            items = [it for it in get_pack(cfg.pack).fetch(http, cfg, dt.date.today()) if not ledger.is_used(it.id)]
            self.log(f"[run] {cfg.pack}: {len(items)} unused items")
            if not items:
                ledger.finish_run(run_id, False, "no unused items")
                return
            tts = KokoroTTS(cfg.models_dir, cfg.tts_voice, cfg.tts_speed, log=self.log)
            for item in items:
                if made >= n:
                    break
                try:
                    p = produce(item, cfg, http, tts, log=self.log)
                except Exception as e:
                    self.log(f"[run] produce failed {item.id}: {e}")
                    continue
                ledger.mark_used(item.id, item.pack, item.title)
                meta = json.loads(p.meta_json.read_text(encoding="utf-8"))
                meta["item"] = item.__dict__ | {"image": (item.image.__dict__ if item.image else None)}
                p.meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                ledger.record_video(item.id, p.mp4, p.seconds, p.script.to_dict())
                made += 1
                self.log(f"[run] rendered {p.mp4.name} ({p.seconds:.1f}s) — {p.script.title}")
                cli._upload_all(cfg, ledger, item.id, p.mp4, meta, platforms=[f"{pl}-web" for pl in platforms])
            ledger.finish_run(run_id, made >= 1, f"rendered {made}")
        except Exception as e:
            ledger.finish_run(run_id, False, f"fatal: {e}"[:400])
            raise
        finally:
            ledger.close()

    def retry(self, key: str, platform: str) -> bool:
        """Re-post one rendered video to one platform (e.g. after a UI failure)."""
        from .. import __main__ as cli
        from .posters import register_web_posters

        def job():
            register_web_posters(self)
            ledger = Ledger(self.cfg.ledger_path)
            try:
                row = ledger.db.execute("SELECT path FROM videos WHERE video_key=?", (key,)).fetchone()
                if not row or not Path(row[0]).exists():
                    self.log(f"[retry] {key}: video file missing")
                    return
                meta_path = Path(row[0]).with_suffix(".json")
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                pl = platform if platform.endswith("-web") or platform in cli.POSTERS else platform + "-web"
                self.log(f"[retry] {key} → {pl}")
                cli._upload_all(self.cfg, ledger, key, Path(row[0]), meta, platforms=[pl])
            finally:
                ledger.close()

        return self.start_job("retry", job)

    def post_pending(self) -> bool:
        """Post every rendered video that has no successful post on a scheduled platform."""
        from .. import __main__ as cli
        from .posters import register_web_posters

        def job():
            register_web_posters(self)
            ledger = Ledger(self.cfg.ledger_path)
            try:
                n = 0
                for pl in [f"{x}-web" for x in self.state["schedule"]["platforms"]]:
                    for key, path, _ in ledger.pending_for(pl):
                        mp = Path(path)
                        if not mp.exists() or not mp.with_suffix(".json").exists():
                            continue
                        meta = json.loads(mp.with_suffix(".json").read_text(encoding="utf-8"))
                        if cli._upload_all(self.cfg, ledger, key, mp, meta, platforms=[pl]).get(pl):
                            n += 1
                self.log(f"[pending] posted {n}")
            finally:
                ledger.close()

        return self.start_job("pending", job)

    def run_now(self, platforms: list[str] | None = None, n: int | None = None) -> bool:
        platforms = platforms or self.state["schedule"]["platforms"]
        n = n or int(self.state["settings"]["videos_per_run"])
        return self.start_job("run", lambda: self.runner(platforms, n))

    # ---------- scheduler ----------
    def scheduler_tick(self, now: dt.datetime | None = None) -> bool:
        """Fire at most one slot; returns True if a job was started. Deterministic on (date, slot)."""
        now = now or dt.datetime.now()
        sch = self.state["schedule"]
        if not sch.get("enabled"):
            return False
        for slot in sch.get("times", []):
            try:
                hh, mm = (int(x) for x in slot.split(":"))
            except ValueError:
                continue
            slot_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            key = f"{now.date().isoformat()}T{slot}"
            if now >= slot_dt and (now - slot_dt) < dt.timedelta(hours=6) and sch["last_run"].get(key) is None:
                if self.running:
                    return False
                sch["last_run"][key] = time.time()
                for k in list(sch["last_run"]):  # keep the map small
                    if k < (now - dt.timedelta(days=3)).date().isoformat():
                        del sch["last_run"][k]
                self.save_state()
                self.log(f"[sched] slot {slot} → run")
                return self.run_now()
        return False

    def scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scheduler_tick()
            except Exception as e:
                self.log(f"[sched] {e}")
            self._stop.wait(30)

    # ---------- snapshot for the UI ----------
    def snapshot(self) -> dict:
        ledger = Ledger(self.cfg.ledger_path)
        try:
            posts = [{"key": k, "platform": pl, "id": rid, "url": u, "at": t, "error": err} for k, pl, rid, u, t, err in ledger.posts()[:50]]
            runs = [{"id": rid, "started": st, "finished": fi, "pack": pk, "ok": ok, "note": note} for rid, st, fi, pk, ok, note in ledger.recent_runs(20)]
            videos = ledger.db.execute("SELECT video_key, path, seconds, script_json, rendered_at FROM videos ORDER BY rendered_at DESC LIMIT 30").fetchall()
            pending = {pl: [k for k, _, _ in ledger.pending_for(f"{pl}-web")] for pl in self.state["schedule"]["platforms"]}
        finally:
            ledger.close()
        vids = []
        for k, path, sec, sj, at in videos:
            try:
                title = json.loads(sj).get("title", k)
            except Exception:
                title = k
            vids.append({"key": k, "title": title, "seconds": sec, "at": at, "exists": Path(path).exists(), "thumb": Path(path).with_suffix(".jpg").exists()})
        shots = sorted((self.cfg.workdir / "ui").glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:6] if (self.cfg.workdir / "ui").exists() else []
        return {
            "state": self.state,
            "running": self.running,
            "posts": posts,
            "runs": runs,
            "videos": vids,
            "pending": pending,
            "shots": [s.name for s in shots],
            "logs": list(self.logs)[-200:],
            "now": time.time(),
            "browser": bool(self.browser and getattr(self.browser, "exe", None)),
        }

    # ---------- HTTP ----------
    def serve(self, open_browser: bool = True) -> None:
        app = self
        threading.Thread(target=self.scheduler_loop, daemon=True).start()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code: int, obj) -> None:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _file(self, path: Path, ctype: str | None = None) -> None:
                if not path.exists():
                    return self._json(404, {"error": "not found"})
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/":
                    return self._file(UI_PATH, "text/html; charset=utf-8")
                if u.path == "/api/state":
                    return self._json(200, app.snapshot())
                if u.path.startswith("/api/shot/"):
                    name = os.path.basename(u.path)
                    return self._file(app.cfg.workdir / "ui" / name)
                if u.path.startswith("/api/thumb/"):
                    key = unquote(os.path.basename(u.path))
                    L = Ledger(app.cfg.ledger_path)
                    row = L.db.execute("SELECT path FROM videos WHERE video_key=?", (key,)).fetchone()
                    L.close()
                    return self._file(Path(row[0]).with_suffix(".jpg"), "image/jpeg") if row else self._json(404, {"error": "no such video"})
                if u.path.startswith("/api/video/"):
                    key = unquote(os.path.basename(u.path))
                    L = Ledger(app.cfg.ledger_path)
                    row = L.db.execute("SELECT path FROM videos WHERE video_key=?", (key,)).fetchone()
                    L.close()
                    return self._file(Path(row[0]), "video/mp4") if row else self._json(404, {"error": "no such video"})
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                u = urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                try:
                    if u.path == "/api/settings":
                        app.state["settings"].update({k: v for k, v in body.items() if k in app.state["settings"]})
                        app.save_state()
                        return self._json(200, {"ok": True})
                    if u.path == "/api/schedule":
                        sch = app.state["schedule"]
                        for k in ("enabled", "times", "platforms"):
                            if k in body:
                                sch[k] = body[k]
                        sch["times"] = sorted({t for t in sch["times"] if isinstance(t, str) and len(t) == 5 and t[2] == ":"})
                        app.save_state()
                        app.log(f"[sched] {'ON' if sch['enabled'] else 'OFF'} {sch['times']} {sch['platforms']}")
                        return self._json(200, {"ok": True, "schedule": sch})
                    if u.path == "/api/login":
                        return self._json(200, {"ok": True, "url": app.open_login(body.get("platform", "youtube"))})
                    if u.path == "/api/check":
                        ok = app.start_job("check", app.check_accounts)
                        return self._json(200 if ok else 409, {"ok": ok})
                    if u.path == "/api/run":
                        ok = app.run_now(body.get("platforms"), body.get("n"))
                        return self._json(200 if ok else 409, {"ok": ok, "running": app.running})
                    if u.path == "/api/retry":
                        ok = app.retry(body["key"], body["platform"])
                        return self._json(200 if ok else 409, {"ok": ok})
                    if u.path == "/api/pending":
                        ok = app.post_pending()
                        return self._json(200 if ok else 409, {"ok": ok})
                    if u.path == "/api/quit":
                        threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                        return self._json(200, {"ok": True})
                    return self._json(404, {"error": "not found"})
                except Exception as e:
                    app.log(f"[api] {u.path}: {e}")
                    return self._json(500, {"error": str(e)})

        srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.log(f"[web] http://127.0.0.1:{self.port}")
        if open_browser:
            try:
                webbrowser.open(f"http://127.0.0.1:{self.port}")
            except Exception:
                pass
        try:
            srv.serve_forever()
        finally:
            self._stop.set()
            srv.server_close()
