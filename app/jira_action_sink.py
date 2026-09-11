"""
Real Jira posting: internal triage notes, and resolution comments for
auto-resolved Jira-sourced tickets.

Two distinct cases for post_internal_triage_note, because a Jira-sourced
ticket and a Zendesk-sourced ticket are in fundamentally different states
when they need an internal triage note:

  1. ticket.source == "jira": there's already a real Jira issue
     (ticket.source_id is a real issue key like "ENG-193") - we COMMENT
     on it.
  2. ticket.source == "zendesk": there's no Jira issue yet - a human
     needs one CREATED so they have something to triage in their normal
     workflow.

Design: payload-building is split into standalone functions
(build_comment_payload, build_resolution_comment_payload,
build_new_issue_payload) that take plain data and return plain dicts -
no network, no auth, fully unit-testable without credentials. The
actual HTTP calls are thin methods that call requests.post with
whatever payload was built.

This class no longer implements post_customer_reply - that used to
delegate to a mock, back when there was no real Zendesk destination.
Now that ZendeskActionSink exists, the two real destinations are
composed via CompositeActionSink in app/router.py, and this class stays
focused on exactly one job: Jira-side posting.
"""

import os
import requests

from app.models import Ticket
from app.llm_judge import TriageDecision
from app.adf import text_to_adf

JIRA_ISSUE_TYPE = os.environ.get("JIRA_ISSUE_TYPE", "Task")


def build_comment_payload(decision: TriageDecision, matches: list[dict]) -> dict:
    """Pure function: decision + matches -> Jira 'add comment' request body."""
    candidates_text = "\n".join(
        f"- {m['ticket_id']} (distance {m['distance']:.4f}): {m['subject']}"
        for m in matches
    )
    comment_text = (
        f"[Automated triage]\n"
        f"Recommended action: {decision.recommended_action}\n"
        f"Confidence: {decision.confidence}\n"
        f"Matched ticket: {decision.matched_ticket_id or 'none'}\n\n"
        f"Reasoning: {decision.reasoning}\n\n"
        f"Candidates considered:\n{candidates_text}"
    )
    return {"body": text_to_adf(comment_text)}


def build_resolution_comment_payload(draft: str) -> dict:
    """Pure function: an auto-resolution draft -> Jira 'add comment' request body,
    used when a JIRA-SOURCED ticket gets auto-resolved (see post_resolution_comment)."""
    comment_text = f"[Auto-resolved]\n\n{draft}"
    return {"body": text_to_adf(comment_text)}


def build_new_issue_payload(
    ticket: Ticket, decision: TriageDecision, matches: list[dict], project_key: str
) -> dict:
    """Pure function: ticket + decision + matches -> Jira 'create issue' request body."""
    candidates_text = "\n".join(
        f"- {m['ticket_id']} (distance {m['distance']:.4f}): {m['subject']}"
        for m in matches
    )
    description_text = (
        f"Auto-triaged from {ticket.source} ticket {ticket.source_id}.\n\n"
        f"Original subject: {ticket.subject}\n"
        f"Original description: {ticket.description}\n\n"
        f"Judge confidence: {decision.confidence} (below auto-resolve threshold)\n"
        f"Judge reasoning: {decision.reasoning}\n\n"
        f"Candidates considered:\n{candidates_text}"
    )
    return {
        "fields": {
            "project": {"key": project_key},
            "summary": f"[Needs triage] {ticket.subject}",
            "description": text_to_adf(description_text),
            "issuetype": {"name": JIRA_ISSUE_TYPE},
        }
    }


class JiraActionSink:
    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        project_key: str | None = None,
    ):
        self._base_url = (base_url or os.environ["JIRA_BASE_URL"]).rstrip("/")
        self._auth = (email or os.environ["JIRA_EMAIL"], api_token or os.environ["JIRA_API_TOKEN"])
        self._project_key = project_key or os.environ["JIRA_PROJECT_KEY"]

    def post_internal_triage_note(
        self, ticket: Ticket, decision: TriageDecision, matches: list[dict]
    ) -> dict:
        if ticket.source == "jira":
            payload = build_comment_payload(decision, matches)
            return self._post_comment(ticket.source_id, payload)
        else:
            return self._create_new_issue(ticket, decision, matches)

    def post_resolution_comment(self, ticket: Ticket, draft: str) -> dict:
        """
        Called for a JIRA-SOURCED ticket that got AUTO-RESOLVED. There's
        no real "customer" to reply to on a raw engineering bug report -
        the closest sensible equivalent is commenting the resolution
        directly onto the issue that was already open.
        """
        payload = build_resolution_comment_payload(draft)
        return self._post_comment(ticket.source_id, payload)

    def _post_comment(self, issue_key: str, payload: dict) -> dict:
        resp = requests.post(
            f"{self._base_url}/rest/api/3/issue/{issue_key}/comment",
            json=payload,
            auth=self._auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        print(f"    [JIRA] Posted comment on {issue_key} (comment id {body.get('id')})")
        return {"sink": "jira_real", "action": "comment_posted", "issue_key": issue_key, "comment_id": body.get("id")}

    def _create_new_issue(
        self, ticket: Ticket, decision: TriageDecision, matches: list[dict]
    ) -> dict:
        payload = build_new_issue_payload(ticket, decision, matches, self._project_key)
        resp = requests.post(
            f"{self._base_url}/rest/api/3/issue",
            json=payload,
            auth=self._auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        print(f"    [JIRA] Created new issue {body.get('key')} for {ticket.source} ticket {ticket.source_id}")
        return {"sink": "jira_real", "action": "issue_created", "issue_key": body.get("key")}