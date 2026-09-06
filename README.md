# shortforge

Unattended faceless-Shorts factory. Grounded data → verified script → local TTS → ffmpeg → YouTube, on a GitHub Actions cron. Zero paid services required (LLM optional).

```
sources/   Wikimedia On-This-Day (+Commons license check) · NASA APOD (public-domain only)
writer/    LLM (Anthropic, optional) or template — BOTH pass grounding.check():
           every number and capitalized name in the script must exist in the source text
tts/       Kokoro-82M ONNX, per-sentence synthesis → exact caption timing (no alignment guesses)
render/    plate (licensed photo or procedural) · ASS captions · procedural ambient music · ffmpeg 1080x1920 H.264
upload/    YouTube Data API v3 resumable upload (containsSyntheticMedia=true), OAuth refresh-token auth, stats
           Instagram Reels via Instagram API with Instagram Login — resumable upload to rupload.facebook.com (no public URL),
           60-day token auto-refresh persisted Fernet-encrypted in state/ (GitHub Actions can't rewrite secrets)
           Upload-Post third-party fan-out (optional). SHORTFORGE_UPLOADER=youtube,instagram posts every video to both;
           per-platform results in the ledger `posts` table, failures retried by `upload-pending`
ledger.py  sqlite: items used · videos · uploads · daily stat snapshots · runs  (committed back by the workflow)
status.py  status/index.html + status/README.md — uploads, views, exact 90-day view window vs YPP thresholds
```

## Commands

```
python -m shortforge doctor                     # ffmpeg / model / keys / egress
python -m shortforge render-fixture fixtures/otd_1620.json   # offline end-to-end sample
python -m shortforge run [--pack onthisday|apod] [--n 1] [--upload] [--date 2026-09-06]
python -m shortforge upload-pending [--max N]
python -m shortforge auth                       # Google OAuth -> prints YT_REFRESH_TOKEN
python -m shortforge ig-auth                    # validates a Meta dashboard token -> prints IG_USER_ID / IG_ACCESS_TOKEN
python -m shortforge ig-refresh                 # refresh long-lived IG token when <50 days remain (workflow does this daily)
python -m shortforge stats                      # snapshot view counts, rebuild status page
python -m shortforge status
```

Exit codes: 0 ok · 1 render failure · 2 missing credentials · 3 nothing unused for this date.

## Design decisions (and why)

- **Grounding gate instead of "trust the LLM".** `writer/grounding.py` is a deterministic set-membership check: script numbers ⊆ source numbers ∪ {years-ago derivations}, script capitalized tokens ⊆ source words ∪ small allowlist. A failing draft is regenerated with the reason; after N failures the template writer (verbatim source sentences) takes over. The number in the video is never one the model made up.
- **Length is measured, not estimated.** Sentences are synthesized, total duration is read from the audio; if it exceeds `SHORTFORGE_MAX_SECONDS` the last body sentence is dropped and TTS re-runs. Word-per-second is only used to choose how many sentences to *try*.
- **Per-sentence TTS = exact captions.** No forced aligner, no proportional guessing; caption boundaries are the audio boundaries.
- **Image license is a gate, not a hope.** Commons `extmetadata.License` must be CC0 / CC-BY / CC-BY-SA / PD; NC/ND/fair-use/missing → procedural background. Credit line is on-screen for the whole video and in the description with the Commons page URL. APOD entries with a `copyright` field are skipped entirely.
- **Music is generated** (numpy additive pads, seeded by item id). No Content ID exposure.
- **Synthetic-media disclosure** is set on every upload (`status.containsSyntheticMedia`), and the description says the narration is AI and the facts are sourced.
- **Ledger in the repo.** GitHub Actions commits `state/ledger.sqlite` + `status/` after each run, so runs never repeat an item and you can read progress on GitHub without any server.

## Honest limits

- Revenue requires YouTube Partner Program approval (1,000 subs + 10M public Shorts views in 90 days until 2027-01-31, then 20M; fan-funding tier at 500 subs + 3M). YouTube's inauthentic-content policy (2025-07) explicitly targets "generic text through synthetic voice over stock footage on a fixed formula". This project's mitigations (sourced facts, per-video different subject and image, visible attribution, disclosure) are design choices, not a guarantee of approval.
- The build container had no egress to Wikimedia / NASA / Google; live API calls are covered by parser tests against documented response shapes and by `doctor` in your environment, not by a live run here. The full offline pipeline (fixture → script → Kokoro → ffmpeg → probe) was run for real.
- YouTube Data API quota: 1,600 units per upload, 10,000/day default → ≤ 6 uploads/day per Cloud project.
- Kokoro voices are English; the packs are English. A Korean pack would need a different TTS.

## Tests

`python -m pytest -q` — 46 tests: license gate, sentence splitter, grounding accept/reject cases, template writer invariants, Commons/OTD/APOD parsers with scripted HTTP, ASS timing, deterministic music, plate composition, real ffmpeg render + ffprobe check, ledger, exact 90-day window math, resumable upload chunking/resume with mocked HTTP, CLI run loop with dedupe.
