"""Procedural ambient bed — generated, not licensed. No copyright claim possible.

Slow chord progression of soft additive pads with a gentle amplitude LFO and a
breath of filtered noise. Deterministic for a given seed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 24000

# chord roots (Hz) and intervals — minor-ish, calm
PROGRESSIONS = [
    [(220.00, (0, 3, 7, 10)), (174.61, (0, 4, 7, 11)), (261.63, (0, 4, 7)), (196.00, (0, 4, 7, 9))],   # Am7 Fmaj7 C G6
    [(146.83, (0, 3, 7, 10)), (196.00, (0, 4, 7)), (220.00, (0, 3, 7)), (174.61, (0, 4, 7, 11))],      # Dm7 G Am Fmaj7
    [(164.81, (0, 3, 7)), (130.81, (0, 4, 7, 11)), (196.00, (0, 4, 7)), (146.83, (0, 3, 7, 10))],     # Em Cmaj7 G Dm7
]


def _pad(freq: float, seconds: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros_like(t)
    for h, amp in ((1, 1.0), (2, 0.35), (3, 0.12)):
        detune = 1 + rng.uniform(-0.002, 0.002)
        out += amp * np.sin(2 * np.pi * freq * h * detune * t + rng.uniform(0, 2 * np.pi))
    # slow attack / release envelope
    env = np.ones_like(t)
    a = int(0.8 * SR)
    r = int(1.2 * SR)
    env[:a] = np.linspace(0, 1, a)
    env[-r:] *= np.linspace(1, 0, r)
    return out * env


def generate(seconds: float, out_wav: Path, seed: int = 0, chord_seconds: float = 4.0) -> Path:
    rng = np.random.default_rng(seed)
    prog = PROGRESSIONS[seed % len(PROGRESSIONS)]
    total = int((seconds + 1.0) * SR)
    mix = np.zeros(total, dtype=np.float64)
    pos = 0
    i = 0
    while pos < total:
        root, intervals = prog[i % len(prog)]
        chord = np.zeros(int(chord_seconds * SR))
        for iv in intervals:
            chord += _pad(root * (2 ** (iv / 12)), chord_seconds, rng)
        end = min(total, pos + len(chord))
        mix[pos:end] += chord[: end - pos]
        pos += int(chord_seconds * SR * 0.85)  # overlap chords for a continuous pad
        i += 1
    t = np.arange(total) / SR
    lfo = 0.85 + 0.15 * np.sin(2 * np.pi * 0.08 * t)
    mix *= lfo
    noise = rng.normal(0, 1, total)
    # cheap low-pass via moving average -> soft "air"
    k = 64
    noise = np.convolve(noise, np.ones(k) / k, mode="same") * 0.25
    mix += noise
    mix = mix[: int(seconds * SR)]
    # fade out over the last 1.5s
    f = int(1.5 * SR)
    if len(mix) > f:
        mix[-f:] *= np.linspace(1, 0, f)
    peak = np.max(np.abs(mix)) or 1.0
    mix = (mix / peak * 0.5).astype(np.float32)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), mix, SR)
    return out_wav
