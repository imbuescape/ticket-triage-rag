"""
Run: venv/bin/python tests/test_zendesk_action_sink.py

Tests the pure payload builder only - no network, no credentials.
Real posting is exercised separately via a guarded smoke test script,
same pattern as Jira's real posting.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.zendesk_action_sink import build_comment_payload


def run():
    print("=" * 60)
    print("TEST 1: build_comment_payload defaults to public=True")
    print("=" * 60)
    payload = build_comment_payload("Here's the fix.")
    print(payload)
    assert payload["ticket"]["comment"]["public"] is True
    assert payload["ticket"]["comment"]["body"] == "Here's the fix."
    print("✅ public defaults to True - customer-facing by default, as intended")

    print("\n" + "=" * 60)
    print("TEST 2: public=False is respected when explicitly requested")
    print("=" * 60)
    private_payload = build_comment_payload("internal note text", public=False)
    assert private_payload["ticket"]["comment"]["public"] is False
    print("✅ Explicit public=False correctly produces an internal-only comment payload")

    print("\n" + "=" * 60)
    print("ALL ZENDESK ACTION SINK TESTS PASSED (no network needed)")
    print("=" * 60)


if __name__ == "__main__":
    run()