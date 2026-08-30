"""
Jira is the case that actually teaches something.

Jira Cloud's 'description' field is NOT a plain string. It's stored as
Atlassian Document Format (ADF) - a JSON tree describing rich text:
paragraphs, bold text, bullet lists, code blocks, mentions, etc.

A raw Jira issue field looks roughly like:

  "description": {
    "type": "doc",
    "version": 1,
    "content": [
      {
        "type": "paragraph",
        "content": [
          {"type": "text", "text": "Steps to reproduce:"}
        ]
      },
      {
        "type": "bulletList",
        "content": [ ... ]
      }
    ]
  }

If you naively do str(issue["fields"]["description"]) and feed THAT into
an embedding model, you're embedding Python dict repr syntax
("{'type': 'doc', 'version': 1, ...") mixed with your actual ticket text.
The embedding model will produce a garbage vector - it's now half text,
half JSON structure noise. This is a classic silent bug: the pipeline
runs without errors, but retrieval quality quietly degrades.

So: we walk the ADF tree and extract just the text nodes.
"""

from app.models import Ticket


def _extract_text_from_adf(node: dict) -> str:
    """Recursively pull plain text out of an Atlassian Document Format tree."""
    if not isinstance(node, dict):
        return ""

    text_parts = []

    if node.get("type") == "text":
        text_parts.append(node.get("text", ""))

    for child in node.get("content", []):
        text_parts.append(_extract_text_from_adf(child))

    # bullet/numbered list items and paragraphs should read on their own line
    joiner = "\n" if node.get("type") in ("paragraph", "listItem") else " "
    return joiner.join(p for p in text_parts if p)


def normalize_jira_payload(payload: dict) -> Ticket:
    """
    Accepts a Jira REST API v3 issue object (same shape whether it comes
    from GET /rest/api/3/issue/{key} or a webhook 'issue' payload).
    """
    fields = payload["fields"]

    description_field = fields.get("description")
    if isinstance(description_field, dict):
        description = _extract_text_from_adf(description_field)
    elif isinstance(description_field, str):
        description = description_field  # older Jira Server/DC APIs use plain text
    else:
        description = ""

    reporter = fields.get("reporter") or {}

    return Ticket(
        source="jira",
        source_id=payload["key"],
        subject=fields["summary"],
        description=description.strip(),
        requester_email=reporter.get("emailAddress"),
        created_at=fields["created"],
        raw=payload,
    )
