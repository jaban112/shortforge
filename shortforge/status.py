"""Status page: what ran, what uploaded, how views are trending, and distance to
the YouTube Partner Program thresholds. Written as status/index.html + status/README.md
so it renders on GitHub without any server.

90-day Shorts views are computed from the ledger's daily snapshots (sum over
videos of views_now - views_at_or_before_90_days_ago), which is what YPP counts.
Before 90 days of snapshots exist the number is exact for what we have and
labeled as such.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import time
from pathlib import Path

from .ledger import Ledger

YPP_SUBS = 1000
YPP_SHORTS_VIEWS_90D = 10_000_000          # until 2027-01-31
YPP_SHORTS_VIEWS_90D_FROM_2027_02 = 20_000_000
FAN_SUBS = 500
FAN_SHORTS_VIEWS_90D = 3_000_000


def views_last_90d(ledger: Ledger, now: float | None = None) -> tuple[int, int]:
    """Returns (views_in_window, snapshot_days). Exact given the snapshots we hold."""
    now = now or time.time()
    cutoff = now - 90 * 86400
    rows = ledger.db.execute("SELECT youtube_id, fetched_at, views FROM stats ORDER BY youtube_id, fetched_at").fetchall()
    by: dict[str, list[tuple[float, int]]] = {}
    for yid, t, v in rows:
        by.setdefault(yid, []).append((t, v or 0))
    total = 0
    earliest = now
    for yid, snaps in by.items():
        latest_v = snaps[-1][1]
        base = 0
        for t, v in snaps:
            if t <= cutoff:
                base = v
            earliest = min(earliest, t)
        total += max(0, latest_v - base)
    days = int((now - earliest) / 86400) if by else 0
    return total, days


def _fmt_ts(t: float | None) -> str:
    if not t:
        return "—"
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build(ledger: Ledger, out_dir: Path, channel: dict | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    uploaded = ledger.uploaded()
    latest = ledger.latest_stats()
    runs = ledger.recent_runs(30)
    pending = ledger.pending_uploads()
    v90, days = views_last_90d(ledger)
    subs = (channel or {}).get("subscribers")
    total_views = sum(latest[y][0] for _, y, _, _ in uploaded if y in latest)
    now_year_month = dt.date.today()
    target = YPP_SHORTS_VIEWS_90D if now_year_month < dt.date(2027, 2, 1) else YPP_SHORTS_VIEWS_90D_FROM_2027_02

    rows_html = []
    rows_md = ["| uploaded | title | views | likes | comments | link |", "|---|---|---:|---:|---:|---|"]
    for key, yid, sj, up_at in uploaded:
        s = json.loads(sj)
        v, l, c, _ = latest.get(yid, (0, 0, 0, 0))
        url = f"https://youtube.com/shorts/{yid}" if not yid.startswith("up:") else "https://app.upload-post.com/"
        rows_html.append(
            f"<tr><td>{_fmt_ts(up_at)}</td><td>{html.escape(s['title'])}</td><td class=n>{v:,}</td><td class=n>{l:,}</td><td class=n>{c:,}</td><td><a href='{url}'>{yid}</a></td></tr>"
        )
        rows_md.append(f"| {_fmt_ts(up_at)} | {s['title'].replace('|', '/')} | {v:,} | {l:,} | {c:,} | [{yid}]({url}) |")

    run_rows = "".join(
        f"<tr><td>{rid}</td><td>{_fmt_ts(st)}</td><td>{_fmt_ts(fi)}</td><td>{html.escape(str(pack))}</td><td>{'ok' if ok else ('FAIL' if ok is not None else 'running')}</td><td>{html.escape(str(note or ''))}</td></tr>"
        for rid, st, fi, pack, ok, note in runs
    )
    subs_txt = f"{subs:,}" if subs is not None else "unknown (run `shortforge stats`)"
    page = f"""<!doctype html><meta charset=utf-8><title>shortforge status</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#222}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}td,th{{border-bottom:1px solid #ddd;padding:.4rem .5rem;text-align:left}}
.n{{text-align:right;font-variant-numeric:tabular-nums}}.kpi{{display:flex;gap:1rem;flex-wrap:wrap}}.kpi div{{border:1px solid #ddd;border-radius:8px;padding:.8rem 1rem;min-width:200px}}
.kpi b{{display:block;font-size:1.6rem}}small{{color:#666}}</style>
<h1>shortforge status</h1><small>generated {_fmt_ts(time.time())}</small>
<div class=kpi>
<div><small>uploaded videos</small><b>{len(uploaded)}</b></div>
<div><small>pending (rendered, not uploaded)</small><b>{len(pending)}</b></div>
<div><small>total views (all uploads)</small><b>{total_views:,}</b></div>
<div><small>views in last 90 days ({days} days of snapshots)</small><b>{v90:,}</b><small>YPP target {target:,}</small></div>
<div><small>subscribers</small><b>{subs_txt}</b><small>YPP target {YPP_SUBS:,} · fan-funding tier {FAN_SUBS:,}</small></div>
</div>
<p><b>Monetization gates (facts, not estimates):</b> ad revenue tier = {YPP_SUBS:,} subscribers AND {target:,} public Shorts views in 90 days
(or 4,000 long-form watch hours in 12 months). Fan-funding tier = {FAN_SUBS:,} subscribers AND {FAN_SHORTS_VIEWS_90D:,} Shorts views in 90 days.
From 2027-02-01 the ad tier Shorts threshold is {YPP_SHORTS_VIEWS_90D_FROM_2027_02:,}. Approval is a YouTube review, not automatic.</p>
<h2>Uploads</h2><table><tr><th>uploaded</th><th>title</th><th class=n>views</th><th class=n>likes</th><th class=n>comments</th><th>id</th></tr>{''.join(rows_html) or '<tr><td colspan=6>none yet</td></tr>'}</table>
<h2>Recent runs</h2><table><tr><th>#</th><th>started</th><th>finished</th><th>pack</th><th>result</th><th>note</th></tr>{run_rows or '<tr><td colspan=6>none yet</td></tr>'}</table>
"""
    md = [
        "# shortforge status", "", f"_generated {_fmt_ts(time.time())}_", "",
        f"- uploaded videos: **{len(uploaded)}**",
        f"- pending (rendered, not uploaded): **{len(pending)}**",
        f"- total views: **{total_views:,}**",
        f"- views in last 90 days ({days} days of snapshots): **{v90:,}** / YPP target {target:,}",
        f"- subscribers: **{subs_txt}** / YPP target {YPP_SUBS:,}",
        "", *rows_md, "",
        "## Recent runs", "", "| # | started | finished | pack | result | note |", "|---|---|---|---|---|---|",
        *[f"| {rid} | {_fmt_ts(st)} | {_fmt_ts(fi)} | {pack} | {'ok' if ok else ('FAIL' if ok is not None else 'running')} | {str(note or '').replace('|', '/')} |" for rid, st, fi, pack, ok, note in runs],
    ]
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    (out_dir / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return out_dir / "index.html", out_dir / "README.md"
