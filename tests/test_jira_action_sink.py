"""
Run: venv/bin/python tests/test_jira_action_sink.py

Tests the PURE payload-building functions (no network, no credentials).
Actually hitting the real Jira API is a separate, deliberate step - see
scripts/jira_posting_smoke_test.py, which you run on your own machine.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.jira_action_sink import build_comment_payload, build_new_issue_payload, JIRA_ISSUE_TYPE
from app.llm_judge import TriageDecision
from app.models import Ticket
from app.adf import text_to_adf
from datetime import datetime


def run():
    print("=" * 60)
    print("TEST 1: build_comment_payload produces valid ADF structure")
    print("=" * 60)
    decision = TriageDecision(
        matched_ticket_id="ZD-100", confidence=88, reasoning="Same root cause.",
        recommended_action="auto_resolve", customer_facing_draft="fix text",
    )
    matches = [{"ticket_id": "ZD-100", "distance": 0.05, "subject": "CSV export bug"}]

    payload = build_comment_payload(decision, matches)
    print(payload)

    assert "body" in payload
    assert payload["body"]["type"] == "doc"
    assert payload["body"]["version"] == 1
    # confirm decision content actually made it into the comment text
    all_text = " ".join(
        span["text"]
        for para in payload["body"]["content"]
        for span in para["content"]
    )
    assert "ZD-100" in all_text
    assert "88" in all_text
    assert "Same root cause" in all_text
    print("✅ Comment payload is valid ADF and contains the decision content")

    print("\n" + "=" * 60)
    print("TEST 2: build_new_issue_payload produces correct Jira issue shape")
    print("=" * 60)
    ticket = Ticket(
        source="zendesk", source_id="48213", subject="Export to CSV button does nothing",
        description="Nothing happens when I click export.", created_at=datetime.now(),
    )
    escalate_decision = TriageDecision(
        matched_ticket_id=None, confidence=30, reasoning="No candidate genuinely applies.",
        recommended_action="escalate_to_human", customer_facing_draft=None,
    )
    issue_payload = build_new_issue_payload(ticket, escalate_decision, matches, project_key="ENG")
    print(issue_payload)

    assert issue_payload["fields"]["project"]["key"] == "ENG"
    assert issue_payload["fields"]["issuetype"]["name"] == JIRA_ISSUE_TYPE
    assert "48213" in issue_payload["fields"]["summary"] or "Export to CSV" in issue_payload["fields"]["summary"]
    assert issue_payload["fields"]["description"]["type"] == "doc"
    print("✅ New-issue payload has correct project, issue type, and summary")

    print("\n" + "=" * 60)
    print("TEST 3: ADF round-trip - what we build, we can also parse back")
    print("=" * 60)
    from app.sources.jira import _extract_text_from_adf  # Phase 1's parser
    original_text = "Line one.\nLine two with detail.\nLine three."
    adf = text_to_adf(original_text)
    recovered_text = _extract_text_from_adf(adf)
    print(f"Original:  {original_text!r}")
    print(f"Recovered: {recovered_text!r}")
    assert "Line one." in recovered_text
    assert "Line two with detail." in recovered_text
    assert "Line three." in recovered_text
    print("✅ text_to_adf and _extract_text_from_adf are correctly symmetric")

    print("\n" + "=" * 60)
    print("ALL JIRA ACTION SINK TESTS PASSED (no network needed)")
    print("=" * 60)


if __name__ == "__main__":
    run()
