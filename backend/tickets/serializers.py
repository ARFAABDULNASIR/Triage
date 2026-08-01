def ticket_to_dict(ticket) -> dict:
    return {
        "id": ticket.pk,
        "title": ticket.title,
        "description": ticket.description,
        "assignee": ticket.assignee or None,
        "due_date": ticket.due_date.isoformat() if ticket.due_date else None,
        "priority": ticket.priority,
        "source_quote": ticket.source_quote,
        "status": ticket.status,
        "tracker_issue_key": ticket.tracker_issue_key,
        "grounded": ticket.grounded,
        "extraction_confidence": ticket.extraction_confidence,
        "confidence_tier": ticket.confidence_tier,
        "match_action": ticket.match_action,
        "matched_issue_key": ticket.matched_issue_key,
    }
