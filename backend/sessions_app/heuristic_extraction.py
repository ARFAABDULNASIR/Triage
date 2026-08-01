import re
from datetime import datetime, timedelta

from sessions_app.ticket_validator import (
    SPEAKER_LINE,
    is_actionable_line,
    is_valid_ticket,
)

# Convert commitments to imperative ticket titles
COMMITMENT = re.compile(
    r"\b(?:i(?:'ll| will)|we(?:'ll| will)|(?:need|has|have) to|must|going to)\s+(.+?)(?:\.|,|$)",
    re.I,
)
REQUEST = re.compile(
    r"\b(?:can|could) you(?:\s+also|\s+please)?\s+(.+?)(?:\.|,|\?|$)",
    re.I,
)
ACTION_ITEM = re.compile(r"\b(?:action item|todo|follow[- ]?up):\s*(.+?)(?:\.|$)", re.I)


def heuristic_extract(transcript: str) -> list[dict]:
    """Conservative local fallback — only high-confidence action lines."""
    candidates = []

    for raw in transcript.splitlines():
        raw = raw.strip()
        if not raw:
            continue

        m = SPEAKER_LINE.match(raw)
        speaker = m.group(1).strip() if m else ""
        text = m.group(2).strip() if m else raw

        if not is_actionable_line(text):
            continue

        title = _imperative_title(text)
        if not title or len(title) < 10:
            continue

        item = {
            "title": title[:200],
            "description": text[:400],
            "assignee": speaker or None,
            "due_date": _guess_due_date(text),
            "priority": _guess_priority(text),
            "source_quote": raw[:500],
        }
        if is_valid_ticket(item):
            candidates.append(item)

    return _dedupe(candidates)


def _imperative_title(text: str) -> str | None:
    for pattern in (COMMITMENT, REQUEST, ACTION_ITEM):
        m = pattern.search(text)
        if m:
            phrase = m.group(1).strip().rstrip(".,;")
            return _clean_imperative(phrase)

    # assigned tasks: "Sarah will send the report"
    m = re.search(r"\b(\w+)\s+will\s+(.+?)(?:\.|,|$)", text, re.I)
    if m:
        return _clean_imperative(m.group(2))

    return None


def _clean_imperative(phrase: str) -> str:
    phrase = re.sub(r"^(?:just|also|maybe|probably)\s+", "", phrase, flags=re.I)
    phrase = re.sub(r"^(?:to|and)\s+", "", phrase, flags=re.I)
    words = phrase.split()
    if not words:
        return ""
    # capitalize first word
    words[0] = words[0].capitalize()
    title = " ".join(words[:12]).rstrip(".,;:")
    return title


def _guess_priority(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ("asap", "urgent", "blocker", "critical", "immediately")):
        return "high"
    if any(w in lower for w in ("whenever", "low priority", "no rush", "if time")):
        return "low"
    return "medium"


def _guess_due_date(text: str) -> str | None:
    lower = text.lower()
    today = datetime.now().date()
    if "tomorrow" in lower:
        return (today + timedelta(days=1)).isoformat()
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return m.group(1) if m else None


def _dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        key = re.sub(r"\W+", "", item["title"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
