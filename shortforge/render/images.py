"""Background plate: 1080x1920 PNG.

With an image: blurred cover-scaled copy behind, sharp fit-to-width copy in the
middle band, dark gradient top/bottom for legible text. Without an image: a
procedural plate (deep gradient + soft orbs) seeded from the item id, so no two
days look identical and nothing is sourced.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
BAND_TOP, BAND_BOTTOM = 300, 1180  # vertical band reserved for the sharp photo (captions sit below)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / img.width, h / img.height)
    r = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
    x, y = (r.width - w) // 2, (r.height - h) // 2
    return r.crop((x, y, x + w, y + h))


def _fit(img: Image.Image, w: int, h: int) -> Image.Image:
    s = min(w / img.width, h / img.height)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def _gradient_overlay(plate: Image.Image) -> Image.Image:
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    top, bottom = 420, 700
    for y in range(top):
        a = int(200 * (1 - y / top) ** 1.6)
        d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    for y in range(bottom):
        a = int(230 * (y / bottom) ** 1.6)
        d.line([(0, H - bottom + y), (W, H - bottom + y)], fill=(0, 0, 0, a))
    return Image.alpha_composite(plate.convert("RGBA"), overlay).convert("RGB")


def plate_from_image(image_bytes: bytes, out_png: Path) -> Path:
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    bg = _cover(img, W, H).filter(ImageFilter.GaussianBlur(28))
    bg = Image.eval(bg, lambda v: int(v * 0.55))
    fg = _fit(img, W - 60, BAND_BOTTOM - BAND_TOP)
    x = (W - fg.width) // 2
    y = BAND_TOP + (BAND_BOTTOM - BAND_TOP - fg.height) // 2
    # subtle frame
    frame = Image.new("RGB", (fg.width + 12, fg.height + 12), (245, 245, 245))
    bg.paste(frame, (x - 6, y - 6))
    bg.paste(fg, (x, y))
    out = _gradient_overlay(bg)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png, "PNG", optimize=True)
    return out_png


def plate_procedural(seed_text: str, out_png: Path) -> Path:
    h = hashlib.sha256(seed_text.encode("utf-8")).digest()
    hue = h[0] / 255.0
    base = _hsv(hue, 0.55, 0.22)
    base2 = _hsv((hue + 0.08) % 1.0, 0.6, 0.10)
    plate = Image.new("RGB", (W, H), base2)
    d = ImageDraw.Draw(plate)
    for y in range(H):
        f = y / H
        col = tuple(int(base[i] * (1 - f) + base2[i] * f) for i in range(3))
        d.line([(0, y), (W, y)], fill=col)
    orbs = Image.new("RGB", (W, H), (0, 0, 0))
    od = ImageDraw.Draw(orbs)
    for i in range(4):
        cx = int(h[1 + i] / 255 * W)
        cy = int(h[6 + i] / 255 * H)
        r = 260 + int(h[11 + i] / 255 * 320)
        col = _hsv((hue + 0.12 * i) % 1.0, 0.7, 0.75)
        od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
    orbs = orbs.filter(ImageFilter.GaussianBlur(140))
    plate = Image.blend(plate, orbs, 0.45)
    out = _gradient_overlay(plate)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png, "PNG", optimize=True)
    return out_png


def _hsv(hh: float, s: float, v: float) -> tuple[int, int, int]:
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(hh, s, v)
    return int(r * 255), int(g * 255), int(b * 255)
