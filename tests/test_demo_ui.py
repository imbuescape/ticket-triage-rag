"""
Run: venv/bin/python tests/test_demo_ui.py

Proves the /demo page and /demo/triage endpoint work correctly:
1. GET /demo returns real HTML with the expected form elements.
2. POST /demo/triage runs the REAL pipeline (retrieval, judge, routing,
   write-back) with a synthetic ticket, using the demo sink instead of
   real Zendesk/Jira calls.
3. Auto-resolve case: written back correctly, demo sink reports what it
   WOULD have done rather than making a real call.
4. Escalate case: held pending, then resolved via the real /tickets/
   {id}/resolve endpoint - proving the demo path and the real webhook
   path share the exact same underlying write-back logic.
5. Jira-sourced auto-resolve through the demo: proves the source-aware
   routing (Jira comment, not fake Zendesk reply) works from the UI
   entrypoint too, not just the webhook entrypoint.
"""

import sys
import os
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from app.main import app, get_kb, get_judge, get_demo_action_sink, get_pending_escalation_store
from app.knowledge_base import ResolvedTicketKB
from app.llm_judge import TriageJudge
from app.write_back import InMemoryPendingEscalationStore
from tests.test_knowledge_base import DeterministicTestEmbedder
from tests.test_llm_judge import FakeGroqClient
from tests.test_router import SpyActionSink

TEST_DB_PATH = "/tmp/test_demo_ui_chroma"

HIGH_CONF_AUTO_RESOLVE = {
    "matched_ticket_id": "ZD-100", "confidence": 90, "reasoning": "Same root cause: report cache bug.",
    "recommended_action": "auto_resolve", "customer_facing_draft": "Here's the fix: clear your browser cache.",
}
LOW_CONF_ESCALATE = {
    "matched_ticket_id": None, "confidence": 20, "reasoning": "No candidate genuinely applies.",
    "recommended_action": "escalate_to_human", "customer_facing_draft": None,
}


def run():
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)
    kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)
    kb.add_resolved_ticket(
        ticket_id="ZD-100", subject="CSV export button unresponsive",
        description="csv export broken", resolution="clear cache",
    )
    pending_store = InMemoryPendingEscalationStore()

    app.dependency_overrides[get_kb] = lambda: kb
    app.dependency_overrides[get_pending_escalation_store] = lambda: pending_store

    client = TestClient(app)

    print("=" * 70)
    print("TEST 1: GET /demo returns real HTML with expected elements")
    print("=" * 70)
    resp = client.get("/demo")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "<form" in html or "submitTicket" in html  # our page uses a button+JS, not a <form> tag
    assert "subject" in html
    assert "description" in html
    assert "/demo/triage" in html
    print("✅ /demo returns a real HTML page with the expected form/JS wiring")

    print("\n" + "=" * 70)
    print("TEST 2: POST /demo/triage - auto-resolve case (Zendesk source)")
    print("=" * 70)
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(HIGH_CONF_AUTO_RESOLVE))
    demo_spy = SpyActionSink()
    app.dependency_overrides[get_demo_action_sink] = lambda: demo_spy

    resp2 = client.post("/demo/triage", json={
        "subject": "Export broken", "description": "csv export not working", "source": "zendesk",
    })
    body2 = resp2.json()
    assert resp2.status_code == 200, body2
    assert body2["decision"]["recommended_action"] == "auto_resolve"
    assert body2["write_back"]["action"] == "auto_written_back"
    assert len(demo_spy.customer_reply_calls) == 1  # demo sink recorded it, no real network call happened
    print("✅ Demo auto-resolve ran the real pipeline, wrote back to KB, demo sink recorded the call")

    print("\n" + "=" * 70)
    print("TEST 3: POST /demo/triage - escalate case, then resolve via real endpoint")
    print("=" * 70)
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(LOW_CONF_ESCALATE))
    count_before = kb.count()

    resp3 = client.post("/demo/triage", json={
        "subject": "Totally novel issue", "description": "never seen this before", "source": "zendesk",
    })
    body3 = resp3.json()
    assert resp3.status_code == 200, body3
    assert body3["write_back"]["action"] == "recorded_as_pending_escalation"
    assert kb.count() == count_before  # not written back yet
    demo_ticket_id = body3["source_id"]
    assert demo_ticket_id.startswith("DEMO-")
    print(f"Escalated demo ticket assigned id: {demo_ticket_id}")

    resolve_resp = client.post(f"/tickets/{demo_ticket_id}/resolve", json={"resolution": "Turned out to be a typo."})
    assert resolve_resp.status_code == 200, resolve_resp.json()
    assert kb.count() == count_before + 1
    matches = kb.query("totally novel issue", top_k=5)
    resolved = next(m for m in matches if m["ticket_id"] == demo_ticket_id)
    assert resolved["origin"] == "human_verified" and resolved["verified"] is True
    print("✅ Demo escalation resolved via the SAME real /tickets/{id}/resolve endpoint used by real webhooks")

    print("\n" + "=" * 70)
    print("TEST 4: POST /demo/triage - Jira-sourced auto-resolve routes correctly")
    print("=" * 70)
    app.dependency_overrides[get_judge] = lambda: TriageJudge(groq_client=FakeGroqClient(HIGH_CONF_AUTO_RESOLVE))
    demo_spy2 = SpyActionSink()
    from app.router import CompositeActionSink
    # Wrap in a REAL CompositeActionSink (customer_sink=escalation_sink=the
    # same spy, for observability) - this is what actually exercises the
    # source-aware routing logic. Overriding with a bare spy would bypass
    # that logic entirely and prove nothing about it.
    app.dependency_overrides[get_demo_action_sink] = lambda: CompositeActionSink(
        customer_sink=demo_spy2, escalation_sink=demo_spy2,
    )

    resp4 = client.post("/demo/triage", json={
        "subject": "Export bug from engineering side", "description": "csv export broken in prod", "source": "jira",
    })
    body4 = resp4.json()
    assert resp4.status_code == 200, body4
    assert len(demo_spy2.resolution_comment_calls) == 1  # routed to Jira comment, not Zendesk reply
    assert len(demo_spy2.customer_reply_calls) == 0
    print("✅ Jira-sourced demo auto-resolve correctly routed through the SAME source-aware "
          "CompositeActionSink logic used in production - Jira comment, not a fake Zendesk reply")

    app.dependency_overrides.clear()
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)

    print("\n" + "=" * 70)
    print("ALL DEMO UI TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    run()