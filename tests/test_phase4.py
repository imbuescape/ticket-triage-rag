"""
Run: venv/bin/python tests/test_phase4.py

Proves the FULL pipeline works: webhook POST -> normalize -> embed ->
retrieve -> LLM judge -> structured decision in the response. Both the
KB and the Judge are overridden with fakes, so this needs zero network
access and zero API key - pure plumbing verification.
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

TEST_DB_PATH = "/tmp/test_phase4_chroma"


def run():
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    # --- Fake KB (same as Phase 3 test) ---
    test_kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)
    test_kb.add_resolved_ticket(
        ticket_id="ZD-100",
        subject="CSV export button unresponsive",
        description="User clicks export to csv on reports page, nothing happens, no error shown",
        resolution="Known bug in report cache. Fix: clear browser cache.",
    )

    # --- Fake Judge: canned high-confidence auto_resolve decision ---
    canned_decision = {
        "matched_ticket_id": "ZD-100",
        "confidence": 90,
        "reasoning": "Same root cause: report export cache bug.",
        "recommended_action": "auto_resolve",
        "customer_facing_draft": "This is a known issue - please clear your browser cache.",
    }
    test_judge = TriageJudge(groq_client=FakeGroqClient(canned_decision))

    app.dependency_overrides[get_kb] = lambda: test_kb
    app.dependency_overrides[get_judge] = lambda: test_judge
    app.dependency_overrides[get_action_sink] = lambda: SpyActionSink()
    app.dependency_overrides[require_valid_zendesk_signature] = passthrough_auth
    app.dependency_overrides[require_valid_jira_signature] = passthrough_auth
    app.dependency_overrides[get_idempotency_store] = lambda: InMemoryIdempotencyStore()

    client = TestClient(app)

    with open("data/mock_zendesk_ticket.json") as f:
        payload = json.load(f)

    print("=" * 60)
    print("TEST: full webhook -> retrieve -> judge pipeline")
    print("=" * 60)

    resp = client.post("/webhooks/zendesk", json=payload)
    body = resp.json()
    print(json.dumps(body, indent=2))

    assert resp.status_code == 200
    assert body["source_id"] == "48213"
    assert len(body["matches"]) >= 1
    assert body["decision"] is not None
    assert body["decision"]["matched_ticket_id"] == "ZD-100"
    assert body["decision"]["recommended_action"] == "auto_resolve"
    assert body["decision"]["confidence"] == 90

    print("\n✅ Full pipeline (webhook -> normalize -> retrieve -> judge) works end to end.")

    # --- Second case: empty KB should skip the judge call entirely ---
    print("\n" + "=" * 60)
    print("TEST: empty KB skips the LLM call rather than judging nothing")
    print("=" * 60)
    empty_kb_path = "/tmp/test_phase4_empty_chroma"
    shutil.rmtree(empty_kb_path, ignore_errors=True)
    empty_kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=empty_kb_path)
    app.dependency_overrides[get_kb] = lambda: empty_kb

    resp2 = client.post("/webhooks/zendesk", json=payload)
    body2 = resp2.json()
    print(json.dumps(body2, indent=2))
    assert body2["matches"] == []
    assert body2["decision"] is None
    print("\n✅ Empty-KB case correctly skips the judge call.")

    app.dependency_overrides.clear()
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    shutil.rmtree(empty_kb_path, ignore_errors=True)


if __name__ == "__main__":
    run()