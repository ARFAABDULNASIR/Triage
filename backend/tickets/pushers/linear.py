from integrations.linear_client import LinearClient
from tickets.pushers.base import TicketPusher


class LinearPusher(TicketPusher):
    def __init__(self, credentials: dict):
        self.api_key = credentials.get("api_key", "")
        self.team_id = credentials.get("team_id", "")

    def push(self, ticket) -> str:
        if not self.api_key:
            raise ValueError(
                "No Linear API key saved. Open Settings, paste your key, and save."
            )

        if ticket.match_action == ticket.MATCH_SKIP:
            if ticket.matched_issue_key:
                return ticket.matched_issue_key
            raise ValueError("Skip action requires a matched issue key")

        if ticket.match_action == ticket.MATCH_LINK:
            if ticket.matched_issue_key:
                return ticket.matched_issue_key
            raise ValueError("Link duplicate requires a matched issue key")

        if ticket.match_action == ticket.MATCH_UPDATE and ticket.matched_issue_id:
            client = LinearClient(self.api_key)
            issue = client.update_issue(
                ticket.matched_issue_id,
                {
                    "title": ticket.title,
                    "description": self._format_description(ticket),
                    "priority": self._priority_value(ticket.priority),
                },
            )
            return issue.get("identifier", ticket.matched_issue_key)

        # Linear requires a team to create an issue.
        if not self.team_id:
            raise ValueError(
                "No Linear team selected. Open Settings, click Test connection, "
                "pick a team from the list, and save."
            )

        issue = LinearClient(self.api_key).create_issue(
            {
                "title": ticket.title,
                "description": self._format_description(ticket),
                "priority": self._priority_value(ticket.priority),
                "teamId": self.team_id,
            }
        )
        return issue["identifier"]

    def _priority_value(self, priority: str) -> int:
        return {"high": 1, "medium": 2, "low": 3}.get(priority, 2)

    def _format_description(self, ticket) -> str:
        parts = [ticket.description, "", f"**Source quote:**\n> {ticket.source_quote}"]
        if ticket.assignee:
            parts.insert(1, f"**Assignee (from meeting):** {ticket.assignee}")
        if ticket.due_date:
            parts.insert(2, f"**Due date (from meeting):** {ticket.due_date}")
        if ticket.match_reason:
            parts.append(f"\n**Match note:** {ticket.match_reason}")
        return "\n".join(parts)
