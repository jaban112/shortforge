import io
import time

import numpy as np
import soundfile as sf
from PIL import Image as PILImage

from shortforge.ledger import Ledger
from shortforge.render import captions, images, music, video
from shortforge.status import build as build_status, views_last_90d
from shortforge.tts.kokoro import Segment


def test_music_is_deterministic_and_bounded(tmp_path):
    a = music.generate(5.0, tmp_path / "a.wav", seed=7)
    b = music.generate(5.0, tmp_path / "b.wav", seed=7)
    x, sr = sf.read(str(a))
    y, _ = sf.read(str(b))
    assert sr == 24000 and len(x) == 5 * 24000
    assert np.array_equal(x, y)
    assert np.max(np.abs(x)) <= 0.5 + 1e-6
    c, _ = sf.read(str(music.generate(5.0, tmp_path / "c.wav", seed=8)))
    assert not np.array_equal(x, c)


def test_plate_from_landscape_and_portrait_images(tmp_path):
    for size in ((1600, 900), (600, 1400), (50, 50)):
        im = PILImage.new("RGB", size, (200, 30, 30))
        buf = io.BytesIO()
        im.save(buf, "JPEG")
        out = images.plate_from_image(buf.getvalue(), tmp_path / f"p{size[0]}.png")
        got = PILImage.open(out)
        assert got.size == (1080, 1920)


def test_plate_procedural_varies_with_seed(tmp_path):
    a = PILImage.open(images.plate_procedural("otd:1", tmp_path / "a.png"))
    b = PILImage.open(images.plate_procedural("otd:2", tmp_path / "b.png"))
    assert a.size == (1080, 1920)
    assert a.tobytes() != b.tobytes()


def test_ass_captions_timing(tmp_path):
    segs = [Segment("Hook!", 0.15, 2.0, "hook"), Segment("Body one.", 2.45, 5.0, "body"), Segment("Follow.", 5.3, 6.5, "cta")]
    p = captions.build_ass(segs, 7.0, "SEPTEMBER 6 · 1620", "Image: X / Wikimedia Commons (CC BY 4.0)", "Wikipedia", tmp_path / "c.ass")
    txt = p.read_text(encoding="utf-8")
    assert "Dialogue: 1,0:00:00.15,0:00:02.45,Hook" in txt          # hook ends when next segment starts
    assert "Dialogue: 1,0:00:02.45,0:00:05.30,Body" in txt
    assert "Dialogue: 1,0:00:05.30,0:00:07.00,Body" in txt          # last is clamped to total
    assert "SEPTEMBER 6 · 1620" in txt and "CC BY 4.0" in txt
    assert "{" not in txt.split("[Events]")[1].replace("{\\fad(120,120)}", "")


def test_ffmpeg_render_short_clip(tmp_path):
    plate = images.plate_procedural("t", tmp_path / "p.png")
    sr = 24000
    voice = np.zeros(int(2.0 * sr), dtype=np.float32)
    voice[: sr // 2] = 0.3 * np.sin(2 * np.pi * 440 * np.arange(sr // 2) / sr)
    sf.write(str(tmp_path / "v.wav"), voice, sr)
    m = music.generate(2.0, tmp_path / "m.wav", seed=1)
    ass = captions.build_ass([Segment("Hello.", 0.1, 1.5, "hook")], 2.0, "H", "C", "S", tmp_path / "c.ass")
    res = video.render(plate, ass, tmp_path / "v.wav", m, 2.0, tmp_path / "o.mp4")
    assert res.width == 1080 and res.height == 1920 and abs(res.seconds - 2.0) < 0.5
    info = video.probe(res.path)
    assert any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in info["streams"])
    assert any(s["codec_type"] == "video" and s["codec_name"] == "h264" for s in info["streams"])


def test_ledger_roundtrip(tmp_path):
    L = Ledger(tmp_path / "l.sqlite")
    assert not L.is_used("a")
    L.mark_used("a", "onthisday", "A")
    assert L.is_used("a")
    L.record_video("a", tmp_path / "a.mp4", 45.2, {"title": "A #Shorts"})
    assert [k for k, _, _ in L.pending_uploads()] == ["a"]
    L.record_upload("a", "vid123")
    assert L.pending_uploads() == []
    assert L.uploaded()[0][1] == "vid123"
    L.record_upload("b", None, error="boom")  # no such row; must not raise
    rid = L.start_run("onthisday")
    L.finish_run(rid, True, "ok")
    assert L.recent_runs()[0][4] == 1


def test_views_last_90d_uses_snapshots(tmp_path):
    L = Ledger(tmp_path / "l.sqlite")
    now = time.time()
    L.db.execute("INSERT INTO stats VALUES('v1', ?, 1000, 0, 0)", (now - 100 * 86400,))
    L.db.execute("INSERT INTO stats VALUES('v1', ?, 1500, 0, 0)", (now - 91 * 86400,))
    L.db.execute("INSERT INTO stats VALUES('v1', ?, 4000, 0, 0)", (now - 1 * 86400,))
    L.db.execute("INSERT INTO stats VALUES('v2', ?, 700, 0, 0)", (now - 10 * 86400,))   # newer than window
    L.db.commit()
    total, days = views_last_90d(L, now)
    assert total == (4000 - 1500) + 700
    assert days == 100


def test_status_page_builds(tmp_path):
    L = Ledger(tmp_path / "l.sqlite")
    L.mark_used("a", "onthisday", "A")
    L.record_video("a", tmp_path / "a.mp4", 45.2, {"title": "A | B #Shorts"})
    L.record_upload("a", "vid123")
    L.record_stats("vid123", 12, 3, 1)
    h, m = build_status(L, tmp_path / "status", {"subscribers": 5})
    html = h.read_text(encoding="utf-8")
    assert "vid123" in html and "10,000,000" in html and "1,000" in html
    md = m.read_text(encoding="utf-8")
    assert "A / B #Shorts" in md
