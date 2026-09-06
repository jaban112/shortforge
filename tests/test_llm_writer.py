import json

from shortforge.writer import llm, write as write_script


def test_llm_writer_rejects_then_accepts(item, monkeypatch):
    calls = []
    drafts = [
        {"hook": "A ship left England with 150 souls.", "sentences": ["Captain Jones sailed west."], "title": "x"},
        {"hook": "In 1620, a ship left Plymouth, England.", "sentences": ["It carried 102 passengers and about 30 crew.", "After 10 weeks at sea it reached Cape Cod."], "title": "Mayflower"},
    ]

    def fake_call(api_key, model, system, user, timeout=90.0):
        calls.append(user)
        return "```json\n" + json.dumps(drafts[len(calls) - 1]) + "\n```"

    monkeypatch.setattr(llm, "_call", fake_call)
    s = llm.write(item, 45, "Chan", "key", "model", attempts=3, log=lambda *_: None)
    assert s is not None and s.writer == "llm"
    assert len(calls) == 2
    assert "REJECTED" in calls[1] and "150" in calls[1] and "Jones" in calls[1]
    assert s.hook.startswith("In 1620")
    assert s.title.endswith("#Shorts")


def test_llm_writer_falls_back_to_template(item, monkeypatch, cfg):
    monkeypatch.setattr(llm, "_call", lambda *a, **k: json.dumps({"hook": "Bogus 999 fact.", "sentences": ["Nope."], "title": "t"}))
    cfg.anthropic_api_key = "k"
    cfg.llm_attempts = 2
    s = write_script(item, cfg, log=lambda *_: None)
    assert s.writer == "template"
    assert s.tags and "shorts" in s.tags


def test_llm_writer_survives_garbage(item, monkeypatch):
    monkeypatch.setattr(llm, "_call", lambda *a, **k: "not json at all")
    assert llm.write(item, 45, "c", "k", "m", attempts=2, log=lambda *_: None) is None
