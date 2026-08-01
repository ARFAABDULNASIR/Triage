"""Post-LLM safety: grounding, validation, dedupe, and confidence scoring."""

import re
from dataclasses import dataclass, field

from sessions_app.heuristic_extraction import _dedupe
from sessions_app.ticket_validator import STRONG_ACTION, is_valid_ticket, prefilter_transcript


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def quote_grounding_score(quote: str, transcript: str) -> tuple[bool, float]:
    """Return (grounded, score 0-1)."""
    if not quote or not transcript:
        return False, 0.0
    if transcript.startswith("[Extracted directly"):
        return False, 0.0

    norm_quote = normalize_text(quote)
    norm_transcript = normalize_text(transcript)
    if len(norm_quote) < 8:
        return False, 0.0

    if norm_quote in norm_transcript:
        return True, 1.0

    # Fuzzy: check if 70%+ of quote words appear in order in transcript
    quote_words = norm_quote.split()
    if len(quote_words) >= 3:
        pattern = r".{0,40}".join(re.escape(w) for w in quote_words[:12])
        if re.search(pattern, norm_transcript, re.DOTALL):
            return True, 0.7

    # Partial substring (first 40 chars)
    snippet = norm_quote[:40]
    if snippet in norm_transcript:
        return True, 0.7

    return False, 0.0


def compute_confidence(item: dict, transcript: str) -> tuple[float, dict]:
    """Blend LLM confidence with computed signals."""
    llm_conf = item.get("confidence")
    if llm_conf is None:
        commitment = (item.get("commitment_type") or "implied").lower()
        llm_conf = {"explicit": 0.9, "implied": 0.6, "weak": 0.35}.get(commitment, 0.5)
    else:
        try:
            llm_conf = max(0.0, min(1.0, float(llm_conf)))
        except (TypeError, ValueError):
            llm_conf = 0.5

    grounded, ground_score = quote_grounding_score(item.get("source_quote", ""), transcript)
    validator_score = 1.0 if is_valid_ticket(item) else 0.3
    if item.get("source_quote") and STRONG_ACTION.search(item["source_quote"]):
        validator_score = min(1.0, validator_score + 0.2)
    assignee_score = 1.0 if item.get("assignee") else 0.5

    final = (
        llm_conf * 0.4
        + ground_score * 0.3
        + validator_score * 0.2
        + assignee_score * 0.1
    )
    factors = {
        "llm": round(llm_conf, 3),
        "grounding": round(ground_score, 3),
        "validator": round(validator_score, 3),
        "assignee": round(assignee_score, 3),
    }
    return round(final, 3), factors


@dataclass
class ExtractionMeta:
    filtered_count: int = 0
    ungrounded_count: int = 0
    deduped_count: int = 0
    fallback_used: bool = False
    prefilter_applied: bool = False
    provider: str = ""
    llm_error: str = ""

    def to_dict(self) -> dict:
        return {
            "filtered_count": self.filtered_count,
            "ungrounded_count": self.ungrounded_count,
            "deduped_count": self.deduped_count,
            "fallback_used": self.fallback_used,
            "prefilter_applied": self.prefilter_applied,
            "provider": self.provider,
            "llm_error": self.llm_error,
        }


def process_extraction_results(
    items: list[dict],
    transcript: str,
    *,
    drop_invalid: bool = False,
) -> tuple[list[dict], ExtractionMeta]:
    """Validate, ground, score, and dedupe extracted tickets."""
    meta = ExtractionMeta()
    before_dedupe = len(items)
    processed: list[dict] = []

    for item in items:
        if not is_valid_ticket(item):
            meta.filtered_count += 1
            if drop_invalid:
                continue

        grounded, ground_score = quote_grounding_score(item.get("source_quote", ""), transcript)
        item["grounded"] = grounded
        if not grounded:
            meta.ungrounded_count += 1

        confidence, factors = compute_confidence(item, transcript)
        item["extraction_confidence"] = confidence
        item["confidence_factors"] = factors
        processed.append(item)

    deduped = _dedupe(processed)
    meta.deduped_count = before_dedupe - len(deduped)
    return deduped, meta


def prepare_transcript(text: str) -> tuple[str, bool]:
    """Prefilter chatter; returns (text, was_prefiltered)."""
    filtered = prefilter_transcript(text)
    if filtered != text.strip():
        return filtered, True
    return text.strip(), False
