
"""
Phase 6b: idempotency, and two genuinely different situations underneath it.

JIRA: has a real answer. Every Jira Cloud webhook includes
X-Atlassian-Webhook-Identifier - confirmed in Atlassian's own docs as
"unique within a Jira Cloud tenant and the same across retries." This is
exactly what an idempotency key should be: stable, provided by the
source, no guessing required.

ZENDESK: does NOT provide one. Checked their docs directly - the only
"invocation id" they mention is queryable via THEIR OWN monitoring API
(GET /api/v2/webhooks/{id}/invocations/), never included in the request
they actually send us. Their own official guidance is just "use webhook
signatures" - but that's fragile to build on: their signature is
computed over (timestamp + body), and if a retry gets a freshly
generated timestamp, the signature would differ across retries of the
SAME event, silently making signature-based dedup do nothing. Instead,
this hashes the RAW BODY directly - a genuine retry resends the same
payload bytes, so the hash is stable regardless of what Zendesk does
with the timestamp on its end. The tradeoff, stated plainly rather than
hidden: two DIFFERENT events with byte-identical bodies would
incorrectly collide. Unlikely in practice (most payloads carry a
distinguishing timestamp or sequence field), but a real limitation of
working around a gap in what Zendesk actually sends.
"""

import hashlib
from typing import Protocol


class IdempotencyStore(Protocol):
    def is_duplicate(self, key: str) -> bool: ...
    def mark_processed(self, key: str) -> None: ...


class InMemoryIdempotencyStore:
    """
    Process-local only - resets on restart, and does NOT work across
    multiple server processes/instances (e.g. if you ever run this
    behind a load balancer with several workers). A real production
    deployment needs a SHARED store (Redis with a TTL is the standard
    choice here) so idempotency holds across restarts and horizontal
    scaling. Flagged here rather than quietly pretended away - this is
    fine for a single local dev instance, not for production as-is.
    """

    def __init__(self):
        self._seen: set[str] = set()

    def is_duplicate(self, key: str) -> bool:
        return key in self._seen

    def mark_processed(self, key: str) -> None:
        self._seen.add(key)


def compute_zendesk_idempotency_key(raw_body: bytes) -> str:
    return f"zendesk:{hashlib.sha256(raw_body).hexdigest()}"


def compute_jira_idempotency_key(webhook_identifier: str) -> str:
    return f"jira:{webhook_identifier}"
