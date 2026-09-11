# Ticket Triage Gateway

An LLM-powered service that automatically triages incoming support tickets from **Zendesk** and **Jira** against a knowledge base of past *resolved* tickets, decides whether a ticket can be **auto-resolved** or must **escalate to a human**, and writes real resolutions back into the knowledge base so the system keeps getting smarter over time.

It's a small but complete Retrieval-Augmented Generation (RAG) system — no external RAG framework, just the components you actually need wired together end to end.

---

## The problem it solves

Help-desk teams spend significant effort on tickets whose answer already exists. A customer reports "export to CSV is broken" — and some previous ticket already walked through the exact fix. Manually searching past tickets for each new one is slow, and the search is unreliable because a surface-level wording match ("the report won't download") can point at a *different* root cause than the one that matters.

This service automates the "have we seen this before, and what fixed it?" question:

- **It retrieves genuinely *resolved* tickets**, not just similar ones — each knowledge-base entry pairs the original problem *with the resolution that fixed it*, so the system finds the *answer*, not just a lookalike.
- **It reasons about whether the match actually holds**, rather than trusting raw vector distance. Retrieval similarity is explicitly treated as a heuristic, not proof.
- **It has an independent safety net**: even when the model wants to auto-resolve, a separate confidence floor escalates the ticket if the model's own confidence is too low — a single LLM call never gets the final say unchecked.
- **It improves with use**, via a write-back loop where newly auto-resolved tickets (and, better, human-confirmed resolutions) become future precedent.

## How it works

```
                     ┌──────────────────────────────┐
   Zendesk webhook ─▶│ verify HMAC → normalize       │
   Jira webhook   ─▶│ (to one Ticket schema) → embed │
                     └──────────────────────────────┘
                                     │ vector
                                     ▼
                    retrieve from Chroma (past resolved tickets)
                                     │
                                     ▼
                  LLM judge (Groq) decides:
              does a past resolution genuinely apply?
                                     │
                 ┌───────────────────┴────────────────┐
                 ▼                                      ▼
          auto_resolve                          escalate_to_human
             │ (confidence ≥ floor)                   │
             ▼                                        ▼
   post customer reply                    post internal triage note
   (Zendesk reply / Jira comment)         (Jira comment or new issue)
             │                                        │
             ▼                                        ▼
   written back as UNVERIFIED             recorded as pending; human submits
   precedent (fast but risky)             real fix later → written back as VERIFIED
```

### The core insight: one normalized `Ticket`

Zendesk and Jira have wildly different payload shapes — flat fields vs. deeply nested structures, and Jira's description is an Atlassian Document Format (ADF) rich-text tree, not a plain string. Rather than sprinkle `if source == ...` branches through every downstream step, each source gets a single **normalizer** whose job is "turn your weird shape into one clean `Ticket`." Everything downstream (retrieval, judge, sinks) only ever sees a `Ticket`. This is the classic *anti-corruption layer* pattern, and it's what keeps the integration code from rotting.

### Built to be testable without credentials or network

The pipeline is assembled entirely from **injectable seams** (Python `Protocol`s): an `Embedder`, the `ResolvedTicketKB`, and the `TicketActionSink`s. That means every piece can be unit-tested with an in-memory store and a mock sink — no API keys, no network, no risk of accidentally messaging a real customer while developing. Real implementations (fastembed, Groq, Jira/Zendesk HTTP) plug in at runtime.

### LLM judging (not just matching)

Retrieval tells you "these look similar." It can't tell you "this past fix actually *applies*." The judge is an LLM call to Groq that reads the new ticket against the top retrieved candidates and produces a **structured decision** (forced tool call — not "please respond in JSON"): which past ticket genuinely matches, a calibrated confidence, reasoning, and whether to auto-resolve (with a customer-facing draft) or escalate. It's explicitly prompted that a *vague* ticket can spuriously match another *vague* ticket even with no shared technical content.

### The write-back loop and provenance

The knowledge base improves over time, but two paths are deliberately held to different standards:

- **Auto-resolved** tickets are written back *immediately* as `origin="auto_resolved"`, `verified=False`. This is a real, acknowledged risk — a wrong auto-resolution could reinforce itself as future precedent — but it's tagged **unverified** so the risk is *inspectable* rather than hidden.
- **Escalated** tickets are not written back until a human submits the real fix via `POST /tickets/{id}/resolve`, which becomes `origin="human_verified"`, `verified=True` — the trusted path.

Every knowledge-base entry carries this `origin` / `verified` provenance so the system's own mistakes are always distinguishable from confirmed ground truth.

### Idempotency and security

- Both webhooks verify HMAC signatures (`hmac.compare_digest`, no timing leak). Zendesk's scheme includes a timestamp (replay-resistant); Jira's follows the WebSub standard.
- Retried deliveries are deduplicated: Jira provides a stable `X-Atlassian-Webhook-Identifier`; Zendesk sends none, so the raw body is hashed instead.

## Build history (phases)

