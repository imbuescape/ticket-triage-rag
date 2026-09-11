
"""
Run: venv/bin/python tests/test_idempotency.py

Part 1: pure unit tests of the store and key-computation functions.
Part 2: real integration proof through the actual FastAPI app - the
SAME payload posted twice should only trigger ONE real Groq call and
ONE real sink call, not two. Counting actual calls is the only way to
prove the dedup is doing something, not just returning a plausible-
looking response.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.idempotency import (
    InMemoryIdempotencyStore, compute_zendesk_idempotency_key, compute_jira_idempotency_key,
)


def run_unit_tests():
    print("=" * 60)
    print("TEST 1: InMemoryIdempotencyStore basic behavior")
    print("=" * 60)
    store = InMemoryIdempotencyStore()
    key = "some-key"
    assert store.is_duplicate(key) is False
    store.mark_processed(key)
    assert store.is_duplicate(key) is True
    print("✅ Unseen key -> not duplicate; after marking -> is duplicate")

    print("\n" + "=" * 60)
    print("TEST 2: Zendesk key is stable for identical bodies, differs for different bodies")
    print("=" * 60)
    body_a = b'{"ticket":{"id":1}}'
    body_a_again = b'{"ticket":{"id":1}}'
    body_b = b'{"ticket":{"id":2}}'
    key_a1 = compute_zendesk_idempotency_key(body_a)
    key_a2 = compute_zendesk_idempotency_key(body_a_again)
    key_b = compute_zendesk_idempotency_key(body_b)
    assert key_a1 == key_a2
    assert key_a1 != key_b
    print("✅ Identical body -> identical key; different body -> different key")

    print("\n" + "=" * 60)
    print("TEST 3: Jira key uses the real X-Atlassian-Webhook-Identifier directly")
    print("=" * 60)
    key1 = compute_jira_idempotency_key("abc-123")
    key2 = compute_jira_idempotency_key("abc-123")
    key3 = compute_jira_idempotency_key("xyz-999")
    assert key1 == key2
    assert key1 != key3
    print("✅ Same identifier -> same key; different identifier -> different key")

    print("\n" + "=" * 60)
    print("UNIT TESTS PASSED")
    print("=" * 60)


def run_integration_test():
    import json
    import shutil
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))

    from fastapi.testclient import TestClient
    from app.main import (
        app, get_kb, get_judge, get_action_sink, get_idempotency_store,
        require_valid_zendesk_signature, require_valid_jira_signature,
    )
    from app.knowledge_base import ResolvedTicketKB
    from app.llm_judge import TriageJudge
    from tests.test_knowledge_base import DeterministicTestEmbedder
    from tests.test_llm_judge import FakeGroqClient
    from tests.test_router import SpyActionSink
    from tests.test_webhook_auth import passthrough_auth

    TEST_DB_PATH = "/tmp/test_idempotency_chroma"
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    test_kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)
    test_kb.add_resolved_ticket(
        ticket_id="ZD-100", subject="CSV export button unresponsive",
        description="csv export broken", resolution="clear cache",
    )
    canned = {
        "matched_ticket_id": "ZD-100", "confidence": 90, "reasoning": "match",
        "recommended_action": "auto_resolve", "customer_facing_draft": "fix",
    }
    fake_groq_client = FakeGroqClient(canned)
    test_judge = TriageJudge(groq_client=fake_groq_client)
    test_sink = SpyActionSink()
    # Real, SHARED store - deliberately NOT overridden with a fresh
    # instance per call, because this test needs the dedup to actually
    # persist across the two requests it's about to make.
    real_store = InMemoryIdempotencyStore()

    app.dependency_overrides[get_kb] = lambda: test_kb
    app.dependency_overrides[get_judge] = lambda: test_judge
    app.dependency_overrides[get_action_sink] = lambda: test_sink
    app.dependency_overrides[get_idempotency_store] = lambda: real_store
    app.dependency_overrides[require_valid_zendesk_signature] = passthrough_auth
    app.dependency_overrides[require_valid_jira_signature] = passthrough_auth

    client = TestClient(app)
    with open("data/mock_zendesk_ticket.json") as f:
        payload_dict = json.load(f)

    print("\n" + "=" * 60)
    print("INTEGRATION TEST: identical payload posted TWICE")
    print("=" * 60)

    print("\n--- First request ---")
    resp1 = client.post("/webhooks/zendesk", json=payload_dict)
    body1 = resp1.json()
    print(json.dumps(body1, indent=2)[:300] + "...")
    assert resp1.status_code == 200
    assert body1["status"] == "received"
    assert body1["decision"] is not None
    print(f"Groq calls so far: {fake_groq_client.chat.completions.call_count}")
    assert fake_groq_client.chat.completions.call_count == 1

    print("\n--- Second request (byte-identical payload) ---")
    resp2 = client.post("/webhooks/zendesk", json=payload_dict)
    body2 = resp2.json()
    print(json.dumps(body2, indent=2))
    assert resp2.status_code == 200
    assert body2["status"] == "duplicate_skipped"
    print(f"Groq calls after second (duplicate) request: {fake_groq_client.chat.completions.call_count}")
    assert fake_groq_client.chat.completions.call_count == 1, \
        "Groq was called AGAIN on a duplicate - idempotency is not actually preventing reprocessing!"
    assert len(test_sink.customer_reply_calls) == 1, \
        "Sink was called AGAIN on a duplicate - a customer would have been messaged twice!"

    print("\n✅ Second identical request was correctly skipped - Groq called exactly once, "
          "sink called exactly once, despite two requests hitting the endpoint")

    app.dependency_overrides.clear()
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    print("\n" + "=" * 60)
    print("ALL IDEMPOTENCY INTEGRATION TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run_unit_tests()
    run_integration_test()
