
"""
Run: venv/bin/python tests/test_phase7.py

Proves the FULL write-back loop through the real FastAPI app:
1. Auto-resolve -> written back immediately, unverified.
2. Escalate -> NOT written back, held as pending.
3. POST /tickets/{id}/resolve -> written back as human_verified, removed
   from pending.
4. Resolving an unknown ticket -> 404.
"""

import sys
import os
import json
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from app.main import (
    app, get_kb, get_judge, get_action_sink, get_idempotency_store,
    get_pending_escalation_store, require_valid_zendesk_signature, require_valid_jira_signature,
)
from app.knowledge_base import ResolvedTicketKB
from app.llm_judge import TriageJudge
from app.idempotency import InMemoryIdempotencyStore
from app.write_back import InMemoryPendingEscalationStore
from tests.test_knowledge_base import DeterministicTestEmbedder
from tests.test_llm_judge import FakeGroqClient
from tests.test_router import SpyActionSink
from tests.test_webhook_auth import passthrough_auth

TEST_DB_PATH = "/tmp/test_phase7_chroma"


def _make_payload(ticket_id: int, subject: str, description: str) -> dict:
    return {"ticket": {"id": ticket_id, "subject": subject, "description": description,
                        "status": "open", "requester": {"email": "test@example.com"},
                        "created_at": "2026-09-04T00:00:00Z"}}


def run():
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    test_kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)
    test_kb.add_resolved_ticket(
        ticket_id="ZD-100", subject="CSV export button unresponsive",
        description="csv export broken", resolution="clear cache",
    )
    initial_count = test_kb.count()

    pending_store = InMemoryPendingEscalationStore()

    app.dependency_overrides[get_kb] = lambda: test_kb
    app.dependency_overrides[get_action_sink] = lambda: SpyActionSink()
    app.dependency_overrides[get_idempotency_store] = lambda: InMemoryIdempotencyStore()
    app.dependency_overrides[get_pending_escalation_store] = lambda: pending_store
    app.dependency_overrides[require_valid_zendesk_signature] = passthrough_auth
    app.dependency_overrides[require_valid_jira_signature] = passthrough_auth

    client = TestClient(app)

    print("=" * 60)
    print("TEST 1: Auto-resolved ticket is written back immediately")
    print("=" * 60)
    high_conf = {
        "matched_ticket_id": "ZD-100", "confidence": 90, "reasoning": "clear match",
        "recommended_action": "auto_resolve", "customer_facing_draft": "Here's the fix.",
    }
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(high_conf))

    payload1 = _make_payload(101, "Export broken again", "csv export still not working")
    resp1 = client.post("/webhooks/zendesk", json=payload1)
    body1 = resp1.json()
    print(json.dumps(body1["write_back"], indent=2))
    assert body1["write_back"]["action"] == "auto_written_back"
    assert test_kb.count() == initial_count + 1

    matches = test_kb.query("export broken", top_k=5)
    written = next(m for m in matches if m["ticket_id"] == "101")
    assert written["origin"] == "auto_resolved"
    assert written["verified"] is False
    print("✅ Auto-resolved ticket correctly written back as unverified precedent")

    print("\n" + "=" * 60)
    print("TEST 2: Escalated ticket is NOT written back, held as pending")
    print("=" * 60)
    low_conf = {
        "matched_ticket_id": None, "confidence": 15, "reasoning": "no real match",
        "recommended_action": "escalate_to_human", "customer_facing_draft": None,
    }
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(low_conf))

    payload2 = _make_payload(202, "Totally novel problem", "something nobody has seen before")
    count_before_escalation = test_kb.count()
    resp2 = client.post("/webhooks/zendesk", json=payload2)
    body2 = resp2.json()
    print(json.dumps(body2["write_back"], indent=2))
    assert body2["write_back"]["action"] == "recorded_as_pending_escalation"
    assert test_kb.count() == count_before_escalation  # unchanged - not written back yet
    assert pending_store.get("202") is not None
    print("✅ Escalated ticket correctly held as pending, KB unchanged")

    print("\n" + "=" * 60)
    print("TEST 3: Resolving the pending escalation writes it back as human_verified")
    print("=" * 60)
    resolve_resp = client.post("/tickets/202/resolve", json={"resolution": "Turned out to be a config typo, fixed manually."})
    print(f"Status: {resolve_resp.status_code}")
    resolve_body = resolve_resp.json()
    print(json.dumps(resolve_body, indent=2))
    assert resolve_resp.status_code == 200
    assert resolve_body["origin"] == "human_verified"
    assert test_kb.count() == count_before_escalation + 1
    assert pending_store.get("202") is None

    resolved_matches = test_kb.query("totally novel problem", top_k=5)
    resolved_entry = next(m for m in resolved_matches if m["ticket_id"] == "202")
    assert resolved_entry["origin"] == "human_verified"
    assert resolved_entry["verified"] is True
    print("✅ Human resolution correctly written back as verified precedent, removed from pending")

    print("\n" + "=" * 60)
    print("TEST 4: Resolving an unknown/already-resolved ticket returns 404")
    print("=" * 60)
    resp4 = client.post("/tickets/202/resolve", json={"resolution": "trying again"})  # already resolved above
    print(f"Status: {resp4.status_code}")
    assert resp4.status_code == 404
    resp5 = client.post("/tickets/NEVER-EXISTED/resolve", json={"resolution": "x"})
    print(f"Status: {resp5.status_code}")
    assert resp5.status_code == 404
    print("✅ Both already-resolved and never-escalated tickets correctly return 404")

    app.dependency_overrides.clear()
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    print("\n" + "=" * 60)
    print("ALL PHASE 7 (WRITE-BACK LOOP) TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run()
