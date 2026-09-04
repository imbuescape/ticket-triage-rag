"""
ADF construction - the reverse of app/sources/jira.py's ADF parser.

Phase 1 built _extract_text_from_adf: rich-text JSON tree -> plain text.
Posting comments/descriptions to Jira needs the opposite direction:
plain text -> rich-text JSON tree, because Jira Cloud's API rejects a
bare string for description/comment bodies - it requires the ADF
document structure.

Kept deliberately simple: one paragraph per line of input text. Jira
supports much richer ADF (bold, bullet lists, mentions, code blocks),
but building minimal valid ADF is enough for auto-generated triage notes
and comments - no need for rich formatting here.
"""


def text_to_adf(text: str) -> dict:
    """Convert plain text into a minimal valid ADF document (one paragraph per line)."""
    lines = text.split("\n") if text else [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}] if line else [],
            }
            for line in lines
        ],
    }
