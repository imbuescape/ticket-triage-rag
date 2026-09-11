"""
Real Zendesk posting via OAuth 2.0 client credentials grant.

Why OAuth instead of an API token: Zendesk stopped allowing NEW accounts
to create API tokens as of July 28, 2026, and is phasing tokens out
entirely by April 30, 2027 for all accounts. For a server-side
integration with no interactive user (nobody's sitting in a browser
approving this each run), Zendesk's own docs recommend the CLIENT
CREDENTIALS grant specifically - it authenticates using only the OAuth
client's id+secret, no user consent screen needed.

Two real gotchas this code handles explicitly rather than ignoring:

1. Client credentials grant does NOT return a refresh token (confirmed
   in Zendesk's docs). There's nothing to "refresh" in the OAuth sense -
   when the token expires, you just request a brand new one the same way
   you got the first one.

2. OAuth clients created on or after April 30, 2026 default to a 30-
   MINUTE access token expiry. Since you're setting this up well after
   that date, don't assume a token lasts a long time - it doesn't, by
   default. ZendeskOAuthTokenManager caches the token and its expiry,
   and transparently re-fetches a new one once it's within 60 seconds of
   expiring, so callers just call get_valid_token() and never have to
   think about expiry themselves.
"""

import os
import requests
from datetime import datetime, timezone, timedelta
from typing import Callable


def normalize_subdomain(raw: str) -> str:
    """
    Defensive normalization: a very natural mistake is pasting the FULL
    domain (e.g. 'cis-58033.zendesk.com', or even 'https://cis-58033.
    zendesk.com/') into what's supposed to be just the subdomain
    ('cis-58033'), since plenty of OTHER Zendesk config fields (webhook
    URLs, etc.) do want the full domain. Left unhandled, this produces a
    malformed host like 'cis-58033.zendesk.com.zendesk.com' - which
    doesn't fail with an obvious "bad hostname" error, but instead
    surfaces as a confusing low-level SSL handshake failure, since the
    connection attempt gets far enough to hit TLS before anything
    validates the hostname makes sense.
    """
    value = raw.strip()
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.rstrip("/")
    value = value.removesuffix(".zendesk.com")
    return value


def build_comment_payload(draft: str, public: bool = True) -> dict:
    """Pure function: draft text -> Zendesk 'update ticket' request body."""
    return {"ticket": {"comment": {"body": draft, "public": public}}}


def build_client_credentials_payload(client_id: str, client_secret: str, scope: str) -> dict:
    """Pure function: OAuth client credentials -> token request body."""
    return {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }


class ZendeskOAuthTokenManager:
    """
    Handles fetching and caching an OAuth access token via the client
    credentials grant. http_post and now_fn are injectable purely for
    testability - tests supply a fake HTTP call and a controllable clock
    so expiry behavior can be verified deterministically, with no real
    network access and no time.sleep() flakiness.
    """

    def __init__(
        self,
        subdomain: str,
        client_id: str,
        client_secret: str,
        scope: str = "tickets:write",
        http_post: Callable = requests.post,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._subdomain = normalize_subdomain(subdomain)
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._http_post = http_post
        self._now_fn = now_fn
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None  # None = treat as non-expiring

    def get_valid_token(self) -> str:
        now = self._now_fn()
        # 60-second safety margin: don't wait until the exact expiry
        # instant to refresh, since the request itself takes some time
        # and we don't want a race where the token expires mid-flight.
        if self._cached_token and (self._expires_at is None or now < self._expires_at - timedelta(seconds=60)):
            return self._cached_token
        self._refresh()
        return self._cached_token

    def _refresh(self) -> None:
        payload = build_client_credentials_payload(self._client_id, self._client_secret, self._scope)
        resp = self._http_post(
            f"https://{self._subdomain}.zendesk.com/oauth/tokens",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        self._cached_token = data["access_token"]
        expires_in = data.get("expires_in")
        self._expires_at = (self._now_fn() + timedelta(seconds=expires_in)) if expires_in else None
        print(f"    [ZENDESK OAUTH] Fetched new access token (expires_in={expires_in}s)")


class ZendeskActionSink:
    def __init__(
        self,
        subdomain: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        scope: str | None = None,
        token_manager: ZendeskOAuthTokenManager | None = None,
    ):
        self._subdomain = normalize_subdomain(subdomain or os.environ["ZENDESK_SUBDOMAIN"])
        self._token_manager = token_manager or ZendeskOAuthTokenManager(
            subdomain=self._subdomain,
            client_id=client_id or os.environ["ZENDESK_OAUTH_CLIENT_ID"],
            client_secret=client_secret or os.environ["ZENDESK_OAUTH_CLIENT_SECRET"],
            scope=scope or os.environ.get("ZENDESK_OAUTH_SCOPE", "tickets:write"),
        )

    def post_customer_reply(self, ticket, draft: str) -> dict:
        access_token = self._token_manager.get_valid_token()
        payload = build_comment_payload(draft, public=True)
        url = f"https://{self._subdomain}.zendesk.com/api/v2/tickets/{ticket.source_id}.json"
        resp = requests.put(
            url, json=payload,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        print(f"    [ZENDESK] Posted public comment on ticket {ticket.source_id}")
        return {"sink": "zendesk_real", "action": "customer_reply_posted", "ticket_id": ticket.source_id}