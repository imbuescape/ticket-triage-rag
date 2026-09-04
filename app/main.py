"""
Phase 6b: idempotency added on top of Phase 6a's signature verification.

Design: each idempotency-check dependency takes the raw_body FROM the
signature dependency as a sub-dependency (FastAPI resolves the chain),
so a forged/unsigned request still gets rejected with 401 before ever
reaching idempotency logic - auth and idempotency are layered, not
duplicated.

Critical ordering choice INSIDE the handlers: store.mark_processed(key)
is called only AFTER the full pipeline succeeds, not the moment a
request is seen. If it were marked immediately and then something failed
mid-processing (Groq down, Jira API error), a legitimate future retry of
the SAME event would be silently treated as an already-handled duplicate
and skipped forever - the opposite of what idempotency is supposed to
protect against. "Have I seen this key" and "did I successfully finish
this key" are different questions; only the second should gate future
retries from being permanently dropped.
"""

import os
import json
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, HTTPException
from functools import lru_cache

from app.sources.zendesk import normalize_zendesk_payload
from app.sources.jira import normalize_jira_payload
from app.knowledge_base import ResolvedTicketKB
from app.embeddings import FastEmbedder
from app.llm_judge import TriageJudge
from app.router import route, TicketActionSink
from app.jira_action_sink import JiraActionSink
from app.webhook_auth import verify_zendesk_signature, verify_jira_signature
from app.idempotency import (
    IdempotencyStore, InMemoryIdempotencyStore,
    compute_zendesk_idempotency_key, compute_jira_idempotency_key,
)

load_dotenv(override=True)  # override=True is deliberate: without it,
# load_dotenv() silently refuses to overwrite a variable that's already
# present in the shell environment. If ZENDESK_WEBHOOK_SECRET was ever
# manually `export`ed in this terminal at any earlier point, the default
# behavior would let that stale value keep winning over .env forever -
# exactly the kind of bug that looks like ".env isn't working" when
# actually .env IS being read, just deliberately not applied.

app = FastAPI(title="Ticket Triage Gateway - Phase 6b")


async def require_valid_zendesk_signature(request: Request) -> bytes:
    raw_body = await request.body()
    secret = os.environ["ZENDESK_WEBHOOK_SECRET"]  # fails closed if unset
    signature = request.headers.get("x-zendesk-webhook-signature", "")
    timestamp = request.headers.get("x-zendesk-webhook-signature-timestamp", "")
    if not verify_zendesk_signature(raw_body, timestamp, signature, secret):
        raise HTTPException(status_code=401, detail="invalid Zendesk webhook signature")
    return raw_body


async def require_valid_jira_signature(request: Request) -> bytes:
    raw_body = await request.body()
    secret = os.environ["JIRA_WEBHOOK_SECRET"]  # fails closed if unset
    signature_header = request.headers.get("x-hub-signature", "")
    if not verify_jira_signature(raw_body, signature_header, secret):
        raise HTTPException(status_code=401, detail="invalid Jira webhook signature")
    return raw_body


@lru_cache
def get_kb() -> ResolvedTicketKB:
    """NOTE: needs fastembed model + seeded ./chroma_data - your machine only."""
    return ResolvedTicketKB(embedder=FastEmbedder(), persist_path="./chroma_data")


@lru_cache
def get_judge() -> TriageJudge:
    """NOTE: needs GROQ_API_KEY - your machine only."""
    return TriageJudge()


@lru_cache
def get_action_sink() -> TicketActionSink:
    """
    NOTE: needs JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN/JIRA_PROJECT_KEY
    set - your machine only, real network call, REAL SIDE EFFECTS.
    Tests override this with a spy - see tests/test_phase5.py.
    """
    return JiraActionSink()


@lru_cache
def get_idempotency_store() -> IdempotencyStore:
    """
    In-memory, process-local (see app/idempotency.py for the production
    caveat). Tests that reuse identical payload content across multiple
    calls override this with a FRESH store per call, so the new dedup
    layer doesn't interfere with assertions those tests were written
    around before idempotency existed.
    """
    return InMemoryIdempotencyStore()


