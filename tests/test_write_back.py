
"""
Run: venv/bin/python tests/test_write_back.py

Tests the write-back logic in isolation - a real (but isolated,
disposable) ResolvedTicketKB backed by the deterministic test embedder,
so this needs zero network access.
"""

import sys
import os
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.write_back import (
    InMemoryPendingEscalationStore, write_back_auto_resolved,
    record_escalation, resolve_escalation, EscalationNotFoundError,
)
from app.knowledge_base import ResolvedTicketKB
from app.llm_judge import TriageDecision
from app.models import Ticket
from tests.test_knowledge_base import DeterministicTestEmbedder
from datetime import datetime

TEST_DB_PATH = "/tmp/test_write_back_chroma"


def _ticket(source_id="T-1", subject="test subject", description="test description"):
    return Ticket(
        source="zendesk", source_id=source_id, subject=subject,
        description=description, created_at=datetime.now(),
    )


def run():
    print("=" * 60)
    print("TEST 1: PendingEscalationStore basic record/get/remove")
    print("=" * 60)
    store = InMemoryPendingEscalationStore()
    ticket = _ticket()
    assert store.get("T-1") is None
    store.record(ticket)
    assert store.get("T-1") == ticket
    store.remove("T-1")
    assert store.get("T-1") is None
    print("✅ Record/get/remove all work correctly")

    print("\n" + "=" * 60)
    print("TEST 2: write_back_auto_resolved writes an UNVERIFIED entry")
    print("=" * 60)
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)
    assert kb.count() == 0

    ticket = _ticket(source_id="ZD-999", subject="Widget explodes on click",
                      description="clicking the widget causes an explosion")
    decision = TriageDecision(
        matched_ticket_id="ZD-100", confidence=91, reasoning="clear match",
        recommended_action="auto_resolve",
        customer_facing_draft="Update to the latest version to fix the widget explosion.",
    )
    write_back_auto_resolved(ticket, decision, kb)
    assert kb.count() == 1

    matches = kb.query("widget explodes", top_k=1)
    assert matches[0]["ticket_id"] == "ZD-999"
    assert matches[0]["origin"] == "auto_resolved"
    assert matches[0]["verified"] is False
    assert matches[0]["resolution"] == decision.customer_facing_draft
    print(f"Written back entry: {matches[0]}")
    print("✅ Auto-resolved write-back correctly tagged origin=auto_resolved, verified=False")

    print("\n" + "=" * 60)
    print("TEST 3: record_escalation + resolve_escalation writes a VERIFIED entry")
    print("=" * 60)
    esc_store = InMemoryPendingEscalationStore()
    escalated_ticket = _ticket(source_id="ZD-888", subject="Billing portal shows blank page",
                                description="the billing portal is completely blank for some users")
    record_escalation(escalated_ticket, esc_store)
    assert esc_store.get("ZD-888") is not None

    # Before resolution, this should NOT be in the KB yet
    pre_matches = kb.query("billing portal blank", top_k=3)
    assert not any(m["ticket_id"] == "ZD-888" for m in pre_matches)
    print("✅ Escalated ticket correctly NOT written back before human resolution")

    resolved = resolve_escalation(
        "ZD-888", "Root cause: a CDN caching bug for accounts on the legacy plan. Cleared cache manually.",
        esc_store, kb,
    )
    assert resolved.source_id == "ZD-888"
    assert kb.count() == 2  # the ZD-999 auto-resolved one, plus this
    assert esc_store.get("ZD-888") is None  # removed from pending after resolution

    post_matches = kb.query("billing portal blank", top_k=1)
    assert post_matches[0]["ticket_id"] == "ZD-888"
    assert post_matches[0]["origin"] == "human_verified"
    assert post_matches[0]["verified"] is True
    print(f"Written back entry: {post_matches[0]}")
    print("✅ Human resolution correctly tagged origin=human_verified, verified=True, removed from pending")

    print("\n" + "=" * 60)
    print("TEST 4: resolving an unknown/already-resolved ticket raises clearly")
    print("=" * 60)
    try:
        resolve_escalation("NEVER-ESCALATED-123", "some fix", esc_store, kb)
        raise AssertionError("Expected EscalationNotFoundError")
    except EscalationNotFoundError as e:
        print(f"Correctly raised: {e}")
    print("✅ Unknown ticket resolution attempt raises EscalationNotFoundError")

    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    print("\n" + "=" * 60)
    print("ALL WRITE-BACK TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run()
