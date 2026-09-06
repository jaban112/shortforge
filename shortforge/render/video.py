"""ffmpeg assembly: plate PNG (slow zoom) + ASS captions + voice + music -> MP4.

1080x1920, 30 fps, H.264 yuv420p, AAC 192k, loudness-normalized to -14 LUFS
(YouTube's reference), faststart. Verified afterwards with ffprobe: the duration
and resolution are checked, not assumed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RenderResult:
    path: Path
    seconds: float
    width: int
    height: int


def _run(cmd: list[str], log=print) -> None:
    log("[ffmpeg] " + " ".join(cmd[:8]) + " …")
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({p.returncode}):\n{p.stderr[-2000:]}")


def probe(path: Path) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr[-500:]}")
    return json.loads(p.stdout)


def render(
    plate_png: Path,
    ass_path: Path,
    voice_wav: Path,
    music_wav: Path | None,
    seconds: float,
    out_mp4: Path,
    fps: int = 30,
    music_gain_db: float = -22.0,
    zoom: float = 0.10,
    log=print,
) -> RenderResult:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    d = f"{seconds:.3f}"
    # slow push-in: scale grows linearly with t, crop back to 1080x1920 (centered)
    vf = (
        f"scale=w='1080*(1+{zoom}*t/{d})':h='1920*(1+{zoom}*t/{d})':eval=frame,"
        f"crop=1080:1920,"
        f"ass='{_ass_escape(ass_path)}',"
        f"format=yuv420p"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-loop", "1", "-framerate", str(fps), "-t", d, "-i", str(plate_png),
           "-i", str(voice_wav)]
    if music_wav is not None:
        cmd += ["-i", str(music_wav)]
        af = (
            f"[1:a]aformat=channel_layouts=mono,volume=1.0[v];"
            f"[2:a]aformat=channel_layouts=mono,volume={music_gain_db}dB[m];"
            f"[v][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
    else:
        af = "[1:a]aformat=channel_layouts=mono,loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    cmd += ["-filter_complex", af, "-map", "0:v", "-map", "[a]",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", d, "-movflags", "+faststart", str(out_mp4)]
    _run(cmd, log)
    info = probe(out_mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    dur = float(info["format"]["duration"])
    w, h = int(v["width"]), int(v["height"])
    if (w, h) != (1080, 1920):
        raise RuntimeError(f"rendered {w}x{h}, expected 1080x1920")
    if abs(dur - seconds) > 0.5:
        raise RuntimeError(f"rendered duration {dur:.2f}s, expected {seconds:.2f}s")
    return RenderResult(path=out_mp4, seconds=dur, width=w, height=h)


def _ass_escape(p: Path) -> str:
    s = str(p.resolve())
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
