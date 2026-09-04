"""
Run: venv/bin/python tests/test_router.py

Tests the routing/safety-floor logic in isolation, with a spy action
sink that records what it was called with (rather than a full mock
framework - simple and enough for this case).
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.router import route, RoutingResult, CONFIDENCE_FLOOR
from app.llm_judge import TriageDecision
from app.models import Ticket
from datetime import datetime


class SpyActionSink:
    """Records calls instead of doing anything real - lets tests assert
    exactly which sink method was called and with what."""

    def __init__(self):
        self.customer_reply_calls = []
        self.triage_note_calls = []

    def post_customer_reply(self, ticket, draft):
        self.customer_reply_calls.append((ticket, draft))
        return {"sink": "spy", "action": "customer_reply_posted"}

    def post_internal_triage_note(self, ticket, decision, matches):
        self.triage_note_calls.append((ticket, decision, matches))
        return {"sink": "spy", "action": "triage_note_posted"}


def _ticket():
    return Ticket(
        source="zendesk", source_id="T-1", subject="test", description="test",
        created_at=datetime.now(),
    )


def run():
    print(f"Testing against CONFIDENCE_FLOOR = {CONFIDENCE_FLOOR}")

    print("=" * 60)
    print("TEST 1: High-confidence auto_resolve passes through unchanged")
    print("=" * 60)
    decision = TriageDecision(
        matched_ticket_id="ZD-100", confidence=90, reasoning="clear match",
        recommended_action="auto_resolve", customer_facing_draft="Here's the fix.",
    )
    sink = SpyActionSink()
    result = route(_ticket(), decision, matches=[], sink=sink)
    print(result.model_dump_json(indent=2))
    assert result.final_action == "auto_resolve"
    assert result.override_applied is False
    assert len(sink.customer_reply_calls) == 1
    assert len(sink.triage_note_calls) == 0
    print("✅ Passed through correctly, customer reply sink called")

    print("\n" + "=" * 60)
    print(f"TEST 2: Low-confidence auto_resolve gets overridden (below {CONFIDENCE_FLOOR})")
    print("=" * 60)
    low_conf_decision = TriageDecision(
        matched_ticket_id="ZD-100", confidence=CONFIDENCE_FLOOR - 1,
        reasoning="shaky match", recommended_action="auto_resolve",
        customer_facing_draft="Here's a maybe-fix.",
    )
    sink2 = SpyActionSink()
    result2 = route(_ticket(), low_conf_decision, matches=[], sink=sink2)
    print(result2.model_dump_json(indent=2))
    assert result2.final_action == "escalate_to_human"
    assert result2.override_applied is True
    assert "floor" in result2.override_reason
    assert len(sink2.customer_reply_calls) == 0  # customer was NOT messaged
    assert len(sink2.triage_note_calls) == 1
    print("✅ Correctly overridden - customer reply sink was NOT called")

    print("\n" + "=" * 60)
    print("TEST 3: Judge's own escalate_to_human passes through, no override flag")
    print("=" * 60)
    escalate_decision = TriageDecision(
        matched_ticket_id=None, confidence=20, reasoning="no real match",
        recommended_action="escalate_to_human", customer_facing_draft=None,
    )
    sink3 = SpyActionSink()
    result3 = route(_ticket(), escalate_decision, matches=[], sink=sink3)
    print(result3.model_dump_json(indent=2))
    assert result3.final_action == "escalate_to_human"
    assert result3.override_applied is False  # judge already said escalate - not an override
    assert result3.override_reason is None
    print("✅ Correctly distinguishes 'judge said escalate' from 'we overrode to escalate'")

    print("\n" + "=" * 60)
    print(f"TEST 4: Boundary case - confidence EXACTLY at floor ({CONFIDENCE_FLOOR}) should pass")
    print("=" * 60)
    boundary_decision = TriageDecision(
        matched_ticket_id="ZD-100", confidence=CONFIDENCE_FLOOR,
        reasoning="boundary case", recommended_action="auto_resolve",
        customer_facing_draft="fix",
    )
    sink4 = SpyActionSink()
    result4 = route(_ticket(), boundary_decision, matches=[], sink=sink4)
    assert result4.final_action == "auto_resolve"
    assert result4.override_applied is False
    print(f"✅ confidence == floor ({CONFIDENCE_FLOOR}) correctly treated as passing (>= not >)")

    print("\n" + "=" * 60)
    print("ALL ROUTER TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run()