"""Deterministic template writer — grounded by construction.

Every sentence is a verbatim sentence from the source text. The only authored
lines are the hook frame and the CTA, which carry no facts beyond date/year.
"""
from __future__ import annotations

from ..sources.base import Item
from .script import CTA_BY_PACK, Script, build_description, build_header, build_title, split_sentences

WORDS_PER_SECOND = 2.3  # measured on Kokoro am_michael @1.05; only picks how many sentences to TRY — real length is measured after TTS


def hook_for(item: Item) -> str:
    if item.pack == "onthisday" and item.year is not None:
        return f"On this day in {item.year}."
    if item.pack == "apod":
        return "This is a real photograph from space."
    return "Here is something true."


def write(item: Item, target_seconds: float, channel_name: str) -> Script:
    hook = hook_for(item)
    cta = CTA_BY_PACK.get(item.pack, "Follow for more.")
    budget = int(target_seconds * WORDS_PER_SECOND) - len(hook.split()) - len(cta.split())
    body: list[str] = []
    used = 0
    event = (item.extra.get("event_text") or "").strip()
    pool = split_sentences(event) if event else []
    # then the article extract, minus sentences already covered by the event line
    for s in split_sentences(item.source_text):
        if s not in pool:
            pool.append(s)
    for s in pool:
        n = len(s.split())
        if body and used + n > budget:
            break
        body.append(s)
        used += n
        if used >= budget:
            break
    if not body:
        body = [item.source_text.strip()[:300]]
    header = build_header(item)
    title = build_title(item, hook)
    spoken = [hook, *body, cta]
    return Script(
        hook=hook,
        sentences=body,
        cta=cta,
        header=header,
        title=title,
        description=build_description(item, spoken, channel_name),
        tags=[],
        writer="template",
    )


def shorten(script: Script) -> Script | None:
    """Drop the last body sentence (used when measured audio exceeds max length)."""
    if len(script.sentences) <= 1:
        return None
    return Script(
        hook=script.hook,
        sentences=script.sentences[:-1],
        cta=script.cta,
        header=script.header,
        title=script.title,
        description=script.description,
        tags=script.tags,
        writer=script.writer,
    )