async def check_zendesk_idempotency(
    raw_body: bytes = Depends(require_valid_zendesk_signature),
    store: IdempotencyStore = Depends(get_idempotency_store),
) -> tuple[bytes, str, IdempotencyStore]:
    key = compute_zendesk_idempotency_key(raw_body)
    return raw_body, key, store


async def check_jira_idempotency(
    request: Request,
    raw_body: bytes = Depends(require_valid_jira_signature),
    store: IdempotencyStore = Depends(get_idempotency_store),
) -> tuple[bytes, str, IdempotencyStore]:
    identifier = request.headers.get("x-atlassian-webhook-identifier", "")
    if identifier:
        key = compute_jira_idempotency_key(identifier)
    else:
        # Missing header is unexpected for a real Jira webhook - rather
        # than silently skip dedup, fall back to a content hash so we
        # still catch exact-duplicate retries instead of pretending
        # everything's fine.
        import hashlib
        key = f"jira:no-identifier:{hashlib.sha256(raw_body).hexdigest()}"
    return raw_body, key, store


def _handle_ticket(ticket, kb: ResolvedTicketKB, judge: TriageJudge, sink: TicketActionSink) -> dict:
    matches = kb.query(ticket.to_embedding_text(), top_k=3)

    print(f"[{ticket.source.upper()}] Ticket {ticket.source_id}: {ticket.subject!r}")
    for m in matches:
        print(f"    -> [{m['distance']:.4f}] {m['ticket_id']}: {m['subject']}")

    if not matches:
        return {
            "status": "received", "source_id": ticket.source_id,
            "matches": [], "decision": None, "routing": None,
        }

    decision = judge.judge(ticket, matches)
    print(f"    DECISION: {decision.recommended_action} "
          f"(confidence={decision.confidence}, matched={decision.matched_ticket_id})")

    routing_result = route(ticket, decision, matches, sink)
    print(f"    ROUTING: final_action={routing_result.final_action} "
          f"override_applied={routing_result.override_applied}")
    if routing_result.override_applied:
        print(f"    OVERRIDE REASON: {routing_result.override_reason}")

    return {
        "status": "received",
        "source_id": ticket.source_id,
        "matches": matches,
        "decision": decision.model_dump(),
        "routing": routing_result.model_dump(),
    }


@app.post("/webhooks/zendesk")
async def zendesk_webhook(
    idempotency_data: tuple = Depends(check_zendesk_idempotency),
    kb: ResolvedTicketKB = Depends(get_kb),
    judge: TriageJudge = Depends(get_judge),
    sink: TicketActionSink = Depends(get_action_sink),
):
    raw_body, idempotency_key, store = idempotency_data
    payload = json.loads(raw_body)

    if store.is_duplicate(idempotency_key):
        ticket_id = str(payload.get("ticket", {}).get("id", "unknown"))
        print(f"[ZENDESK] Duplicate delivery detected for ticket {ticket_id} - skipping reprocessing")
        return {"status": "duplicate_skipped", "source_id": ticket_id}

    ticket = normalize_zendesk_payload(payload)
    result = _handle_ticket(ticket, kb, judge, sink)
    store.mark_processed(idempotency_key)  # only after full success - see module docstring
    return result


@app.post("/webhooks/jira")
async def jira_webhook(
    idempotency_data: tuple = Depends(check_jira_idempotency),
    kb: ResolvedTicketKB = Depends(get_kb),
    judge: TriageJudge = Depends(get_judge),
    sink: TicketActionSink = Depends(get_action_sink),
):
    raw_body, idempotency_key, store = idempotency_data
    payload = json.loads(raw_body)

    if store.is_duplicate(idempotency_key):
        issue = payload.get("issue", payload)
        ticket_id = str(issue.get("key", "unknown"))
        print(f"[JIRA] Duplicate delivery detected for issue {ticket_id} - skipping reprocessing")
        return {"status": "duplicate_skipped", "source_id": ticket_id}

    issue = payload.get("issue", payload)
    ticket = normalize_jira_payload(issue)
    result = _handle_ticket(ticket, kb, judge, sink)
    store.mark_processed(idempotency_key)
    return result


@app.get("/health")
async def health():
    return {"status": "ok"}