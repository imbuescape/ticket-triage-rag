"""
Phase 3: Retrieval is now wired into the gateway.

Design choice worth noticing: get_kb() is a FastAPI dependency (Depends),
not a hardcoded global. Why this matters concretely - FastEmbedder needs
huggingface.co, which this sandbox can't reach. Without dependency
injection, testing this endpoint here would mean either (a) needing real
network access, or (b) monkeypatching internals in a fragile way.

With Depends(), tests just call app.dependency_overrides[get_kb] = ...
and swap in a KB backed by the dummy embedder - no changes to main.py's
actual logic, no monkeypatching. This is the same "define the seam,
inject the implementation" idea from embeddings.py, just applied one
layer up, at the API boundary instead of the retrieval boundary.
"""

from fastapi import FastAPI, Request, Depends
from functools import lru_cache

from app.sources.zendesk import normalize_zendesk_payload
from app.sources.jira import normalize_jira_payload
from app.knowledge_base import ResolvedTicketKB
from app.embeddings import FastEmbedder

app = FastAPI(title="Ticket Triage Gateway - Phase 3")


@lru_cache
def get_kb() -> ResolvedTicketKB:
    """
    Lazily constructs the real KB (real embedder, real persisted Chroma
    data) on first use. @lru_cache means this only runs once per process,
    not once per request - loading the embedding model is expensive
    enough that you never want to do it per-request.

    NOTE: this default only works on your machine (needs the fastembed
    model, needs chroma_data/ already seeded via scripts/seed_knowledge_base.py).
    Tests override this dependency entirely - see tests/test_phase3.py.
    """
    return ResolvedTicketKB(embedder=FastEmbedder(), persist_path="./chroma_data")


def _handle_ticket(ticket, kb: ResolvedTicketKB) -> dict:
    """Shared logic for both webhook handlers - retrieve and log matches."""
    matches = kb.query(ticket.to_embedding_text(), top_k=3)

    print(f"[{ticket.source.upper()}] Ticket {ticket.source_id}: {ticket.subject!r}")
    for m in matches:
        print(f"    -> [{m['distance']:.4f}] {m['ticket_id']}: {m['subject']}")

    return {
        "status": "received",
        "source_id": ticket.source_id,
        "matches": matches,
    }


@app.post("/webhooks/zendesk")
async def zendesk_webhook(request: Request, kb: ResolvedTicketKB = Depends(get_kb)):
    payload = await request.json()
    ticket = normalize_zendesk_payload(payload)
    return _handle_ticket(ticket, kb)


@app.post("/webhooks/jira")
async def jira_webhook(request: Request, kb: ResolvedTicketKB = Depends(get_kb)):
    payload = await request.json()
    issue = payload.get("issue", payload)
    ticket = normalize_jira_payload(issue)
    return _handle_ticket(ticket, kb)


@app.get("/health")
async def health():
    return {"status": "ok"}
