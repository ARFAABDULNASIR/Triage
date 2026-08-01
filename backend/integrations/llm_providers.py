"""Free-tier LLM provider chain: Groq (primary) with Gemini fallback.

Both providers are called with temperature 0 and forced-JSON output.
`generate_json` tries each configured provider in order and returns the raw
response plus the name of the provider that produced it.
"""

import logging
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """
You are analyzing a meeting transcript to extract concrete, actionable tasks.

Read the FULL transcript first and understand the meeting's context, goals, and decisions.
Then identify only items where the team explicitly committed to doing something —
not topics discussed, not ideas floated, not questions asked.

For each genuine action item:
1. Synthesize a clear task title in your own words (do NOT copy transcript text verbatim)
2. Write a 1-2 sentence description giving enough context that someone who
   wasn't in the meeting would understand what to do and why
3. Identify who owns it, if stated or clearly implied
4. Note any deadline mentioned
5. Rate your confidence (0.0-1.0) based on how explicit the commitment was
6. Classify commitment_type as "explicit" (direct "I will"), "implied" (clear but indirect), or "weak"

Discard:
- General discussion with no commitment
- Questions that weren't answered with a decision
- Ideas mentioned but not assigned or agreed upon

If a later statement supersedes an earlier decision, only extract the final decision
and note the reversal in the description.

Return ONLY valid JSON, this exact shape:
{
  "tickets": [
    {
      "title": "string, max 8 words, imperative mood",
      "description": "string, 1-2 sentences, self-contained context",
      "assignee": "string or null",
      "due_date": "ISO date or null",
      "priority": "high|medium|low",
      "source_quote": "the exact sentence(s) this was derived from, for traceability",
      "confidence": 0.0,
      "confidence_reason": "brief explanation",
      "commitment_type": "explicit|implied|weak"
    }
  ]
}

If there are no genuine action items, return {"tickets": []}.
"""

MAX_RETRY_WAIT = 12


class LLMUnavailableError(Exception):
    """Raised when no configured provider produced a response."""


def groq_available() -> bool:
    return bool((settings.GROQ_API_KEY or "").strip())


def gemini_available() -> bool:
    return bool((settings.GEMINI_API_KEY or "").strip())


def any_llm_available() -> bool:
    return groq_available() or gemini_available()


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "resource_exhausted" in msg or "quota" in msg


def _quota_exhausted(exc: Exception) -> bool:
    return "limit: 0" in str(exc) or "perday" in str(exc).lower()


def _retry_seconds(exc: Exception) -> int:
    if _quota_exhausted(exc):
        return 0
    match = re.search(r"try again in ([\d.]+)s|retry in ([\d.]+)s", str(exc), re.IGNORECASE)
    if match:
        seconds = float(match.group(1) or match.group(2))
        return min(int(seconds) + 1, MAX_RETRY_WAIT)
    return 8


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, LLMUnavailableError):
        return str(exc)
    if _is_rate_limited(exc):
        return "The AI provider is rate limited. Wait a minute and try again."
    msg = str(exc)
    lower = msg.lower()
    if "api_key_invalid" in lower or "api key not valid" in lower or "invalid api key" in lower or "invalid_api_key" in lower:
        return (
            "An API key was rejected. Check GROQ_API_KEY (free at https://console.groq.com/keys) "
            "and GEMINI_API_KEY (free at https://aistudio.google.com/apikey) in .env, then restart the server."
        )
    if isinstance(exc, ValueError):
        return msg
    return msg[:300]


def _groq_generate(user_content: str, system_prompt: str) -> str:
    from groq import Groq

    client = Groq(api_key=settings.GROQ_API_KEY.strip())
    response = client.chat.completions.create(
        model=settings.GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or "{}"


def _gemini_generate(user_content: str, system_prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=settings.GEMINI_API_KEY.strip())
    model = genai.GenerativeModel(
        settings.GEMINI_MODEL,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    response = model.generate_content(user_content)
    return response.text or "{}"


def _call_with_retry(fn, user_content: str, system_prompt: str) -> str:
    """One retry on rate limit, respecting the provider's suggested wait."""
    try:
        return fn(user_content, system_prompt)
    except Exception as exc:
        if _is_rate_limited(exc) and not _quota_exhausted(exc):
            wait = _retry_seconds(exc)
            if wait > 0:
                time.sleep(wait)
                return fn(user_content, system_prompt)
        raise


PROVIDER_LABELS = {"groq": "Groq", "gemini": "Gemini"}


def generate_json(user_content: str, *, system_prompt: str, on_progress=None) -> tuple[str, str]:
    """Return (raw JSON text, provider name), trying Groq then Gemini.

    `on_progress` is an optional callable receiving human-readable status
    messages (used to narrate provider switches in the UI).
    """
    notify = on_progress or (lambda msg: None)
    providers = [
        ("groq", groq_available(), _groq_generate),
        ("gemini", gemini_available(), _gemini_generate),
    ]
    active = [p for p in providers if p[1]]
    errors: list[str] = []
    for i, (name, _available, fn) in enumerate(active):
        label = PROVIDER_LABELS.get(name, name)
        try:
            notify(f"Asking {label} to extract action items…")
            return _call_with_retry(fn, user_content, system_prompt), name
        except Exception as exc:
            logger.warning("%s generation failed: %s", name, exc)
            errors.append(f"{name}: {_friendly_error(exc)}")
            if i + 1 < len(active):
                next_label = PROVIDER_LABELS.get(active[i + 1][0], active[i + 1][0])
                notify(f"{label} unavailable, switching to {next_label}…")
    if not errors:
        raise LLMUnavailableError(
            "No AI provider configured. Add a free GROQ_API_KEY (https://console.groq.com/keys) "
            "or GEMINI_API_KEY (https://aistudio.google.com/apikey) to .env."
        )
    raise LLMUnavailableError(" · ".join(errors)[:500])
