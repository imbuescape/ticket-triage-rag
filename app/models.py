"""
Normalized ticket schema.

Why this file exists:
Zendesk and Jira have WILDLY different payload shapes. Zendesk gives you
flat fields. Jira gives you deeply nested fields, and its 'description'
is often not a plain string but an Atlassian Document Format (ADF) tree
(a JSON structure for rich text - bold, bullets, mentions, etc).

If every downstream piece of our pipeline (embedding, retrieval, LLM call)
had to know "is this a Zendesk ticket or a Jira ticket?", every function
would need if/else branches for source type. That's how integration code
rots.

Instead: every source gets ONE normalization function whose only job is
"turn your weird shape into this clean shape." Everything downstream only
ever sees a Ticket. This is the classic "anti-corruption layer" pattern.
"""

from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class Ticket(BaseModel):
    source: Literal["zendesk", "jira"]
    source_id: str  # e.g. Zendesk ticket #4821, Jira issue "ENG-193"
    subject: str
    description: str  # ALWAYS plain text by the time it reaches here
    requester_email: str | None = None
    created_at: datetime
    raw: dict = Field(default_factory=dict, exclude=True)
    # 'raw' keeps the original payload for debugging / audit trail,
    # but excluded from serialization so we don't leak it downstream
    # by accident (this is also where PII stripping would look, in step 2).

    def to_embedding_text(self) -> str:
        """
        What we actually feed into the embedding model.
        Subject usually carries more signal-per-word than description,
        so it's worth keeping them separate here rather than just
        concatenating blindly - you may want to weight them differently
        later (e.g. embed subject and description separately and combine).
        """
        return f"{self.subject}\n\n{self.description}".strip()
