"""ASS subtitle file: per-sentence captions with exact TTS timing, a persistent
header (date · year) and a persistent credit/source line."""
from __future__ import annotations

from pathlib import Path

from ..tts.kokoro import Segment

FONT = "DejaVu Sans"


def _ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(segments: list[Segment], total_seconds: float, header: str, credit: str, source_label: str, out: Path, font: str = FONT) -> Path:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Body,{font},60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,380,1",
        f"Style: Hook,{font},76,&H0050E6FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,70,70,380,1",
        f"Style: Header,{font},56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,4,0,1,4,1,8,60,60,110,1",
        f"Style: Credit,{font},30,&H00D0D0D0,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,40,40,60,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{_ts(0)},{_ts(total_seconds)},Header,,0,0,0,,{_esc(header)}",
        f"Dialogue: 0,{_ts(0)},{_ts(total_seconds)},Credit,,0,0,0,,{_esc(credit)}",
    ]
    for i, seg in enumerate(segments):
        style = "Hook" if seg.kind == "hook" else "Body"
        end = segments[i + 1].start if i + 1 < len(segments) else min(total_seconds, seg.end + 0.6)
        fade = "{\\fad(120,120)}"
        lines.append(f"Dialogue: 1,{_ts(seg.start)},{_ts(end)},{style},,0,0,0,,{fade}{_esc(seg.text)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
