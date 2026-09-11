"""
Demo action sink - implements all three sink methods so it can be
wrapped in the SAME CompositeActionSink used for real traffic, meaning
the demo UI exercises the exact same source-aware routing logic as
production (Jira-sourced auto-resolve -> Jira comment, not a fake
Zendesk reply) rather than a simplified stand-in that could silently
drift out of sync with real behavior.

Never makes a network call - describes what WOULD happen instead, so
the demo UI is safe to use freely without touching real Zendesk/Jira
credentials or posting anything visible to a real customer or engineer.
"""


class DemoActionSink:
    def post_customer_reply(self, ticket, draft: str) -> dict:
        return {
            "sink": "demo", "action": "would_post_zendesk_customer_reply",
            "ticket_id": ticket.source_id, "preview": draft,
        }

    def post_internal_triage_note(self, ticket, decision, matches: list[dict]) -> dict:
        target = "existing Jira issue" if ticket.source == "jira" else "a NEW Jira issue"
        return {
            "sink": "demo", "action": f"would_create_triage_note_on_{target.replace(' ', '_')}",
            "ticket_id": ticket.source_id,
        }

    def post_resolution_comment(self, ticket, draft: str) -> dict:
        return {
            "sink": "demo", "action": "would_post_jira_resolution_comment",
            "ticket_id": ticket.source_id, "preview": draft,
        }
