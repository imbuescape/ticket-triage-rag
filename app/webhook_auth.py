"""
Webhook signature verification - two genuinely different mechanisms.

Zendesk: base64(HMAC-SHA256(timestamp + raw_body, secret)), sent in
X-Zendesk-Webhook-Signature, with the timestamp in a separate header
(X-Zendesk-Webhook-Signature-Timestamp). The timestamp is included in
the signed content specifically to prevent replay attacks - an attacker
who captures a valid request can't just resend it forever, since you can
also check the timestamp is recent (not implemented here yet, but the
hook is there if you want to add a max-age check later).

Jira Cloud: hex(HMAC-SHA256(raw_body, secret)), prefixed "sha256=", sent
in X-Hub-Signature (following the WebSub standard - notice it does NOT
include a timestamp, so Jira's scheme alone doesn't protect against
replay the way Zendesk's does).

Both verifications are PURE FUNCTIONS (bytes/strings in, bool out) - no
FastAPI, no environment variables, no network. That's what makes them
directly unit-testable, including with deliberately-wrong signatures to
confirm rejection actually works, not just acceptance of a good one.

Both use hmac.compare_digest for the final comparison - a naive `==`
string comparison leaks timing information proportional to how many
leading bytes matched, which is a real (if narrow) attack vector against
naive implementations.
"""

import hmac
import hashlib
import base64


def compute_zendesk_signature(raw_body: bytes, timestamp: str, secret: str) -> str:
    signed_content = (timestamp + raw_body.decode("utf-8")).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed_content, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_zendesk_signature(raw_body: bytes, timestamp: str, provided_signature: str, secret: str) -> bool:
    if not timestamp or not provided_signature:
        return False
    expected = compute_zendesk_signature(raw_body, timestamp, secret)
    return hmac.compare_digest(expected, provided_signature)


def compute_jira_signature(raw_body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_jira_signature(raw_body: bytes, provided_signature_header: str, secret: str) -> bool:
    if not provided_signature_header:
        return False
    expected = compute_jira_signature(raw_body, secret)
    return hmac.compare_digest(expected, provided_signature_header)
