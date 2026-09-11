"""
Run: venv/bin/python tests/test_zendesk_action_sink.py

Tests the pure payload builders, then the token caching/expiry logic
using an injected fake HTTP call and a controllable clock - no real
network, no real waiting, fully deterministic.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.zendesk_action_sink import (
    build_comment_payload, build_client_credentials_payload, ZendeskOAuthTokenManager,
)
from datetime import datetime, timezone, timedelta


class FakeTokenResponse:
    def __init__(self, access_token: str, expires_in: int | None):
        self._data = {"access_token": access_token, "token_type": "bearer"}
        if expires_in is not None:
            self._data["expires_in"] = expires_in

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeHttpPost:
    """Records calls and returns a new token each time it's called, so
    tests can distinguish 'reused cached token' from 'fetched a new one'."""

    def __init__(self, expires_in: int | None = 1800):
        self.call_count = 0
        self._expires_in = expires_in

    def __call__(self, url, json, headers, timeout):
        self.call_count += 1
        return FakeTokenResponse(f"token-{self.call_count}", self._expires_in)


class ControllableClock:
    def __init__(self, start: datetime):
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: int):
        self._now += timedelta(seconds=seconds)


def run():
    print("=" * 60)
    print("TEST 0: normalize_subdomain handles common real-world mistakes")
    print("=" * 60)
    from app.zendesk_action_sink import normalize_subdomain

    # This exact case caused a real bug: doubled ".zendesk.com" in the
    # hostname, surfacing as a confusing SSL handshake failure rather
    # than an obvious "bad domain" error.
    assert normalize_subdomain("cis-58033.zendesk.com") == "cis-58033"
    assert normalize_subdomain("cis-58033") == "cis-58033"
    assert normalize_subdomain("https://cis-58033.zendesk.com") == "cis-58033"
    assert normalize_subdomain("https://cis-58033.zendesk.com/") == "cis-58033"
    assert normalize_subdomain("  cis-58033  ") == "cis-58033"
    print("✅ All common input variations normalize to the bare subdomain")

    print("\n" + "=" * 60)
    print("TEST 1: build_comment_payload defaults to public=True")
    print("=" * 60)
    payload = build_comment_payload("Here's the fix.")
    assert payload["ticket"]["comment"]["public"] is True
    print("✅ public defaults to True")

    print("\n" + "=" * 60)
    print("TEST 2: build_client_credentials_payload has the correct grant_type")
    print("=" * 60)
    cc_payload = build_client_credentials_payload("cid", "csecret", "tickets:write")
    print(cc_payload)
    assert cc_payload["grant_type"] == "client_credentials"
    assert cc_payload["client_id"] == "cid"
    assert cc_payload["client_secret"] == "csecret"
    assert cc_payload["scope"] == "tickets:write"
    print("✅ Correct client_credentials request shape")

    print("\n" + "=" * 60)
    print("TEST 3: token is cached and REUSED across calls within its lifetime")
    print("=" * 60)
    clock = ControllableClock(datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc))
    fake_post = FakeHttpPost(expires_in=1800)  # 30 minutes, matching the real default
    manager = ZendeskOAuthTokenManager(
        subdomain="test", client_id="cid", client_secret="csecret",
        http_post=fake_post, now_fn=clock,
    )

    token1 = manager.get_valid_token()
    assert fake_post.call_count == 1
    token2 = manager.get_valid_token()  # should reuse, not re-fetch
    assert fake_post.call_count == 1
    assert token1 == token2
    print(f"✅ Same token ({token1}) reused across two calls, only 1 HTTP call made")

    print("\n" + "=" * 60)
    print("TEST 4: token is REFRESHED once it's within the 60s safety margin of expiry")
    print("=" * 60)
    clock.advance(1800 - 30)  # 30 seconds before the 30-min token expires
    token3 = manager.get_valid_token()
    assert fake_post.call_count == 2, "Should have refreshed - within the 60s safety margin"
    assert token3 != token1
    print(f"✅ Correctly refreshed ({token1} -> {token3}) before actually hitting expiry")

    print("\n" + "=" * 60)
    print("TEST 5: a token with NO expires_in is treated as non-expiring")
    print("=" * 60)
    fake_post_2 = FakeHttpPost(expires_in=None)
    manager2 = ZendeskOAuthTokenManager(
        subdomain="test", client_id="cid", client_secret="csecret",
        http_post=fake_post_2, now_fn=clock,
    )
    manager2.get_valid_token()
    clock.advance(999_999)  # jump far into the future
    manager2.get_valid_token()
    assert fake_post_2.call_count == 1, "Should NOT have refreshed - no expiry was ever set"
    print("✅ Missing expires_in correctly treated as never-expiring, no unnecessary refresh")

    print("\n" + "=" * 60)
    print("ALL ZENDESK ACTION SINK TESTS PASSED (no network needed)")
    print("=" * 60)


if __name__ == "__main__":
    run()