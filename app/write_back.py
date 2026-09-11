
"""
Phase 7: the write-back loop - the piece that lets the knowledge base
actually improve over time instead of being frozen at whatever was
seeded.

Two distinct paths, deliberately different:

1. AUTO-RESOLVED tickets get written back IMMEDIATELY, tagged
   origin="auto_resolved", verified=False. This is a real, acknowledged
   risk: if the auto-resolution was wrong, the system is now reinforcing
   its own mistake as future "precedent" for similar tickets. Tagging it
   unverified doesn't eliminate that risk - it makes it INSPECTABLE.
   A judge prompt could later be taught to weight verified=True higher,
   or an audit process could periodically check verified=False entries
   against real customer follow-up (did they reopen the ticket?) and
   promote or delete accordingly. None of that is built yet - this is
   the seam it would attach to.

2. ESCALATED tickets do NOT get written back until a human actually
   provides the real resolution. There's no auto-generated content to
   trust yet. Escalating a ticket records it in a PendingEscalationStore
   (same in-memory-for-now pattern as the idempotency store), and a
   separate endpoint (POST /tickets/{source_id}/resolve) lets a human -
   or eventually a real "ticket closed" webhook - submit the actual fix,
   which becomes origin="human_verified", verified=True: the trustworthy
   path.
"""

from typing import Protocol
from app.models import Ticket
from app.llm_judge import TriageDecision
from app.knowledge_base import ResolvedTicketKB


class PendingEscalationStore(Protocol):
    def record(self, ticket: Ticket) -> None: ...
    def get(self, source_id: str) -> Ticket | None: ...
    def remove(self, source_id: str) -> None: ...


class InMemoryPendingEscalationStore:
    """
    Process-local only - same limitation as InMemoryIdempotencyStore
    (see app/idempotency.py). A restart loses track of anything escalated
    but not yet resolved. Production would need this backed by a real
    database so pending escalations survive restarts and are queryable
    by a real support/eng team, not just this process's memory.
    """

    def __init__(self):
        self._pending: dict[str, Ticket] = {}

    def record(self, ticket: Ticket) -> None:
        self._pending[ticket.source_id] = ticket

    def get(self, source_id: str) -> Ticket | None:
        return self._pending.get(source_id)

    def remove(self, source_id: str) -> None:
        self._pending.pop(source_id, None)


def write_back_auto_resolved(ticket: Ticket, decision: TriageDecision, kb: ResolvedTicketKB) -> None:
    """
    Called immediately after a ticket is auto-resolved. Writes the
    original problem + the LLM's own draft resolution back into the KB
    as unverified precedent. See module docstring for the reinforcement
    risk this carries.
    """
    kb.add_resolved_ticket(
        ticket_id=ticket.source_id,
        subject=ticket.subject,
        description=ticket.description,
        resolution=decision.customer_facing_draft,
        origin="auto_resolved",
        verified=False,
    )


def record_escalation(ticket: Ticket, store: PendingEscalationStore) -> None:
    """Called when a ticket is escalated - remembers it so a later human
    resolution can be matched back to the original problem text without
    requiring the human to re-supply subject/description themselves."""
    store.record(ticket)


class EscalationNotFoundError(Exception):
    """Raised when someone tries to resolve a ticket that was never
    escalated (or was already resolved/removed) - a 404 in HTTP terms."""


def resolve_escalation(
    source_id: str, resolution_text: str, store: PendingEscalationStore, kb: ResolvedTicketKB
) -> Ticket:
    """
    Called by a human (or eventually a real webhook) providing the actual
    fix for a previously-escalated ticket. Writes it back as TRUSTED
    precedent (verified=True) and removes it from the pending store.
    Returns the ticket that was resolved, for confirmation purposes.
    """
    ticket = store.get(source_id)
    if ticket is None:
        raise EscalationNotFoundError(
            f"No pending escalation found for source_id={source_id!r} "
            f"(never escalated, already resolved, or server restarted since)"
        )

    kb.add_resolved_ticket(
        ticket_id=ticket.source_id,
        subject=ticket.subject,
        description=ticket.description,
        resolution=resolution_text,
        origin="human_verified",
        verified=True,
    )
    store.remove(source_id)
    return ticket
