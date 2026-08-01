"""Linear GraphQL client for read/update operations."""

import requests

LINEAR_API = "https://api.linear.app/graphql"


class LinearClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _query(self, query: str, variables: dict | None = None) -> dict:
        if not self.api_key:
            raise ValueError("Linear API key not configured")
        try:
            resp = requests.post(
                LINEAR_API,
                headers={"Authorization": self.api_key, "Content-Type": "application/json"},
                json={"query": query, "variables": variables or {}},
                timeout=30,
            )
        except requests.RequestException:
            raise RuntimeError("Could not reach Linear. Check your internet connection and try again.")
        if resp.status_code in (401, 403):
            raise RuntimeError("Linear rejected the API key. Re-check it in Settings and test the connection.")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if data.get("errors"):
            # Linear returns GraphQL errors (with a useful message) even on HTTP 400.
            raise RuntimeError(data["errors"][0].get("message", "Linear API error"))
        resp.raise_for_status()
        return data.get("data", {})

    def test_connection(self) -> dict:
        data = self._query(
            """
            query {
              viewer { id name email }
            }
            """
        )
        return data.get("viewer", {})

    def list_teams(self) -> list[dict]:
        data = self._query(
            """
            query {
              teams {
                nodes { id key name }
              }
            }
            """
        )
        return data.get("teams", {}).get("nodes", [])

    def list_open_issues(self, team_id: str, limit: int = 100) -> list[dict]:
        data = self._query(
            """
            query OpenIssues($teamId: String!, $first: Int!) {
              team(id: $teamId) {
                issues(
                  first: $first
                  filter: { state: { type: { nin: ["completed", "canceled"] } } }
                ) {
                  nodes {
                    id
                    identifier
                    title
                    description
                    priority
                    state { name type }
                    url
                  }
                }
              }
            }
            """,
            {"teamId": team_id, "first": limit},
        )
        team = data.get("team") or {}
        return team.get("issues", {}).get("nodes", [])

    def get_issue(self, identifier: str) -> dict | None:
        data = self._query(
            """
            query Issue($id: String!) {
              issue(id: $id) {
                id identifier title description priority url
                state { name type }
              }
            }
            """,
            {"id": identifier},
        )
        return data.get("issue")

    def create_issue(self, fields: dict) -> dict:
        data = self._query(
            """
            mutation IssueCreate($input: IssueCreateInput!) {
              issueCreate(input: $input) {
                success
                issue { id identifier url }
              }
            }
            """,
            {"input": fields},
        )
        result = data.get("issueCreate", {})
        if not result.get("success"):
            raise RuntimeError("Linear issue creation failed")
        return result.get("issue", {})

    def update_issue(self, issue_id: str, fields: dict) -> dict:
        data = self._query(
            """
            mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
              issueUpdate(id: $id, input: $input) {
                success
                issue { id identifier url }
              }
            }
            """,
            {"id": issue_id, "input": fields},
        )
        result = data.get("issueUpdate", {})
        if not result.get("success"):
            raise RuntimeError("Linear issue update failed")
        return result.get("issue", {})