This project was built incrementally, one verifiable slice at a time — a useful map of how the pieces connect:

| Phase | What it added |
|-------|---------------|
| 1 | `Ticket` schema + Zendesk/Jira normalizers (incl. ADF rich-text parsing) |
| 2 | Embeddings + Chroma knowledge base of resolved tickets |
| 3 | Retrieval quality checks |
| 4 | LLM judge (Groq, structured tool-call output) |
| 5 | Routing with an independent confidence floor + composable action sinks |
| 6 | Webhook auth (HMAC) + idempotency |
| 6b/7 | Write-back loop: auto-resolved vs. human-verified precedent |
| 6a/8 | Real Jira posting (comment vs. create issue) + Zendesk OAuth posting |

## Project layout

```
app/
  main.py                  FastAPI gateway: /webhooks/{zendesk,jira}, /tickets/{id}/resolve, /health
  models.py                the normalized Ticket schema (anti-corruption layer)
  sources/zendesk.py       Zendesk payload normalizer
  sources/jira.py          Jira normalizer + ADF rich-text → plain text
  sources/jira_client.py   live Jira REST client (run on your machine; needs network)
  embeddings.py            Embedder Protocol + fastembed implementation
  knowledge_base.py        ResolvedTicketKB (Chroma, resolved problem + resolution)
  llm_judge.py             Groq judge → structured TriageDecision
  router.py                route() + CONFIDENCE_FLOOR safety net + action-sink seams
  jira_action_sink.py      real Jira posting (triage note + resolution comment)
  zendesk_action_sink.py   real Zendesk OAuth posting
  demo_action_sink.py      no-network demo of the same routing logic
  write_back.py            the learning loop (auto vs. human-verified precedent)
  idempotency.py           retry dedup (Jira identifier / Zendesk body hash)
  webhook_auth.py          HMAC signature verification (both sources)
  adf.py                   plain text → ADF (reverse of the parser)
scripts/
  seed_knowledge_base.py   one-time: embed data/resolved_tickets.json into ./chroma_data
  query_demo.py            test retrieval quality against the seeded KB
  *_smoke_test.py          live posting smoke tests (your machine, real side effects)
data/                      sample webhook payloads for curl smoke tests
tests/                     plain-assert test scripts (no pytest — see below)
```

## Setting up

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Copy the gitignored .env template and fill in the values you need:
#   GROQ_API_KEY                     — required for the LLM judge
#   ZENDESK_WEBHOOK_SECRET           — required to hit /webhooks/zendesk
#   JIRA_WEBHOOK_SECRET              — required to hit /webhooks/jira
#   ZENDESK_SUBDOMAIN / ZENDESK_OAUTH_CLIENT_{ID,SECRET}  — only for live Zendesk posting
#   JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN / JIRA_PROJECT_KEY — only for live Jira posting
#   ROUTING_CONFIDENCE_FLOOR         — optional, default 75
```

**One-time knowledge-base seed** (downloads ~130MB model weights on first run, then cached offline):

```bash
./venv/bin/python -m scripts.seed_knowledge_base
```

**Run the server:**

```bash
./venv/bin/uvicorn app.main:app --reload
```

Smoke-test with the sample payload:

```bash
curl -X POST http://localhost:8000/webhooks/zendesk \
  -H "Content-Type: application/json" \
  -d @data/mock_zendesk_ticket.json
```

If something looks wrong (0 results, auth errors), run the diagnostic:

```bash
./venv/bin/python -m app.sources.jira_debug
```

**Clickable browser demo** (no credentials, no network — for stakeholders):

```bash
./venv/bin/python scripts/demo_server.py   # then open http://localhost:8001
```

It reruns the seeded KB through the real routing/safety-floor logic with a demo sink
and a scripted judge, so it's stage-safe and doesn't touch live Zendesk/Jira.

## Running tests

There is **no pytest / unittest** — every test is a self-contained script of plain `assert`s, run directly with Python:

```bash
./venv/bin/python tests/test_router.py        # one file
for f in tests/test_*.py; do ./venv/bin/python "$f"; done   # the whole suite
```

Tests use injected fakes (mock Groq client, spy sinks, in-memory stores) so they run with **no network and no credentials**.

## Known limitations (documented in code)

- **In-memory stores.** Idempotency dedup and the pending-escalation tracker are process-local: they reset on restart and don't work across multiple server processes. A production deployment would swap these for Redis/a database.
- **Zendesk idempotency is body-hash based.** Zendesk provides no stable event ID, so the raw body is hashed — two genuinely different events with byte-identical payloads would collide. Rare in practice, but a real tradeoff of working around what Zendesk actually sends.
- **Jira's signature has no timestamp**, so (unlike Zendesk's) it doesn't protect against replay on its own.
- **Confidence floor is a knob, not a calibrated guarantee.** `CONFIDENCE_FLOOR` defaults to 75 and is meant to be tuned with real outcome data (do low-confidence auto-resolves actually fail?) — the calibration loop is the intended next step rather than a completed feature.