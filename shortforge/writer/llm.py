"""LLM writer (optional): Anthropic Messages API via plain HTTP.

Produces hook + sentences + title from the source text ONLY. Output is passed
through the grounding gate; on failure it retries with the gate's reason, and
after `attempts` failures the caller falls back to the template writer.
"""
from __future__ import annotations

import json
import re

import requests

from ..sources.base import Item
from . import grounding
from .script import CTA_BY_PACK, Script, build_description, build_header, build_title

API = "https://api.anthropic.com/v1/messages"

SYSTEM = """You write narration for 45-second vertical educational videos.
Rules — violating any of them is a failure:
1. Use ONLY facts present in SOURCE. Do not add names, numbers, dates, places, causes, or outcomes that SOURCE does not state.
2. Every number you write must appear in SOURCE verbatim (years, counts, ages). If SOURCE has no number for something, do not give one.
3. Plain spoken English, short sentences, no lists, no hashtags, no emojis, no quotes from people unless quoted in SOURCE.
4. Structure: a one-sentence hook that creates curiosity WITHOUT a fact not in SOURCE, then 4-7 sentences telling the story, ending on the most striking detail from SOURCE.
5. Total length {min_words}-{max_words} words.
Return ONLY JSON: {{"hook": "...", "sentences": ["...", "..."], "title": "..."}} where title is <= 80 characters and contains no hashtags."""


class LlmError(RuntimeError):
    pass


def _call(api_key: str, model: str, system: str, user: str, timeout: float = 90.0) -> str:
    r = requests.post(
        API,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 800,
            "temperature": 0.7,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise LlmError(f"anthropic {r.status_code}: {r.text[:300]}")
    data = r.json()
    return "".join(block.get("text", "") for block in data.get("content", []))


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LlmError("no JSON in LLM output")
    obj = json.loads(m.group(0))
    if not isinstance(obj.get("hook"), str) or not isinstance(obj.get("sentences"), list) or not obj["sentences"]:
        raise LlmError("LLM JSON missing hook/sentences")
    obj["sentences"] = [str(s).strip() for s in obj["sentences"] if str(s).strip()]
    obj["title"] = str(obj.get("title") or "").strip()
    return obj


def write(item: Item, target_seconds: float, channel_name: str, api_key: str, model: str, attempts: int = 3, log=print) -> Script | None:
    min_words = int(target_seconds * 2.3)
    max_words = int(target_seconds * 2.9)
    system = SYSTEM.format(min_words=min_words, max_words=max_words)
    user = (
        f"DATE: {item.date_label}\nYEAR: {item.year}\nSUBJECT: {item.title}\n\nSOURCE:\n{item.source_text}\n\n"
        "Write the narration JSON now."
    )
    feedback = ""
    for attempt in range(1, attempts + 1):
        try:
            raw = _call(api_key, model, system, user + feedback)
            obj = _parse(raw)
        except (LlmError, json.JSONDecodeError, requests.RequestException) as e:
            log(f"[llm] attempt {attempt}: {e}")
            continue
        lines = [obj["hook"], *obj["sentences"]]
        verdict = grounding.check(lines, item)
        if verdict.ok:
            cta = CTA_BY_PACK.get(item.pack, "Follow for more.")
            spoken = [obj["hook"], *obj["sentences"], cta]
            title = build_title(item, obj["hook"])
            return Script(
                hook=obj["hook"],
                sentences=obj["sentences"],
                cta=cta,
                header=build_header(item),
                title=title,
                description=build_description(item, spoken, channel_name),
                tags=[],
                writer="llm",
            )
        log(f"[llm] attempt {attempt} rejected by grounding gate: {verdict.reason()}")
        feedback = (
            f"\n\nYour previous draft was REJECTED: {verdict.reason()}. "
            "Remove or replace those with wording that uses only SOURCE facts."
        )
    return None
