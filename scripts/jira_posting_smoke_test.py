"""
RUN THIS ON YOUR OWN MACHINE, with JIRA_BASE_URL / JIRA_EMAIL /
JIRA_API_TOKEN / JIRA_PROJECT_KEY set.

    python -m scripts.jira_posting_smoke_test

THIS ONE HAS REAL SIDE EFFECTS - it will post a real comment on a real
issue and/or create a real issue in your Jira demo project. Both steps
require typing "yes" to confirm before anything happens, on purpose.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.sources.jira_client import fetch_recent_issues
from app.jira_action_sink import JiraActionSink
from app.llm_judge import TriageDecision
from app.models import Ticket
from datetime import datetime


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [type 'yes' to proceed, anything else to skip]: ").strip().lower() == "yes"


def main():
    sink = JiraActionSink()

    print("=" * 60)
    print("STEP 1: Comment on an existing real Jira issue")
    print("=" * 60)
    issues = fetch_recent_issues(max_results=1)
    if not issues:
        print("No issues found in your project - skipping comment test.")
    else:
        issue_key = issues[0]["key"]
        print(f"Will comment on: {issue_key} ({issues[0]['fields']['summary']})")
        if confirm("Post a real test comment on this issue?"):
            fake_decision = TriageDecision(
                matched_ticket_id="TEST-MATCH", confidence=99,
                reasoning="This is a smoke test comment from jira_posting_smoke_test.py - safe to ignore/delete.",
                recommended_action="auto_resolve", customer_facing_draft="n/a",
            )
            fake_matches = [{"ticket_id": "TEST-MATCH", "distance": 0.01, "subject": "smoke test candidate"}]
            receipt = sink.post_internal_triage_note(
                Ticket(source="jira", source_id=issue_key, subject="smoke test",
                       description="smoke test", created_at=datetime.now()),
                fake_decision, fake_matches,
            )
            print(f"Result: {receipt}")
        else:
            print("Skipped.")

    print("\n" + "=" * 60)
    print("STEP 2: Create a new real Jira issue (simulating a Zendesk escalation)")
    print("=" * 60)
    print(f"Will create a new issue in project: {os.environ['JIRA_PROJECT_KEY']}")
    if confirm("Create a real test issue?"):
        fake_ticket = Ticket(
            source="zendesk", source_id="SMOKE-TEST-1",
            subject="[SMOKE TEST] safe to delete",
            description="This issue was created by jira_posting_smoke_test.py - safe to delete.",
            created_at=datetime.now(),
        )
        fake_decision = TriageDecision(
            matched_ticket_id=None, confidence=20,
            reasoning="Smoke test - no real candidate.", recommended_action="escalate_to_human",
            customer_facing_draft=None,
        )
        receipt = sink.post_internal_triage_note(fake_ticket, fake_decision, matches=[])
        print(f"Result: {receipt}")
    else:
        print("Skipped.")

    print("\nDone.")


if __name__ == "__main__":
    main()

