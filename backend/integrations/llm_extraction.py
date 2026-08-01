"""Transcript → structured tickets via the LLM chain, with heuristic fallback.

Order of preference:
  1. Groq (free API)      — see llm_providers.generate_json
  2. Gemini (free API)    — see llm_providers.generate_json
  3. Local heuristic      — no network, always available

Whatever produced the items, the same post-processing pipeline runs:
grounding, validation, dedupe, and confidence scoring.
"""

import json
import logging
import re

from integrations.extraction_pipeline import (
    ExtractionMeta,
    prepare_transcript,
    process_extraction_results,
)
from integrations.llm_providers import (
    EXTRACTION_SYSTEM_PROMPT,
    any_llm_available,
    generate_json,
)
from sessions_app.heuristic_extraction import heuristic_extract

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 100_000
MAX_CHUNK_CHARS = 30_000


class ExtractionResult:
    def __init__(
        self,
        items: list[dict],
        transcript: str = "",
        meta: ExtractionMeta | None = None,
    ):
        self.items = items
        self.transcript = transcript
        self.meta = meta or ExtractionMeta()


def _parse_items(raw: str) -> list:
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)
    if isinstance(data, dict):
        if "tickets" in data:
            data = data["tickets"]
        elif "items" in data:
            data = data["items"]
    if not isinstance(data, list):
        raise ValueError("The model did not return a JSON list of tickets")
    return data


def _map_item(item: dict) -> dict | None:
    """Pass through model fields — only coerce types for database storage."""
    if not isinstance(item, dict):
        return None
    title = (item.get("title") or "").strip()
    if not title:
        return None
    priority = (item.get("priority") or "medium").lower()
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    due = item.get("due_date")
    if due in ("null", "", None):
        due = None
    assignee = item.get("assignee")
    if assignee in ("null", "", None):
        assignee = None
    commitment = (item.get("commitment_type") or "implied").lower()
    if commitment not in ("explicit", "implied", "weak"):
        commitment = "implied"
    return {
        "title": title[:200],
        "description": (item.get("description") or "").strip(),
        "assignee": assignee,
        "due_date": due,
        "priority": priority,
        "source_quote": (item.get("source_quote") or "").strip(),
        "confidence": item.get("confidence"),
        "confidence_reason": (item.get("confidence_reason") or "").strip(),
        "commitment_type": commitment,
    }


def _items_from_response(raw: str) -> list[dict]:
    return [mapped for item in _parse_items(raw) if (mapped := _map_item(item))]


def _chunk_text(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHUNK_CHARS, len(text))
        if end < len(text):
            break_at = text.rfind("\n", start, end)
            if break_at > start:
                end = break_at
        chunks.append(text[start:end])
        start = end
    return chunks


def _run_pipeline(items: list[dict], transcript: str, meta: ExtractionMeta) -> ExtractionResult:
    processed, pipe_meta = process_extraction_results(items, transcript)
    meta.filtered_count += pipe_meta.filtered_count
    meta.ungrounded_count += pipe_meta.ungrounded_count
    meta.deduped_count += pipe_meta.deduped_count
    return ExtractionResult(processed, transcript, meta)


def _llm_extract(text: str, on_progress=None) -> tuple[list[dict], str]:
    """Extract via the provider chain; returns (items, provider name)."""
    chunks = _chunk_text(text)
    if len(chunks) == 1:
        raw, provider = generate_json(
            f"Meeting transcript:\n\n{text}",
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            on_progress=on_progress,
        )
        return _items_from_response(raw), provider

    all_items: list[dict] = []
    provider = ""
    for i, chunk in enumerate(chunks):
        raw, provider = generate_json(
            f"Meeting transcript (part {i + 1} of {len(chunks)}):\n\n{chunk}",
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            on_progress=on_progress,
        )
        all_items.extend(_items_from_response(raw))
    return all_items, provider


def extract_tickets(transcript_text: str, on_progress=None) -> ExtractionResult:
    """Extract action items with the full safety pipeline and fallback chain."""
    notify = on_progress or (lambda msg: None)
    text, prefiltered = prepare_transcript(transcript_text.strip())
    meta = ExtractionMeta(prefilter_applied=prefiltered)
    if not text:
        return ExtractionResult([], "", meta)

    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS]

    if any_llm_available():
        try:
            items, provider = _llm_extract(text, on_progress)
            meta.provider = provider
            return _run_pipeline(items, text, meta)
        except Exception as exc:
            logger.warning("LLM extraction failed, using heuristic fallback: %s", exc)
            meta.llm_error = str(exc)[:300]
            notify("AI providers unavailable, using the built-in local engine…")

    meta.provider = "heuristic"
    meta.fallback_used = True
    return _run_pipeline(heuristic_extract(text), text, meta)
