"""
Phase 1: The ingestion gateway.

Right now these endpoints just normalize and print the result.
In Phase 3 they'll call the retrieval pipeline. In Phase 5 they'll
call the routing logic. We're building this incrementally on purpose -
each phase adds one real capability to a pipeline that already runs.
"""

from fastapi import FastAPI, Request
from app.sources.zendesk import normalize_zendesk_payload
from app.sources.jira import normalize_jira_payload

app = FastAPI(title="Ticket Triage Gateway - Phase 1")


@app.post("/webhooks/zendesk")
async def zendesk_webhook(request: Request):
    payload = await request.json()
    ticket = normalize_zendesk_payload(payload)
    print(f"[ZENDESK] Normalized ticket: {ticket}")
    return {"status": "received", "source_id": ticket.source_id}


@app.post("/webhooks/jira")
async def jira_webhook(request: Request):
    payload = await request.json()
    # Real Jira webhooks wrap the issue under an "issue" key
    issue = payload.get("issue", payload)
    ticket = normalize_jira_payload(issue)
    print(f"[JIRA] Normalized ticket: {ticket}")
    return {"status": "received", "source_id": ticket.source_id}


@app.get("/health")
async def health():
    return {"status": "ok"}
