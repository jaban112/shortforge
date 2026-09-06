import json
from pathlib import Path

from shortforge.__main__ import main


def test_status_command(cfg, capsys):
    rc = main(["status"])
    assert rc == 0
    assert (cfg.root / "status" / "index.html").exists()


def test_run_refuses_upload_without_creds(cfg):
    assert main(["run", "--upload"]) == 2


def test_run_with_fake_pack_offline(cfg, monkeypatch, item):
    """Full run loop with the network + TTS + ffmpeg replaced by fakes; exercises
    ledger dedupe, meta JSON, status page."""
    import shortforge.__main__ as cli
    import shortforge.pipeline as pl

    class FakePack:
        name = "onthisday"

        def fetch(self, http, cfg, day=None):
            return [item]

    monkeypatch.setattr(cli, "get_pack", lambda name: FakePack())

    class FakeTTS:
        def __init__(self, *a, **k):
            pass

    import shortforge.tts as tts_mod

    monkeypatch.setattr(tts_mod, "KokoroTTS", FakeTTS)

    def fake_produce(it, cfg, http, tts, log=print):
        mp4 = cfg.outdir / "x.mp4"
        mp4.write_bytes(b"0")
        meta = mp4.with_suffix(".json")
        meta.write_text(json.dumps({"title": "t", "description": "d", "tags": ["a"]}))
        from shortforge.writer import template

        s = template.write(it, 45, "c")
        return pl.Produced(item=it, script=s, mp4=mp4, meta_json=meta, seconds=45.0)

    monkeypatch.setattr(pl, "produce", fake_produce)
    assert main(["run"]) == 0
    meta = json.loads((cfg.outdir / "x.json").read_text())
    assert meta["item"]["id"] == item.id
    # second run: item is used -> exit 3
    assert main(["run"]) == 3
    from shortforge.ledger import Ledger

    L = Ledger(cfg.ledger_path)
    assert L.is_used(item.id)
    assert len(L.pending_uploads()) == 1
    assert (cfg.root / "status" / "README.md").exists()
