"""CLI.

  shortforge doctor                  check ffmpeg / models / keys / egress
  shortforge run [--pack X] [--n N] [--upload] [--date YYYY-MM-DD]
                                     fetch -> write -> tts -> render -> (upload) -> ledger
  shortforge render-fixture PATH     render one Item JSON offline (no network)
  shortforge upload-pending          upload everything rendered but not yet uploaded
  shortforge auth                    interactive Google OAuth -> prints YT_REFRESH_TOKEN
  shortforge ig-auth                 validate an Instagram token -> prints IG_USER_ID / IG_ACCESS_TOKEN
  shortforge ig-refresh              refresh the Instagram long-lived token (workflow runs this daily)
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


def _ig_client(cfg):
    from .upload import igtoken
    from .upload.instagram import Instagram

    tok = igtoken.resolve(cfg.ig_access_token, cfg.ig_token_path, cfg.ig_token_key)
    if tok is None:
        raise RuntimeError("no Instagram token (IG_ACCESS_TOKEN or state/ig_token.enc + IG_TOKEN_KEY)")
    return Instagram(tok.token, cfg.ig_user_id, host=cfg.ig_graph_host, version=cfg.ig_api_version, log=log), tok


def _post_youtube(cfg, mp4: Path, meta: dict) -> tuple[str, str]:
    from .upload import YouTube

    yt = YouTube(cfg.yt_client_id, cfg.yt_client_secret, cfg.yt_refresh_token, log=log)
    vid = yt.upload(mp4, meta["title"], meta["description"], meta.get("tags") or _tags_from(meta),
                    category_id=cfg.yt_category_id, privacy=cfg.yt_privacy, synthetic=True)
    return vid, f"https://youtube.com/shorts/{vid}"


def _post_instagram(cfg, mp4: Path, meta: dict) -> tuple[str, str]:
    ig, _tok = _ig_client(cfg)
    caption = _ig_caption(meta)
    res = ig.upload_reel(mp4, caption, share_to_feed=cfg.ig_share_to_feed)
    return res.media_id, res.permalink or f"ig:{res.media_id}"


def _ig_caption(meta: dict) -> str:
    """Instagram caption: title line + description, ≤2,200 chars, ≤30 hashtags (extra tags removed in place)."""
    import re

    body = f"{meta['title'].replace(' #Shorts', '')}\n\n{meta['description']}"
    seen = 0

    def keep(m):
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= 30 else ""

    body = re.sub(r"(?<!\S)#\w+", keep, body)
    body = re.sub(r"[ \t]{2,}", " ", body).strip()
    return body[:2200]


def _post_uploadpost(cfg, mp4: Path, meta: dict) -> tuple[str, str]:
    """One POST per Upload-Post profile; remote_id = 'user=request_id,...'. Raises only if ALL profiles fail."""
    from .upload.uploadpost import UploadPost

    up = UploadPost(cfg.uploadpost_api_key, cfg.uploadpost_header, log=log)
    ids, errors = [], []
    for user in cfg.uploadpost_users:
        try:
            res = up.upload(mp4, user, cfg.uploadpost_platforms, meta["title"], meta["description"],
                            meta.get("tags") or _tags_from(meta), privacy=cfg.yt_privacy, category_id=cfg.yt_category_id, synthetic=True)
            ids.append(f"{user}={res.request_id or 'ok'}")
        except Exception as e:
            errors.append(f"{user}: {str(e)[:160]}")
            log(f"[upload-post] FAILED for {user}: {e}")
    if not ids:
        raise RuntimeError("; ".join(errors))
    return ",".join(ids), "https://app.upload-post.com/"


POSTERS = {"youtube": _post_youtube, "instagram": _post_instagram, "uploadpost": _post_uploadpost}


def _upload_all(cfg, ledger: Ledger, key: str, mp4: Path, meta: dict, platforms: list[str] | None = None) -> dict[str, str | None]:
    """Post to every configured platform; each failure is recorded per platform and never blocks the others."""
    out: dict[str, str | None] = {}
    for platform in platforms or cfg.uploaders:
        fn = POSTERS.get(platform)
        if fn is None:
            ledger.record_post(key, platform, None, error=f"unknown uploader {platform!r}")
            out[platform] = None
            continue
        try:
            rid, url = fn(cfg, mp4, meta)
            ledger.record_post(key, platform, rid, url=url)
            log(f"[post] {platform}: {url}")
            out[platform] = rid
        except Exception as e:
            ledger.record_post(key, platform, None, error=str(e)[:500])
            log(f"[post] {platform} FAILED for {mp4.name}: {e}")
            out[platform] = None
    return out


def _upload_one(cfg, ledger: Ledger, key: str, mp4: Path, meta: dict) -> str | None:
    """Backward-compatible single-result wrapper: returns the first successful remote id."""
    res = _upload_all(cfg, ledger, key, mp4, meta)
    for v in res.values():
        if v:
            return v
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
        log("upload requested but credentials are incomplete: " + " | ".join(cfg.missing()))
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
        log("credentials incomplete: " + " | ".join(cfg.missing()))
        return 2
    ledger = Ledger(cfg.ledger_path)
    n = 0
    for platform in cfg.uploaders:
        for key, path, _sj in ledger.pending_for(platform):
            mp4 = Path(path)
            meta_path = mp4.with_suffix(".json")
            if not mp4.exists() or not meta_path.exists():
                log(f"[upload] {platform}: missing files for {key}; skipping")
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if _upload_all(cfg, ledger, key, mp4, meta, platforms=[platform]).get(platform):
                n += 1
            if args.max and n >= args.max:
                break
    status.build(ledger, cfg.root / "status")
    log(f"[upload] posted {n}")
    return 0


def cmd_ig_auth(args) -> int:
    """Validate an Instagram token (pasted from the Meta App Dashboard) and print the env lines."""
    cfg = config.load()
    from .upload import igtoken
    from .upload.instagram import Instagram, exchange_short_lived

    token = (args.token or cfg.ig_access_token or input("Paste the Instagram access token from App Dashboard → Instagram → 'Generate token': ").strip())
    if args.app_secret:
        token, exp = exchange_short_lived(token, args.app_secret, host=cfg.ig_graph_host)
        log(f"[ig] exchanged for a long-lived token (expires in {exp // 86400} days)")
    ig = Instagram(token, "me", host=cfg.ig_graph_host, version=cfg.ig_api_version, log=log)
    me = ig.me()
    uid = str(me["id"])
    log(f"[ig] token OK: @{me.get('username')} id={uid} type={me.get('account_type')}")
    if me.get("account_type") not in (None, "BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        log("[ig] WARNING: account is not a professional (Business/Creator) account — publishing will be refused")
    ig.uid = uid
    try:
        lim = ig.publishing_limit()
        log(f"[ig] publishing quota used {lim['used']}/{lim['limit']} in the last 24h")
    except Exception as e:
        log(f"[ig] publishing_limit check failed: {e}")
    if cfg.ig_token_key:
        igtoken.save(cfg.ig_token_path, igtoken.IgToken(token, time.time(), time.time() + 60 * 86400, igtoken.digest(token)), cfg.ig_token_key)
        log(f"[ig] saved encrypted token to {cfg.ig_token_path}")
    log("\nPut these in .env (local) and in GitHub secrets:\n")
    log(f"IG_USER_ID={uid}\nIG_ACCESS_TOKEN={token}\nIG_TOKEN_KEY={cfg.ig_token_key or '<any long random string>'}\n")
    return 0


def cmd_ig_refresh(args) -> int:
    """Refresh the long-lived token if it is older than 24h; persist encrypted. Exit 1 only when refresh is REQUIRED and fails."""
    cfg = config.load()
    from .upload import igtoken

    if not cfg.ig_token_key:
        log("[ig] IG_TOKEN_KEY not set; cannot persist refreshed tokens (token will expire in ≤60 days)")
        return 0
    try:
        ig, tok = _ig_client(cfg)
    except Exception as e:
        log(f"[ig] {e}")
        return 1
    days_left = tok.remaining_s / 86400
    if tok.age_s < 86400 + 600:
        log(f"[ig] token is {tok.age_s / 3600:.1f}h old; Meta refuses refresh under 24h. {days_left:.0f} days left. Nothing to do")
        return 0
    if days_left > float(args.min_days_left):
        log(f"[ig] {days_left:.0f} days left (> {args.min_days_left}); skipping refresh")
        return 0
    try:
        new, exp = ig.refresh_token()
    except Exception as e:
        log(f"[ig] refresh FAILED: {e}")
        return 1 if days_left < 3 else 0
    igtoken.save(cfg.ig_token_path, igtoken.IgToken(new, time.time(), time.time() + exp, tok.origin), cfg.ig_token_key)
    log(f"[ig] refreshed; new token expires in {exp // 86400} days; saved to {cfg.ig_token_path}")
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
    line(cfg.upload_ready or not cfg.upload, f"uploaders={','.join(cfg.uploaders)}: credentials " + ("set" if cfg.upload_ready else "NOT set: " + " | ".join(cfg.missing())))
    log(("  --  " if cfg.anthropic_api_key else "  --  ") + ("ANTHROPIC_API_KEY set: LLM writer on (grounding-gated)" if cfg.anthropic_api_key else "ANTHROPIC_API_KEY not set: template writer (verbatim source sentences)"))
    http = Http(cfg.user_agent, timeout=10, retries=1)
    for name, url in (("wikimedia feed", "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/01/01"),
                      ("commons api", "https://commons.wikimedia.org/w/api.php?action=query&format=json&meta=siteinfo"),
                      ("nasa apod", f"https://api.nasa.gov/planetary/apod?api_key={cfg.nasa_api_key}&date=2020-01-01"),
                      ("google oauth", "https://oauth2.googleapis.com/tokeninfo?access_token=x"),
                      ("upload-post", "https://api.upload-post.com/api/uploadposts/history"),
                      ("instagram graph", f"https://{cfg.ig_graph_host}/{cfg.ig_api_version}/me"),
                      ("rupload.facebook.com", "https://rupload.facebook.com/")):
        try:
            http._get(url, headers={"Api-User-Agent": cfg.user_agent})
            line(True, f"egress: {name}")
        except Exception as e:
            reachable = any(c in str(e) for c in ("400", "401", "403", "404", "405"))  # an HTTP answer == reachable
            good = reachable and ("tokeninfo" in url or "upload-post" in url or "graph." in url or "rupload" in url)
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
    ia = sub.add_parser("ig-auth"); ia.add_argument("--token"); ia.add_argument("--app-secret", help="only if pasting a SHORT-lived token from your own OAuth flow"); ia.set_defaults(fn=cmd_ig_auth)
    ir = sub.add_parser("ig-refresh"); ir.add_argument("--min-days-left", default="50", help="refresh when fewer days than this remain (default 50 = roughly weekly)"); ir.set_defaults(fn=cmd_ig_refresh)
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
