"""
Run: venv/bin/python tests/test_webhook_auth.py

Pure logic tests - no FastAPI, no network, no env vars. Confirms both
that VALID signatures pass, and that TAMPERED bodies/signatures/missing
headers all correctly get rejected - a verifier that only ever proves
it accepts good input hasn't actually proven it rejects bad input.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import Request
from app.webhook_auth import (
    compute_zendesk_signature, verify_zendesk_signature,
    compute_jira_signature, verify_jira_signature,
)


async def passthrough_auth(request: Request) -> bytes:
    """
    Reusable override for tests that aren't testing auth itself (Phase 3/4/5
    integration tests) - skips signature verification entirely and just
    returns the raw body, so those tests don't need to compute real
    signatures for every payload just to reach the logic they actually
    care about.
    """
    return await request.body()


def run():
    print("=" * 60)
    print("ZENDESK: valid signature verifies correctly")
    print("=" * 60)
    secret = "test_secret_123"
    body = b'{"ticket":{"id":1,"subject":"test"}}'
    timestamp = "2026-08-31T12:00:00Z"

    sig = compute_zendesk_signature(body, timestamp, secret)
    print(f"Computed signature: {sig}")
    assert verify_zendesk_signature(body, timestamp, sig, secret) is True
    print("✅ Valid signature accepted")

    print("\n" + "=" * 60)
    print("ZENDESK: tampered body is rejected")
    print("=" * 60)
    tampered_body = b'{"ticket":{"id":1,"subject":"HACKED"}}'
    assert verify_zendesk_signature(tampered_body, timestamp, sig, secret) is False
    print("✅ Tampered body correctly rejected (signature no longer matches)")

    print("\n" + "=" * 60)
    print("ZENDESK: wrong secret is rejected")
    print("=" * 60)
    assert verify_zendesk_signature(body, timestamp, sig, "wrong_secret") is False
    print("✅ Wrong secret correctly rejected")

    print("\n" + "=" * 60)
    print("ZENDESK: missing signature/timestamp rejected, not treated as valid")
    print("=" * 60)
    assert verify_zendesk_signature(body, timestamp, "", secret) is False
    assert verify_zendesk_signature(body, "", sig, secret) is False
    print("✅ Missing headers correctly rejected rather than defaulting to accept")

    print("\n" + "=" * 60)
    print("JIRA: valid signature verifies correctly")
    print("=" * 60)
    jira_secret = "jira_test_secret"
    jira_body = b'{"issue":{"key":"ENG-193"}}'
    jira_sig = compute_jira_signature(jira_body, jira_secret)
    print(f"Computed signature: {jira_sig}")
    assert jira_sig.startswith("sha256=")
    assert verify_jira_signature(jira_body, jira_sig, jira_secret) is True
    print("✅ Valid signature accepted, correctly prefixed with 'sha256='")

    print("\n" + "=" * 60)
    print("JIRA: tampered body is rejected")
    print("=" * 60)
    tampered_jira_body = b'{"issue":{"key":"HACKED-1"}}'
    assert verify_jira_signature(tampered_jira_body, jira_sig, jira_secret) is False
    print("✅ Tampered body correctly rejected")

    print("\n" + "=" * 60)
    print("JIRA: wrong secret is rejected")
    print("=" * 60)
    assert verify_jira_signature(jira_body, jira_sig, "wrong_secret") is False
    print("✅ Wrong secret correctly rejected")

    print("\n" + "=" * 60)
    print("ALL PURE SIGNATURE TESTS PASSED")
    print("=" * 60)


def run_integration_test():
    """
    Proves the auth dependency is ACTUALLY WIRED IN, not just correct in
    isolation - a valid signature reaches the pipeline, an invalid one
    gets a 401 before any KB/judge/router code ever runs.
    """
    import json
    import shutil
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))

    os.environ["ZENDESK_WEBHOOK_SECRET"] = "integration_test_secret"
    os.environ["JIRA_WEBHOOK_SECRET"] = "integration_test_jira_secret"

    from fastapi.testclient import TestClient
    from app.main import app, get_kb, get_judge, get_action_sink
    from app.knowledge_base import ResolvedTicketKB
    from app.llm_judge import TriageJudge
    from tests.test_knowledge_base import DeterministicTestEmbedder
    from tests.test_llm_judge import FakeGroqClient
    from tests.test_router import SpyActionSink

    TEST_DB_PATH = "/tmp/test_webhook_auth_chroma"
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
    app.dependency_overrides[get_kb] = lambda: test_kb
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(canned))
    app.dependency_overrides[get_action_sink] = lambda: SpyActionSink()

    client = TestClient(app)
    with open("data/mock_zendesk_ticket.json") as f:
        payload_dict = json.load(f)
    raw_body = json.dumps(payload_dict).encode("utf-8")

    print("\n" + "=" * 60)
    print("INTEGRATION TEST 1: valid Zendesk signature reaches the pipeline")
    print("=" * 60)
    timestamp = "2026-08-31T12:00:00Z"
    sig = compute_zendesk_signature(raw_body, timestamp, os.environ["ZENDESK_WEBHOOK_SECRET"])
    resp = client.post(
        "/webhooks/zendesk",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Zendesk-Webhook-Signature": sig,
            "X-Zendesk-Webhook-Signature-Timestamp": timestamp,
        },
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 200
    assert resp.json()["source_id"] == "48213"
    print("✅ Valid signature -> request processed normally")

    print("\n" + "=" * 60)
    print("INTEGRATION TEST 2: invalid Zendesk signature gets 401, pipeline never runs")
    print("=" * 60)
    resp2 = client.post(
        "/webhooks/zendesk",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Zendesk-Webhook-Signature": "totally-forged-signature",
            "X-Zendesk-Webhook-Signature-Timestamp": timestamp,
        },
    )
    print(f"Status: {resp2.status_code}")
    assert resp2.status_code == 401
    print("✅ Forged signature correctly rejected with 401")

    print("\n" + "=" * 60)
    print("INTEGRATION TEST 3: missing signature headers entirely gets 401")
    print("=" * 60)
    resp3 = client.post("/webhooks/zendesk", content=raw_body, headers={"Content-Type": "application/json"})
    print(f"Status: {resp3.status_code}")
    assert resp3.status_code == 401
    print("✅ Missing headers correctly rejected with 401")

    app.dependency_overrides.clear()
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    print("\n" + "=" * 60)
    print("ALL WEBHOOK AUTH INTEGRATION TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run()
    run_integration_test()