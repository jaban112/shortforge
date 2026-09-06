"""One item -> one finished MP4 (+ metadata JSON). Pure function of inputs
except for TTS/ffmpeg side effects in `workdir`."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .render import captions, images, music, video
from .sources.base import Item
from .tts import KokoroTTS
from .writer import Script, template, write as write_script


@dataclass
class Produced:
    item: Item
    script: Script
    mp4: Path
    meta_json: Path
    seconds: float


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:60] or "video"


def fetch_plate(item: Item, http, workdir: Path, log=print) -> Path:
    png = workdir / "plate.png"
    if item.image is not None:
        try:
            data = http.get_bytes(item.image.url)
            return images.plate_from_image(data, png)
        except Exception as e:  # image fetch/decode failure must not kill the run
            log(f"[plate] image failed ({e}); using procedural plate")
            item.image = None
    return images.plate_procedural(item.id, png)


def produce(item: Item, cfg: Config, http, tts: KokoroTTS, log=print) -> Produced:
    wd = cfg.workdir / _slug(item.id)
    wd.mkdir(parents=True, exist_ok=True)

    script = write_script(item, cfg, log=log)
    log(f"[writer] {script.writer}: {script.word_count} words, {len(script.sentences)} body sentences")

    # measure, don't estimate: synthesize, and if too long, drop trailing sentences and redo
    while True:
        lines = script.spoken
        kinds = ["hook", *(["body"] * len(script.sentences)), "cta"]
        speech = tts.speak(lines, kinds, wd / "voice.wav")
        log(f"[tts] total {speech.seconds:.2f}s")
        if speech.seconds <= cfg.max_seconds:
            break
        shorter = template.shorten(script)
        if shorter is None:
            raise RuntimeError(f"script cannot be shortened below {cfg.max_seconds}s")
        log(f"[tts] {speech.seconds:.1f}s > max {cfg.max_seconds}s; dropping last sentence")
        script = shorter

    total = speech.seconds
    plate = fetch_plate(item, http, wd, log)
    credit = item.image.credit if item.image else f"Source: {item.source_label}"
    ass = captions.build_ass(speech.segments, total, script.header, credit, item.source_label, wd / "captions.ass",
                             font=cfg.font_path or captions.FONT)
    music_wav = None
    if cfg.music_enabled:
        seed = cfg.seed if cfg.seed is not None else int(hash_seed(item.id))
        music_wav = music.generate(total, wd / "music.wav", seed=seed)

    out_mp4 = cfg.outdir / f"{_slug(item.id)}.mp4"
    res = video.render(plate, ass, speech.wav_path, music_wav, total, out_mp4, fps=cfg.fps,
                       music_gain_db=cfg.music_gain_db, log=log)

    thumb = out_mp4.with_suffix(".jpg")
    try:
        video.thumbnail(out_mp4, thumb, at=min(1.0, res.seconds / 2))
    except Exception as e:  # cosmetic only
        log(f"[thumb] failed: {e}")
        thumb = None

    meta = {
        "item_id": item.id,
        "thumb": str(thumb) if thumb else None,
        "pack": item.pack,
        "title": script.title,
        "description": script.description,
        "tags": script.tags or None,
        "script": script.to_dict(),
        "segments": [{"text": s.text, "start": s.start, "end": s.end, "kind": s.kind} for s in speech.segments],
        "seconds": res.seconds,
        "source_url": item.source_url,
        "image": (item.image.__dict__ if item.image else None),
        "contains_synthetic_media": True,
    }
    meta_json = out_mp4.with_suffix(".json")
    meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return Produced(item=item, script=script, mp4=out_mp4, meta_json=meta_json, seconds=res.seconds)


def hash_seed(s: str) -> int:
    import hashlib

    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], 16)
