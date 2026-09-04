"""
Phase 5: routing, with a safety net independent of the LLM's own judgment.

Design: recommended_action from the judge is the PRIMARY signal - it's
reasoned, not a bare threshold on a number. But a single LLM call is a
single point of failure, and confidence scores from any model (even a
well-prompted one) can be miscalibrated on inputs unlike anything in
this batch. So we add one more check, entirely independent of the
judge's own self-assessment: if it says auto_resolve but its OWN stated
confidence is below a floor, we override to escalate anyway.

This mirrors defense-in-depth in other safety-critical systems: never
let a single signal make the final call unchecked, even when that
signal is usually right.

The floor is deliberately a MODULE-LEVEL CONSTANT you can tune with
real outcome data over time (Phase 6+ territory: log every decision +
override + eventual human-verified correctness, then adjust the floor
based on where auto_resolve actually goes wrong in practice - this is
the calibration loop the original architecture never had).
"""

import os
from typing import Protocol
from pydantic import BaseModel
from typing import Literal

from app.models import Ticket
from app.llm_judge import TriageDecision

CONFIDENCE_FLOOR = int(os.environ.get("ROUTING_CONFIDENCE_FLOOR", "75"))


class RoutingResult(BaseModel):
    final_action: Literal["auto_resolve", "escalate_to_human"]
    override_applied: bool  # True if we overrode the judge's own recommendation
    override_reason: str | None
    receipt: dict  # whatever the action sink returned


class TicketActionSink(Protocol):
    """
    Anything that can actually DO the final action - post a customer
    reply, or push an internal triage note. Same seam pattern as
    Embedder and the KB: define the interface, inject the implementation.
    Real Zendesk/Jira posting implementations plug in here later without
    touching routing logic at all.
    """

    def post_customer_reply(self, ticket: Ticket, draft: str) -> dict: ...

    def post_internal_triage_note(
        self, ticket: Ticket, decision: TriageDecision, matches: list[dict]
    ) -> dict: ...


class MockActionSink:
    """
    Does not call any real API. Logs what WOULD have happened and
    returns a fake receipt - safe to run with zero credentials, zero
    network, zero risk of actually messaging a customer while testing.
    """

    def post_customer_reply(self, ticket: Ticket, draft: str) -> dict:
        print(f"    [MOCK ZENDESK POST] Would post to ticket {ticket.source_id}:")
        print(f"        {draft[:150]}{'...' if len(draft) > 150 else ''}")
        return {"sink": "mock_zendesk", "action": "customer_reply_posted", "ticket_id": ticket.source_id}

    def post_internal_triage_note(
        self, ticket: Ticket, decision: TriageDecision, matches: list[dict]
    ) -> dict:
        print(f"    [MOCK JIRA POST] Would push internal triage note for {ticket.source_id}:")
        print(f"        confidence={decision.confidence}, reasoning={decision.reasoning[:120]}")
        return {"sink": "mock_jira", "action": "triage_note_posted", "ticket_id": ticket.source_id}


def route(
    ticket: Ticket,
    decision: TriageDecision,
    matches: list[dict],
    sink: TicketActionSink,
) -> RoutingResult:
    override_applied = False
    override_reason = None
    final_action = decision.recommended_action

    if decision.recommended_action == "auto_resolve" and decision.confidence < CONFIDENCE_FLOOR:
        # The judge wanted to auto-resolve, but its own confidence didn't
        # clear our independent floor - override regardless of its reasoning.
        final_action = "escalate_to_human"
        override_applied = True
        override_reason = (
            f"judge recommended auto_resolve but confidence "
            f"{decision.confidence} < floor {CONFIDENCE_FLOOR}"
        )

    if final_action == "auto_resolve":
        receipt = sink.post_customer_reply(ticket, decision.customer_facing_draft)
    else:
        receipt = sink.post_internal_triage_note(ticket, decision, matches)

    return RoutingResult(
        final_action=final_action,
        override_applied=override_applied,
        override_reason=override_reason,
        receipt=receipt,
    )