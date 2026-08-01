from accounts.models import WorkspaceSettings
from tickets.models import ExtractedTicket
from tickets.pushers import get_pusher


def push_approved_tickets(session) -> dict:
    def _config_error(message):
        return {"pushed": 0, "failed": 0, "results": [], "error": message}

    settings = WorkspaceSettings.objects.filter(workspace=session.workspace).first()
    if not settings:
        return _config_error("Your tracker isn't set up yet. Open Settings to connect Linear or Jira.")

    creds = settings.get_credentials()
    if not creds:
        return _config_error(
            "No tracker credentials saved. Open Settings, add your "
            f"{settings.get_tracker_type_display()} key, and save."
        )

    if settings.tracker_type == "linear":
        if not settings.linear_team_id:
            return _config_error(
                "Almost there. Linear needs a team to create issues in: open Settings, "
                "click Test connection, pick your team from the list, and save."
            )
        creds["team_id"] = settings.linear_team_id

    pusher = get_pusher(settings.tracker_type, creds)
    approved = ExtractedTicket.objects.filter(
        session=session,
        status=ExtractedTicket.STATUS_APPROVED,
        tracker_issue_key="",
    )

    results = []
    pushed = failed = 0
    created = updated = linked = skipped = 0

    for ticket in approved:
        try:
            issue_key = pusher.push(ticket)
            ticket.tracker_issue_key = issue_key
            ticket.tracker_type = settings.tracker_type
            ticket.push_error = ""
            ticket.save(update_fields=["tracker_issue_key", "tracker_type", "push_error"])
            action = ticket.match_action
            if action == ExtractedTicket.MATCH_UPDATE:
                updated += 1
            elif action == ExtractedTicket.MATCH_LINK:
                linked += 1
            elif action == ExtractedTicket.MATCH_SKIP:
                skipped += 1
            else:
                created += 1
            results.append({
                "ticket_id": ticket.pk,
                "title": ticket.title,
                "ok": True,
                "issue_key": issue_key,
                "action": action,
            })
            pushed += 1
        except Exception as exc:
            ticket.push_error = str(exc)[:300]
            ticket.save(update_fields=["push_error"])
            results.append(
                {"ticket_id": ticket.pk, "title": ticket.title, "ok": False, "error": str(exc)[:300]}
            )
            failed += 1

    return {
        "pushed": pushed,
        "failed": failed,
        "created": created,
        "updated": updated,
        "linked": linked,
        "skipped": skipped,
        "results": results,
    }
