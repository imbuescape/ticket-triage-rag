"""
Zendesk is the easy case: flat JSON, plain-text description already.
This normalizer is almost a direct field mapping.
"""

from app.models import Ticket


def normalize_zendesk_payload(payload: dict) -> Ticket:
    t = payload["ticket"]
    return Ticket(
        source="zendesk",
        source_id=str(t["id"]),
        subject=t["subject"],
        description=t["description"],
        requester_email=t.get("requester", {}).get("email"),
        created_at=t["created_at"],
        raw=payload,
    )
