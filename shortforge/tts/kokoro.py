"""Local TTS with Kokoro-82M (ONNX). Each sentence is synthesized separately, so
caption timing is exact by construction — no forced alignment, no estimates.

Model files (~350 MB) are fetched once from the kokoro-onnx GitHub release into
`models_dir`; GitHub Actions caches that directory.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
MODEL_MIN_BYTES = 300_000_000
VOICES_MIN_BYTES = 20_000_000

SAMPLE_RATE = 24000
GAP_AFTER_HOOK = 0.45
GAP_BETWEEN = 0.28
GAP_BEFORE_CTA = 0.40
LEAD_IN = 0.15


@dataclass
class Segment:
    text: str
    start: float
    end: float
    kind: str  # "hook" | "body" | "cta"


@dataclass
class Speech:
    wav_path: Path
    seconds: float
    segments: list[Segment]


def _download(url: str, dest: Path, min_bytes: int, log=print) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    log(f"[tts] downloading {url} -> {dest}")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    if tmp.stat().st_size < min_bytes:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded {url} is too small ({tmp.stat().st_size if tmp.exists() else 0} bytes)")
    tmp.replace(dest)


def ensure_models(models_dir: Path, log=print) -> tuple[Path, Path]:
    models_dir.mkdir(parents=True, exist_ok=True)
    model = models_dir / "kokoro-v1.0.onnx"
    voices = models_dir / "voices-v1.0.bin"
    if not model.exists() or model.stat().st_size < MODEL_MIN_BYTES:
        _download(MODEL_URL, model, MODEL_MIN_BYTES, log)
    if not voices.exists() or voices.stat().st_size < VOICES_MIN_BYTES:
        _download(VOICES_URL, voices, VOICES_MIN_BYTES, log)
    return model, voices


class KokoroTTS:
    def __init__(self, models_dir: Path, voice: str = "am_michael", speed: float = 1.05, log=print):
        from kokoro_onnx import Kokoro  # heavy import, keep local

        model, voices = ensure_models(Path(models_dir), log)
        t = time.time()
        self.k = Kokoro(str(model), str(voices))
        self.voice = voice
        self.speed = speed
        self.log = log
        log(f"[tts] kokoro loaded in {time.time() - t:.1f}s (voice={voice}, speed={speed})")

    def _synth(self, text: str) -> np.ndarray:
        samples, sr = self.k.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        if sr != SAMPLE_RATE:
            raise RuntimeError(f"unexpected sample rate {sr}")
        samples = np.asarray(samples, dtype=np.float32)
        # trim leading/trailing near-silence so gaps are controlled by us, not the model
        thresh = 0.01
        idx = np.where(np.abs(samples) > thresh)[0]
        if len(idx):
            a = max(0, idx[0] - int(0.05 * SAMPLE_RATE))
            b = min(len(samples), idx[-1] + int(0.12 * SAMPLE_RATE))
            samples = samples[a:b]
        return samples

    def speak(self, lines: list[str], kinds: list[str], out_wav: Path) -> Speech:
        assert len(lines) == len(kinds)
        pieces: list[np.ndarray] = []
        segments: list[Segment] = []
        cursor = LEAD_IN
        pieces.append(np.zeros(int(LEAD_IN * SAMPLE_RATE), dtype=np.float32))
        for i, (text, kind) in enumerate(zip(lines, kinds)):
            t0 = time.time()
            audio = self._synth(text)
            dur = len(audio) / SAMPLE_RATE
            segments.append(Segment(text=text, start=cursor, end=cursor + dur, kind=kind))
            pieces.append(audio)
            cursor += dur
            self.log(f"[tts] {kind:<4} {dur:5.2f}s ({time.time() - t0:.1f}s) {text[:60]}")
            if i < len(lines) - 1:
                gap = GAP_AFTER_HOOK if kind == "hook" else (GAP_BEFORE_CTA if kinds[i + 1] == "cta" else GAP_BETWEEN)
                pieces.append(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))
                cursor += gap
        pieces.append(np.zeros(int(0.6 * SAMPLE_RATE), dtype=np.float32))
        cursor += 0.6
        wav = np.concatenate(pieces)
        peak = float(np.max(np.abs(wav))) if len(wav) else 0.0
        if peak > 0:
            wav = wav * (0.89 / peak)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_wav), wav, SAMPLE_RATE)
        return Speech(wav_path=out_wav, seconds=len(wav) / SAMPLE_RATE, segments=segments)
