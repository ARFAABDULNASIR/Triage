"""Match extracted tickets against open Linear issues and prior pushed tickets."""

import json
import logging
import re

from integrations.linear_client import LinearClient

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz

    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


def _normalize_title(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def _fuzzy_score(a: str, b: str) -> float:
    if HAS_RAPIDFUZZ:
        return fuzz.token_sort_ratio(a, b) / 100.0
    na, nb = _normalize_title(a), _normalize_title(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.8
    return 0.0


def retrieve_candidates(ticket: dict, open_issues: list[dict], prior_tickets: list[dict]) -> list[dict]:
    """Cheap retrieval: top candidates by title similarity."""
    candidates = []
    title = ticket.get("title", "")

    for issue in open_issues:
        score = _fuzzy_score(title, issue.get("title", ""))
        if score >= 0.45:
            candidates.append({
                "source": "linear",
                "id": issue.get("id"),
                "key": issue.get("identifier"),
                "title": issue.get("title"),
                "description": (issue.get("description") or "")[:300],
                "score": score,
            })

    for prior in prior_tickets:
        score = _fuzzy_score(title, prior.get("title", ""))
        if score >= 0.5:
            candidates.append({
                "source": "prior",
                "id": prior.get("tracker_issue_key"),
                "key": prior.get("tracker_issue_key"),
                "title": prior.get("title"),
                "description": (prior.get("description") or "")[:300],
                "score": score,
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    seen = set()
    unique = []
    for c in candidates:
        key = c.get("key") or c.get("title")
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique[:5]


def _llm_classify_match(ticket: dict, candidates: list[dict]) -> dict:
    """Use the LLM provider chain to classify match action among top candidates."""
    if not candidates:
        return {
            "action": "create_new",
            "matched_issue_key": "",
            "matched_issue_id": "",
            "confidence": 0.9,
            "reason": "No similar open issues found",
        }

    from integrations.llm_providers import generate_json

    prompt = f"""You are matching a meeting action item to existing tracker issues.

New action item:
Title: {ticket.get('title')}
Description: {ticket.get('description')}
Source quote: {ticket.get('source_quote')}

Candidate existing issues:
{json.dumps(candidates, indent=2)}

Decide the best action:
- "create_new" — genuinely new work not covered by any candidate
- "update_existing" — same task, new info (due date, scope change)
- "link_duplicate" — exact duplicate, no update needed
- "skip_done" — already completed or no longer relevant

Return ONLY JSON:
{{
  "action": "create_new|update_existing|link_duplicate|skip_done",
  "matched_issue_key": "ENG-42 or empty",
  "matched_issue_id": "uuid or empty",
  "confidence": 0.0,
  "reason": "one sentence"
}}
"""
    try:
        raw, _provider = generate_json(
            prompt,
            system_prompt="You match meeting action items to existing tracker issues. Respond with JSON only.",
        )
        data = json.loads(raw or "{}")
        action = data.get("action", "create_new")
        valid_actions = ("create_new", "update_existing", "link_duplicate", "skip_done")
        if action not in valid_actions:
            action = "create_new"
        return {
            "action": action,
            "matched_issue_key": data.get("matched_issue_key", ""),
            "matched_issue_id": data.get("matched_issue_id", ""),
            "confidence": float(data.get("confidence", 0.5)),
            "reason": data.get("reason", ""),
        }
    except Exception as exc:
        logger.warning("LLM match classification failed: %s", exc)
        best = candidates[0]
        if best["score"] >= 0.85:
            return {
                "action": "link_duplicate",
                "matched_issue_key": best.get("key", ""),
                "matched_issue_id": best.get("id", ""),
                "confidence": best["score"],
                "reason": f"High title similarity ({best['score']:.0%})",
            }
        return {
            "action": "create_new",
            "matched_issue_key": "",
            "matched_issue_id": "",
            "confidence": 0.5,
            "reason": "Could not classify match",
        }


def match_tickets_for_session(
    items: list[dict],
    workspace,
    team_id: str | None = None,
) -> list[dict]:
    """Enrich extracted items with match_action and related fields."""
    from accounts.models import WorkspaceSettings
    from tickets.models import ExtractedTicket

    settings_obj = WorkspaceSettings.objects.filter(workspace=workspace).first()
    if not settings_obj or settings_obj.tracker_type != "linear":
        return items

    creds = settings_obj.get_credentials()
    api_key = creds.get("api_key", "")
    team_id = team_id or settings_obj.linear_team_id
    if not api_key or not team_id:
        return items

    try:
        client = LinearClient(api_key)
        open_issues = client.list_open_issues(team_id)
    except Exception as exc:
        logger.warning("Failed to fetch Linear issues for matching: %s", exc)
        return items

    prior_tickets = list(
        ExtractedTicket.objects.filter(
            session__workspace=workspace,
            status=ExtractedTicket.STATUS_APPROVED,
            tracker_issue_key__gt="",
        )
        .exclude(session__status="failed")
        .values("title", "description", "tracker_issue_key")[:50]
    )

    enriched = []
    for item in items:
        candidates = retrieve_candidates(item, open_issues, prior_tickets)
        match = _llm_classify_match(item, candidates)
        item["match_action"] = match["action"]
        item["matched_issue_key"] = match.get("matched_issue_key", "")
        item["matched_issue_id"] = match.get("matched_issue_id", "")
        item["match_confidence"] = match.get("confidence")
        item["match_reason"] = match.get("reason", "")
        enriched.append(item)
    return enriched
