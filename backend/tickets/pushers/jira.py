import requests
from requests.auth import HTTPBasicAuth

from tickets.pushers.base import TicketPusher

PRIORITY_MAP = {"high": "Highest", "medium": "Medium", "low": "Low"}


class JiraPusher(TicketPusher):
    def __init__(self, credentials: dict):
        self.site_url = credentials.get("site_url", "").rstrip("/")
        self.email = credentials.get("email", "")
        self.api_token = credentials.get("api_token", "")

    def push(self, ticket) -> str:
        if not all([self.site_url, self.email, self.api_token]):
            raise ValueError("Jira credentials incomplete")

        project_key = self._get_default_project()
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": ticket.title,
                "description": self._adf_description(ticket),
                "issuetype": {"name": "Task"},
                "priority": {"name": PRIORITY_MAP.get(ticket.priority, "Medium")},
            }
        }

        url = f"https://{self.site_url}/rest/api/3/issue"
        resp = requests.post(
            url,
            auth=HTTPBasicAuth(self.email, self.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Jira API error: {resp.status_code} {resp.text[:200]}")

        return resp.json()["key"]

    def _get_default_project(self) -> str:
        url = f"https://{self.site_url}/rest/api/3/project"
        resp = requests.get(
            url,
            auth=HTTPBasicAuth(self.email, self.api_token),
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        projects = resp.json()
        if not projects:
            raise ValueError("No Jira projects found")
        return projects[0]["key"]

    def _adf_description(self, ticket) -> dict:
        lines = [
            ticket.description,
            "",
            f"Source quote: {ticket.source_quote}",
        ]
        if ticket.assignee:
            lines.insert(1, f"Assignee (from meeting): {ticket.assignee}")
        content = []
        for line in lines:
            content.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
        return {"type": "doc", "version": 1, "content": content}
