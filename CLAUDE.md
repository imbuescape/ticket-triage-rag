# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI service that triages incoming Zendesk/Jira support tickets using RAG. An incoming ticket is embedded, matched against a Chroma vector DB of past *resolved* tickets, judged by an LLM (Groq), then either auto-resolved (reply posted) or escalated to a human. It was built in explicit `phase1 → phase8` increments (§ Commit history).

## Commands

All commands use the `venv` virtualenv directly. **There is no pytest / unittest — tests are plain `assert` scripts run with Python.**

```bash
# Run one test file
venv/bin/python tests/test_router.py

# Run the whole suite (you'll see each file's own asserts)
for f in tests/test_*.py; do venv/bin/python "$f"; done

# Start the server
venv/bin/uvicorn app.main:app --reload

# One-time: download model weights (~130MB) + embed seed data into ./chroma_data
python -m scripts.seed_knowledge_base

# Test retrieval quality against the seeded KB
python -m scripts.query_demo
```

`data/mock_zendesk_ticket.json` and `data/mock_jira_ticket.json` are sample webhook payloads for `curl` smoke tests.

## Runtime environment

- Secrets live in `.env` (gitignored — never commit). See `requirements.txt`.
- **Network-dependent steps run on the user's machine, not in any sandbox**: the `FastEmbedder` must download weights from huggingface.co (sandbox allows only pypi/npm/github), and the real Jira/Zendesk sinks make live authenticated calls (`app/sources/jira_client.py`, `scripts/*_smoke_test.py`). Modules are written so tests never need this.

## Architecture

The pipeline is one long chain in `app/main.py`'s `_handle_ticket`:

```
webhook auth  normalize  →  embed  →  Chroma retrieve  →  LLM judge  →  route  →  action sink  →  write-back loop
(Zendesk/Jira HMAC)  (→ Ticket)      (ResolvedTicketKB)    (TriageJudge)     (safety floor)
```

1. **Anti-corruption layer** — `app/models.py` defines one normalized `Ticket` schema. `app/sources/zendesk.py` and `app/sources/jira.py` are the only places that know each source's raw shape; Jira has ADF rich-text that must be parsed to plain text (`app/sources/jira.py::_extract_text_from_adf`), and posting back needs the inverse (`app/adf.py::text_to_adf`).
2. **Injected seams (Protocol types)** — the pipeline is built to be tested without network or credentials. `Embedder` (`app/embeddings.py`), `ResolvedTicketKB` (`app/knowledge_base.py`), `TicketActionSink` (`app/router.py`), and the idempotency/pending stores are all Protocols with in-memory or mock implementations for tests. Add capabilities by defining a Protocol and injecting a real impl; don't hard-code dependencies.
3. **Judge** (`app/llm_judge.py`) — Groq chat with a forced tool call for structured output (not "respond in JSON"). Retries exponential backoff **only on `RateLimitError`**.
4. **Router** (`app/router.py`) — the judge's `recommended_action` is the primary signal, but a module-level `CONFIDENCE_FLOOR` (env `ROUTING_CONFIDENCE_FLOOR`, default 75) overrides `auto_resolve` → `escalate_to_human` when the judge's own confidence is below it. This is the independent safety net.
5. **Action sinks** — `JiraActionSink` comments on an existing issue (jira-sourced) or creates a new one (zendesk-sourced); `ZendeskActionSink` posts a customer reply via OAuth client-credentials; `CompositeActionSink` routes customer replies → Zendesk and internal triage notes → Jira. `DemoActionSink` exercises the same routing without network.
6. **Write-back loop** (`app/write_back.py`) — the KB improves over time via two deliberately different paths: `auto_resolved` tickets are written back *immediately* as `origin="auto_resolved"`, `verified=False` (risk: self-reinforcing wrong precedent, but inspectable); escalated tickets are recorded in a `PendingEscalationStore` and only become trusted `origin="human_verified"`, `verified=True` precedent when a human submits the real fix via `POST /tickets/{source_id}/resolve`.
7. **Idempotency / auth** — `app/idempotency.py` dedups retries: Jira uses the stable `X-Atlassian-Webhook-Identifier`; Zendesk has no stable ID so it hashes the raw body. `app/webhook_auth.py` verifies both signatures with `hmac.compare_digest`. Stores are in-memory (process-local) — production would swap in Redis/DB.
8. **Provenance** — every KB entry carries `origin` (`seed` / `auto_resolved` / `human_verified`) + `verified` so the system's own mistakes are distinguishable from trusted precedent.

## Known limitations (documented in code)

- All stores are in-memory: idempotency dedup and pending-escalation tracking reset on restart and don't work across multiple processes. Flagged for a Redis/DB swap, not quietly pretended away.
- Zendesk body-hash idempotency can false-positive on two byte-identical events; Jira signature lacks replay protection (no timestamp).
- `CONFIDENCE_FLOOR` is a knob for calibration — the calibration loop with outcome data is the intended Phase 6+ work.

## Git hygiene

This repo is mid-renovation: the working tree has many `*.backUp` / `*.bckUp` / mis-named files (e.g. `test_jira_action_sinl.py`) that are NOT current. Verify a file is current by reading it, not by trusting names ending in `_bckUp` / `.backUp`.