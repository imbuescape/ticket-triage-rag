# Ticket Triage with RAG — Project Overview

An agentic pipeline that reads an incoming support (Zendesk) or engineering bug (Jira) ticket,
matches it against a knowledge base of *past resolved tickets*, lets an LLM judge whether a past
fix genuinely applies, then either replies to the customer automatically or escalates with a
written recommendation — and gets smarter every time it runs.

```
webhook auth  normalize  →  embed  →  Chroma retrieve  →  LLM judge  →  route  →  action sink  →  write-back loop
(Zendesk/Jira HMAC)  (→ Ticket)      (ResolvedTicketKB)    (TriageJudge)     (safety floor)
```

---

## 1. The vector embedding

- **Model:** BAAI/bge-small-en-v1.5 — an open-weight, 384-dimension embedding model.
- **Runtime:** fastembed on **ONNX**, so no GPU and no PyTorch dependency. Just a ~130MB weight
  download, cached locally after first use.
- **Design note:** the embedder is defined as an **interface (a `Protocol`), not a concrete class**.
  Nothing downstream imports a specific model. That makes swapping to a different or multilingual
  embedder a one-file change, and it's what lets the whole pipeline be unit-tested with a fake
  embedder and zero network.

## 2. The vector database

- **Store:** Chroma, a persistent local store, using **cosine similarity**.
- **What it stores:** the *problem plus the resolution that fixed it* — not bare tickets. Retrieval
  is therefore "find a past ticket whose resolution might apply," not "find a similar ticket."
- Embeddings are computed by our own injected embedder rather than Chroma's internal one, keeping
  the swap-the-model seam explicit.

## 3. LLM-as-a-judge — the step raw distance can't do

Vector distance only measures *wording* similarity. It can't tell whether a past fix's *reasoning*
transfers to a new, different-looking problem. That's the gap the judge fills.

- **Provider:** Groq (fast, cheap open-weight models). Model is a one-line env-var swap.
- **Structured output via a forced tool call**, not "respond in JSON" — the API layer validates the
  shape instead of us parsing free text.
- The prompt warns the model that low distance is a **heuristic, not proof**, and that a vague
  ticket can spuriously match another vague one; it's instructed not to force a match.
- Retries use exponential backoff **only on rate limits** — a malformed request should fail fast,
  not resend endlessly.

## 4. Routing with a safety floor independent of the LLM

The judge's *reasoned* recommendation is the primary signal. But a single LLM call is a single
point of failure, and a model's own confidence can be miscalibrated. So there is an independent
**`CONFIDENCE_FLOOR` (default 75)**: even if the judge says "auto-resolve," if its *own stated
confidence* is below the floor, we override to escalation anyway.

- Defense-in-depth modeled on safety-critical systems: no single signal makes the final call
  unchecked.
- The threshold is a **tunable knob**, calibrated against real outcome data over time — not a magic
  constant.

## 5. The write-back loop — how it gets smarter

Two deliberately different paths:

| Path | Origin tag | Verified |
|------|-----------|----------|
| Auto-resolved, written back immediately | `auto_resolved` | `False` |
| Escalated, written back only after a human submits the real fix | `human_verified` | `True` |
| Hand-curated seed data | `seed` | `True` |

- Auto-resolved tickets become **unverified precedent** immediately. This carries a real, acknowledged
  risk of reinforcing its own mistakes, so the tag makes it *inspectable* rather than hiding it.
- Escalated tickets are recorded in a pending store; a human (or eventually a "ticket closed"
  webhook) submits the real fix via `POST /tickets/{id}/resolve`, which becomes the **trusted** path.
- This **provenance model** means the system's own errors are distinguishable from trusted precedent —
  auditability by design.

## 6. Points worth mentioning beyond the core loop

- **Anti-corruption layer.** One normalized `Ticket` schema. Only the Zendesk/Jira sources know each
  vendor's raw shape — including parsing Jira's ADF rich-text to plain text and posting back the inverse.
- **Idempotency, per-vendor.** Jira ships a stable `X-Atlassian-Webhook-Identifier` — literally an
  idempotency key. Zendesk doesn't, so it hashes the raw body instead, with the limitation (byte-identical
  events collide) stated plainly. Retries never double-process.
- **Auth / replay protection.** Zendesk signs HMAC-SHA256 over timestamp+body; Jira uses WebSub
  `sha256=` over the body. Both verified with `hmac.compare_digest` to avoid timing side-channels.
  Fails closed if a secret is unset.
- **Dependency injection everywhere.** Every seam — embedder, vector store, action sink, idempotency
  store, pending store — is a `Protocol` with both a real and a mock implementation. The entire pipeline
  is testable with zero credentials and zero network (plain `assert` scripts, no test framework).

## 7. Honest production caveats

- All stores — idempotency and pending-escalation tracking — are **in-memory and process-local**: they
  reset on restart and don't work across multiple server processes. This is flagged in code as the
  intended next step (Redis/DB), not silently pretended away.
- The system starts from hand-curated seed tickets; the **calibration loop** to tune the confidence
  floor against real outcomes is the explicit Phase 6+ roadmap.

---

## 30-second elevator pitch

*Open-weight embeddings in a Chroma vector store, an LLM judge that checks root-cause fit rather than
just similarity, a confidence floor that keeps the LLM honest, and a write-back loop that learns both
trusted and unflagged precedent — engineered for production seams and honesty about its own limits.*