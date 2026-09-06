"""Writer facade: LLM if configured (gated), else template. Both are grounded."""
from __future__ import annotations

from ..sources.base import Item
from . import grounding, llm, template
from .script import Script, build_tags, split_sentences


def write(item: Item, cfg, log=print) -> Script:
    if cfg.anthropic_api_key:
        s = llm.write(
            item,
            cfg.target_seconds,
            cfg.channel_name,
            cfg.anthropic_api_key,
            cfg.anthropic_model,
            attempts=cfg.llm_attempts,
            log=log,
        )
        if s is not None:
            s.tags = build_tags(item)
            return s
        log("[writer] LLM failed grounding gate; falling back to template writer")
    s = template.write(item, cfg.target_seconds, cfg.channel_name)
    v = grounding.check([s.hook, *s.sentences], item)
    if not v.ok:
        # Template sentences are verbatim source; the only way this trips is a
        # sentence-initial word our allowlist doesn't know. Fail loudly.
        raise RuntimeError(f"template script failed grounding gate (bug): {v.reason()}")
    s.tags = build_tags(item)
    return s


__all__ = ["Script", "grounding", "llm", "split_sentences", "template", "write"]
