"""
Run: venv/bin/python tests/test_phase1.py

Tests the two things we can verify without live network access:
1. Zendesk normalization against the real mock payload file.
2. Jira ADF text extraction against a realistic (but offline) Jira
   issue shape - same structure the real API would return, so this
   proves the parser works before you ever touch the live API.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.sources.zendesk import normalize_zendesk_payload
from app.sources.jira import normalize_jira_payload

# --- Test 1: Zendesk ---
print("=" * 60)
print("TEST 1: Zendesk normalization")
print("=" * 60)

with open("data/mock_zendesk_ticket.json") as f:
    zendesk_payload = json.load(f)

zendesk_ticket = normalize_zendesk_payload(zendesk_payload)
print(zendesk_ticket.model_dump_json(indent=2))
assert zendesk_ticket.source == "zendesk"
assert zendesk_ticket.source_id == "48213"
assert "Export to CSV" in zendesk_ticket.subject
print("\n Zendesk normalization passed\n")


# --- Test 2: Jira ADF parsing ---
print("=" * 60)
print("TEST 2: Jira ADF description parsing")
print("=" * 60)

sample_jira_issue = {
    "key": "ENG-193",
    "fields": {
        "summary": "Rate limiter drops requests under 50 req/s",
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Steps to reproduce:"}
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "Send 40 req/s for 60 seconds"}
                                    ],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "Observe ~12% of requests return 429"}
                                    ],
                                }
                            ],
                        },
                    ],
                },
            ],
        },
        "reporter": {"emailAddress": "eng-oncall@ourcompany.com"},
        "created": "2026-08-25T09:15:00.000+0000",
    },
}

jira_ticket = normalize_jira_payload(sample_jira_issue)
print(jira_ticket.model_dump_json(indent=2))

assert jira_ticket.source == "jira"
assert jira_ticket.source_id == "ENG-193"
assert "{" not in jira_ticket.description  # proves we didn't leak raw ADF JSON
assert "Steps to reproduce" in jira_ticket.description
assert "429" in jira_ticket.description
print("\n✅ Jira ADF parsing passed - plain text extracted, no JSON noise leaked\n")

print("=" * 60)
print("PHASE 1 COMPLETE: both sources normalize into a clean, unified Ticket.")
print("=" * 60)
