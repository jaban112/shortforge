"""CLI.

  shortforge doctor                  check ffmpeg / models / keys / egress
  shortforge run [--pack X] [--n N] [--upload] [--date YYYY-MM-DD]
                                     fetch -> write -> tts -> render -> (upload) -> ledger
  shortforge render-fixture PATH     render one Item JSON offline (no network)
  shortforge upload-pending          upload everything rendered but not yet uploaded
  shortforge auth                    interactive OAuth -> prints YT_REFRESH_TOKEN
  shortforge stats                   pull view counts + channel stats into ledger, rebuild status page
  shortforge status                  rebuild status page from ledger only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path

from . import __version__, config, status
from .http import Http
from .ledger import Ledger
from .sources import PACKS, get_pack
from .sources.base import Item


def log(msg: str) -> None:
    print(msg, flush=True)


def _upload_one(cfg, ledger: Ledger, key: str, mp4: Path, meta: dict) -> str | None:
    if cfg.uploader == "uploadpost":
        return _upload_one_uploadpost(cfg, ledger, key, mp4, meta)
    from .upload import YouTube

    yt = YouTube(cfg.yt_client_id, cfg.yt_client_secret, cfg.yt_refresh_token, log=log)
    try:
        vid = yt.upload(mp4, meta["title"], meta["description"], meta.get("tags") or _tags_from(meta),
                        category_id=cfg.yt_category_id, privacy=cfg.yt_privacy, synthetic=True)
        ledger.record_upload(key, vid)
        return vid
    except Exception as e:
        ledger.record_upload(key, None, error=str(e)[:500])
        log(f"[yt] upload FAILED for {mp4.name}: {e}")
        return None


def _upload_one_uploadpost(cfg, ledger: Ledger, key: str, mp4: Path, meta: dict) -> str | None:
    """One POST per Upload-Post profile; each profile fans out to its connected platforms.
    Ledger id = 'up:' + comma-joined '<user>=<request_id>' so stats (YouTube-only) skip it."""
    from .upload.uploadpost import UploadPost

    up = UploadPost(cfg.uploadpost_api_key, cfg.uploadpost_header, log=log)
    ids: list[str] = []
    errors: list[str] = []
    for user in cfg.uploadpost_users:
        try:
            res = up.upload(mp4, user, cfg.uploadpost_platforms, meta["title"], meta["description"],
                            meta.get("tags") or _tags_from(meta), privacy=cfg.yt_privacy,
                            category_id=cfg.yt_category_id, synthetic=True)
            ids.append(f"{user}={res.request_id or 'ok'}")
        except Exception as e:
            errors.append(f"{user}: {str(e)[:160]}")
            log(f"[upload-post] FAILED for {user}: {e}")
    if ids:
        ledger.record_upload(key, "up:" + ",".join(ids), error=("; ".join(errors) or None))
        return "up:" + ",".join(ids)
    ledger.record_upload(key, None, error="; ".join(errors)[:500])
    return None


def _tags_from(meta: dict) -> list[str]:
    from .writer.script import build_tags

    it = meta.get("item")
    if it:
        return build_tags(Item(**it))
    return ["shorts", "history", "facts"]


def cmd_run(args) -> int:
    cfg = config.load()
    if args.pack:
        cfg.pack = args.pack
    if args.n:
        cfg.videos_per_run = args.n
    if args.upload:
        cfg.upload = True
    if cfg.upload and not cfg.upload_ready:
        log("upload requested but credentials are incomplete: " + ("UPLOAD_POST_API_KEY / UPLOAD_POST_USERS / UPLOAD_POST_PLATFORMS" if cfg.uploader == "uploadpost" else "YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN"))
        return 2
    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    ledger = Ledger(cfg.ledger_path)
    run_id = ledger.start_run(cfg.pack)
    http = Http(cfg.user_agent)
    try:
        pack = get_pack(cfg.pack)
        items = pack.fetch(http, cfg, day)
        fresh = [it for it in items if not ledger.is_used(it.id)]
        log(f"[run] pack={cfg.pack} date={day} items={len(items)} unused={len(fresh)}")
        if not fresh:
            ledger.finish_run(run_id, False, "no unused items")
            log("[run] nothing to do: every item for this date is already used")
            return 3
        from .pipeline import produce
        from .tts import KokoroTTS

        tts = KokoroTTS(cfg.models_dir, cfg.tts_voice, cfg.tts_speed, log=log)
        made = 0
        errors: list[str] = []
        for item in fresh:
            if made >= cfg.videos_per_run:
                break
            try:
                p = produce(item, cfg, http, tts, log=log)
            except Exception as e:
                errors.append(f"{item.id}: {e}")
                log(f"[run] produce failed for {item.id}: {e}")
                continue
            ledger.mark_used(item.id, item.pack, item.title)
            meta = json.loads(p.meta_json.read_text(encoding="utf-8"))
            meta["item"] = item.__dict__ | {"image": (item.image.__dict__ if item.image else None)}
            p.meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            ledger.record_video(item.id, p.mp4, p.seconds, p.script.to_dict())
            made += 1
            log(f"[run] rendered {p.mp4} ({p.seconds:.1f}s) — {p.script.title}")
            if cfg.upload:
                _upload_one(cfg, ledger, item.id, p.mp4, meta)
        ok = made >= 1
        ledger.finish_run(run_id, ok, f"rendered {made}" + (f"; errors: {' | '.join(errors)[:300]}" if errors else ""))
        status.build(ledger, cfg.root / "status")
        return 0 if ok else 1
    except Exception as e:
        ledger.finish_run(run_id, False, f"fatal: {e}"[:500])
        status.build(ledger, cfg.root / "status")
        log(f"[run] FATAL: {e}")
        return 1
    finally:
        ledger.close()


def cmd_render_fixture(args) -> int:
    cfg = config.load()
    d = json.loads(Path(args.path).read_text(encoding="utf-8"))
    if d.get("image"):
        from .sources.base import Image

        d["image"] = Image(**d["image"])
    item = Item(**d)
    from .pipeline import produce
    from .tts import KokoroTTS

    tts = KokoroTTS(cfg.models_dir, cfg.tts_voice, cfg.tts_speed, log=log)
    p = produce(item, cfg, Http(cfg.user_agent), tts, log=log)
    log(f"rendered {p.mp4} ({p.seconds:.1f}s)\n{p.script.title}")
    return 0


def cmd_upload_pending(args) -> int:
    cfg = config.load()
    if not cfg.upload_ready:
        log("YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN not all set")
        return 2
    ledger = Ledger(cfg.ledger_path)
    n = 0
    for key, path, _sj in ledger.pending_uploads():
        mp4 = Path(path)
        meta_path = mp4.with_suffix(".json")
        if not mp4.exists() or not meta_path.exists():
            log(f"[upload] missing files for {key}; skipping")
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if _upload_one(cfg, ledger, key, mp4, meta):
            n += 1
        if args.max and n >= args.max:
            break
    status.build(ledger, cfg.root / "status")
    log(f"[upload] uploaded {n}")
    return 0


def cmd_auth(args) -> int:
    cfg = config.load()
    from .upload import interactive_auth

    cid = cfg.yt_client_id or input("YT_CLIENT_ID: ").strip()
    csec = cfg.yt_client_secret or input("YT_CLIENT_SECRET: ").strip()
    tok = interactive_auth(cid, csec, port=args.port, log=log)
    log("\nSUCCESS. Put these in .env (local) and in GitHub repo secrets:\n")
    log(f"YT_CLIENT_ID={cid}\nYT_CLIENT_SECRET={csec}\nYT_REFRESH_TOKEN={tok.refresh_token}\n")
    return 0


def cmd_stats(args) -> int:
    cfg = config.load()
    ledger = Ledger(cfg.ledger_path)
    channel = None
    if cfg.upload_ready:
        from .upload import YouTube

        yt = YouTube(cfg.yt_client_id, cfg.yt_client_secret, cfg.yt_refresh_token, log=log)
        ids = [yid for _, yid, _, _ in ledger.uploaded() if not yid.startswith("up:")]
        if ids:
            for yid, s in yt.video_stats(ids).items():
                ledger.record_stats(yid, s["views"], s["likes"], s["comments"])
        try:
            channel = yt.channel_stats()
        except Exception as e:
            log(f"[stats] channel stats failed: {e}")
    else:
        log("[stats] no YouTube credentials; rebuilding status from ledger only")
    h, m = status.build(ledger, cfg.root / "status", channel)
    log(f"[stats] wrote {h} and {m}")
    return 0


def cmd_status(args) -> int:
    cfg = config.load()
    ledger = Ledger(cfg.ledger_path)
    h, m = status.build(ledger, cfg.root / "status")
    log(f"wrote {h} and {m}")
    return 0


def cmd_doctor(args) -> int:
    cfg = config.load()
    ok = True

    def line(good: bool, msg: str) -> None:
        nonlocal ok
        ok = ok and good
        log(("  OK  " if good else " FAIL ") + msg)

    line(shutil.which("ffmpeg") is not None, "ffmpeg on PATH")
    line(shutil.which("ffprobe") is not None, "ffprobe on PATH")
    try:
        import kokoro_onnx  # noqa: F401
        import onnxruntime  # noqa: F401

        line(True, "kokoro-onnx + onnxruntime importable")
    except Exception as e:
        line(False, f"kokoro-onnx import: {e}")
    m = cfg.models_dir / "kokoro-v1.0.onnx"
    line(m.exists() and m.stat().st_size > 300_000_000, f"TTS model present at {m} (auto-downloads on first run if missing)")
    line(cfg.pack in PACKS, f"pack {cfg.pack!r} exists ({sorted(PACKS)})")
    line(cfg.upload_ready or not cfg.upload, f"uploader={cfg.uploader}: credentials " + ("set" if cfg.upload_ready else "NOT set (upload disabled)"))
    log(("  --  " if cfg.anthropic_api_key else "  --  ") + ("ANTHROPIC_API_KEY set: LLM writer on (grounding-gated)" if cfg.anthropic_api_key else "ANTHROPIC_API_KEY not set: template writer (verbatim source sentences)"))
    http = Http(cfg.user_agent, timeout=10, retries=1)
    for name, url in (("wikimedia feed", "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/01/01"),
                      ("commons api", "https://commons.wikimedia.org/w/api.php?action=query&format=json&meta=siteinfo"),
                      ("nasa apod", f"https://api.nasa.gov/planetary/apod?api_key={cfg.nasa_api_key}&date=2020-01-01"),
                      ("google oauth", "https://oauth2.googleapis.com/tokeninfo?access_token=x"),
                      ("upload-post", "https://api.upload-post.com/api/uploadposts/history")):
        try:
            http._get(url, headers={"Api-User-Agent": cfg.user_agent})
            line(True, f"egress: {name}")
        except Exception as e:
            good = ("tokeninfo" in url and "400" in str(e)) or ("upload-post" in url and ("401" in str(e) or "403" in str(e)))  # auth error == reachable
            line(good, f"egress: {name} ({str(e)[:80]})")
    log("doctor: " + ("all good" if ok else "problems above"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shortforge", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--pack"); r.add_argument("--n", type=int); r.add_argument("--upload", action="store_true"); r.add_argument("--date"); r.set_defaults(fn=cmd_run)
    f = sub.add_parser("render-fixture"); f.add_argument("path"); f.set_defaults(fn=cmd_render_fixture)
    u = sub.add_parser("upload-pending"); u.add_argument("--max", type=int, default=0); u.set_defaults(fn=cmd_upload_pending)
    a = sub.add_parser("auth"); a.add_argument("--port", type=int, default=8765); a.set_defaults(fn=cmd_auth)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    args = ap.parse_args(argv)
    t = time.time()
    rc = args.fn(args)
    log(f"[{args.cmd}] exit {rc} in {time.time() - t:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
