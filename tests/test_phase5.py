"""
Run: venv/bin/python tests/test_phase5.py

Proves the FULL pipeline through routing: webhook -> normalize ->
retrieve -> judge -> route (with safety floor) -> mock action sink.
Uses fakes for KB and Judge (as in test_phase4.py); get_action_sink is
NOT overridden since MockActionSink is already safe to run anywhere.
"""

import sys
import os
import json
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from app.main import app, get_kb, get_judge, get_action_sink, require_valid_zendesk_signature, require_valid_jira_signature, get_idempotency_store
from app.idempotency import InMemoryIdempotencyStore
from app.knowledge_base import ResolvedTicketKB
from app.llm_judge import TriageJudge
from tests.test_knowledge_base import DeterministicTestEmbedder
from tests.test_llm_judge import FakeGroqClient
from tests.test_router import SpyActionSink
from tests.test_webhook_auth import passthrough_auth

TEST_DB_PATH = "/tmp/test_phase5_chroma"


def _seed_kb():
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)
    kb.add_resolved_ticket(
        ticket_id="ZD-100", subject="CSV export button unresponsive",
        description="User clicks export to csv on reports page, nothing happens",
        resolution="Known bug in report cache. Fix: clear browser cache.",
    )
    return kb


def run():
    client = TestClient(app)
    with open("data/mock_zendesk_ticket.json") as f:
        payload = json.load(f)

    # Build ONE KB instance and reuse it across both requests below - a
    # plain lambda in dependency_overrides is NOT cached like the real
    # get_kb() (which has @lru_cache), so calling _seed_kb() inside the
    # lambda would open a fresh Chroma/SQLite handle on every request,
    # colliding with the still-open earlier handle. This bit me while
    # writing this test - worth remembering as a real gotcha.
    test_kb = _seed_kb()
    app.dependency_overrides[get_kb] = lambda: test_kb
    # Real get_action_sink() now needs JIRA_* env vars - override with a
    # spy so this test still needs zero credentials, same reasoning as
    # overriding get_judge for zero API-key dependency.
    test_sink = SpyActionSink()
    app.dependency_overrides[get_action_sink] = lambda: test_sink
    app.dependency_overrides[require_valid_zendesk_signature] = passthrough_auth
    app.dependency_overrides[require_valid_jira_signature] = passthrough_auth
    app.dependency_overrides[get_idempotency_store] = lambda: InMemoryIdempotencyStore()

    print("=" * 60)
    print("TEST 1: High-confidence decision -> auto_resolve, no override")
    print("=" * 60)
    high_conf = {
        "matched_ticket_id": "ZD-100", "confidence": 90,
        "reasoning": "clear match", "recommended_action": "auto_resolve",
        "customer_facing_draft": "Here's the known fix.",
    }
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(high_conf))

    resp = client.post("/webhooks/zendesk", json=payload)
    body = resp.json()
    print(json.dumps(body["routing"], indent=2))
    assert body["routing"]["final_action"] == "auto_resolve"
    assert body["routing"]["override_applied"] is False
    assert body["routing"]["receipt"]["action"] == "customer_reply_posted"
    print("✅ High confidence correctly reaches the customer-reply mock sink")

    print("\n" + "=" * 60)
    print("TEST 2: Low-confidence auto_resolve gets safety-floor overridden")
    print("=" * 60)
    low_conf = {
        "matched_ticket_id": "ZD-100", "confidence": 40,
        "reasoning": "shaky match", "recommended_action": "auto_resolve",
        "customer_facing_draft": "Maybe this fix?",
    }
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(low_conf))

    resp2 = client.post("/webhooks/zendesk", json=payload)
    body2 = resp2.json()
    print(json.dumps(body2["routing"], indent=2))
    assert body2["routing"]["final_action"] == "escalate_to_human"
    assert body2["routing"]["override_applied"] is True
    assert body2["routing"]["receipt"]["action"] == "triage_note_posted"
    print("✅ Low confidence correctly overridden - customer was NOT auto-messaged")

    app.dependency_overrides.clear()
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    print("\n" + "=" * 60)
    print("ALL PHASE 5 TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run()