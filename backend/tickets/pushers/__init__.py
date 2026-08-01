from tickets.pushers.base import TicketPusher
from tickets.pushers.jira import JiraPusher
from tickets.pushers.linear import LinearPusher


def get_pusher(tracker_type: str, credentials: dict) -> TicketPusher:
    if tracker_type == "linear":
        return LinearPusher(credentials)
    if tracker_type == "jira":
        return JiraPusher(credentials)
    raise ValueError(f"Unknown tracker type: {tracker_type}")
