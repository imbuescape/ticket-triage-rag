"""
Run: venv/bin/python tests/test_phase3.py

Proves: a webhook POST triggers normalization -> embedding -> KB query
-> matches returned in the response. Uses dependency override to swap
in a KB backed by the deterministic test embedder, so this needs zero
network access and runs identically here or on your machine.
"""

import sys
import os
import json
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from app.main import app, get_kb
from app.knowledge_base import ResolvedTicketKB
from tests.test_knowledge_base import DeterministicTestEmbedder

TEST_DB_PATH = "/tmp/test_phase3_chroma"


def override_get_kb():
    """This replaces the real get_kb() dependency for the duration of the test."""
    return _test_kb


def run():
    global _test_kb

    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    _test_kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)

    # Seed with one ticket that should match our incoming mock Zendesk payload
    _test_kb.add_resolved_ticket(
        ticket_id="ZD-100",
        subject="CSV export button unresponsive",
        description="User clicks export to csv on reports page, nothing happens, no error shown",
        resolution="Known bug in report cache. Fix: clear browser cache.",
    )

    app.dependency_overrides[get_kb] = override_get_kb
    client = TestClient(app)

    with open("data/mock_zendesk_ticket.json") as f:
        payload = json.load(f)

    print("=" * 60)
    print("TEST: POST /webhooks/zendesk triggers retrieval")
    print("=" * 60)

    resp = client.post("/webhooks/zendesk", json=payload)
    print("Status:", resp.status_code)
    body = resp.json()
    print(json.dumps(body, indent=2))

    assert resp.status_code == 200
    assert body["source_id"] == "48213"
    assert len(body["matches"]) >= 1
    assert body["matches"][0]["ticket_id"] == "ZD-100"

    print("\n✅ Webhook -> normalize -> retrieve pipeline works end to end.")

    app.dependency_overrides.clear()
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)


if __name__ == "__main__":
    run()
