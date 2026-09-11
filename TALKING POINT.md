# Talking Points — Precedent (Ticket Triage RAG)

Speaker notes for presenting or defending the design. Organized so each section stands alone if you only get asked about one piece.

---

## 1. The one-sentence pitch

"It treats your history of resolved tickets as precedent — retrieves similar past cases, has an LLM *reason* about whether they actually apply (not just whether the wording matches), and only auto-acts when an independent confidence check agrees. Everything that could go wrong with that idea — bad matches, overconfidence, replay attacks, duplicate processing — is explicitly handled, not assumed away."

---

## 2. Vector embeddings — what, and why this one

**What:** `fastembed` running `BAAI/bge-small-en-v1.5`, a 384-dimension embedding model, via ONNX Runtime — no PyTorch.

**How it works end to end:**
1. A ticket's subject + description get concatenated into one string.
2. `fastembed` converts that string into a 384-number vector — a point in semantic space where similar *meanings* land close together, regardless of shared vocabulary.
3. That vector is stored in ChromaDB alongside the ticket's resolution text and metadata.
4. A new ticket gets embedded the same way, and ChromaDB finds the nearest vectors by cosine distance.

**Why this specific choice, if asked:**
- **Local and free** — no per-call API cost, no rate limits, and it works fully offline after the first ~130MB model download.
- **ONNX instead of the more common PyTorch-based sentence-transformers** — same underlying idea, far smaller footprint, faster cold start. This was a deliberate swap made under real disk constraints, not the default choice everyone reaches for.
- **Proven, not assumed, to capture meaning over wording** — the retrieval was stress-tested against paraphrases sharing zero words with their correct match (e.g. *"my data pull to spreadsheet just spins forever"* correctly retrieved a CSV-export bug it had no lexical overlap with).

**A real, disclosed limitation:** it's also *fooled* by surface-level style. A vague ticket like *"something is wrong, please help"* can rank close to another vague ticket purely because both are unspecific — not because they share a root cause. This exact failure mode is the reason the LLM judge exists as a second layer, not a redundant one.

---

## 3. LLM-as-a-judge — the reasoning layer on top of retrieval

**The core idea:** vector distance measures *similarity of wording*. It cannot measure *"does this past fix actually address the same underlying problem."* So retrieval hands its top candidates to an LLM (Groq, `openai/gpt-oss-120b`), and the LLM reasons about content, not just rank order.

**How it's implemented, mechanically:**
- **Structured output via forced tool-calling**, not "please respond in JSON." The model is required to call a `submit_triage_decision` function with a fixed schema — this eliminates the entire class of bugs where an LLM wraps its JSON in markdown fences or adds preamble text.
- **`temperature=0`** — deliberate. A judgment task needs low run-to-run variance so a confidence score means the same thing called twice; a creative-writing default temperature would make confidence scores noise.
- **The prompt explicitly warns the model that distance is a heuristic, not proof** — this is what lets it override retrieval when it's misleading, e.g. correctly picking a lexically-*less* similar candidate because it's the *actually correct* one.

**Proof this earns its cost, not just theory:** in a stress-test batch, the judge correctly picked the right candidate over a much more surface-similar wrong one (two "CSV export unresponsive" tickets that were actually different modules — contacts vs. reports), citing the actual functional difference in its reasoning field. Confidence and distance also don't move in lockstep — one case with worse distance scored *higher* confidence than a near-perfect-distance case, because the reasoning genuinely had less ambiguity to resolve. That's evidence of real reasoning, not a repackaged distance score.

---

## 4. The safety net — confidence floor as an independent check

**The problem in the original naive design:** trusting a single number an LLM reports about itself ("confidence: 92%") with no external check. LLM self-reported confidence is not inherently calibrated.

**The fix:** the judge's own recommendation (`auto_resolve` / `escalate_to_human`) is the primary signal — it's reasoned, not a bare threshold. But layered on top is an **independent, tunable confidence floor**: if the judge says auto-resolve but its own stated confidence is below the floor, the system overrides to escalate anyway, regardless of the judge's reasoning.

**Why this matters as a talking point:** it's defense-in-depth. No single signal — not distance, not the judge's own confidence, not the judge's own recommendation — is ever trusted alone to trigger a customer-facing action. This is also the natural foundation for a future calibration loop: log every decision plus eventual real-world outcome (did the customer reopen the ticket?), and use that to tune the floor with data instead of intuition.

---

## 5. Source-aware routing — why Zendesk and Jira aren't treated the same

**The subtlety:** a Zendesk-sourced ticket has a real customer to reply to. A Jira-sourced ticket is an engineering bug report — there's no "customer," so an auto-resolved Jira ticket can't sensibly get a Zendesk reply.

**The fix:** a `CompositeActionSink` inspects the ticket's source and routes accordingly — Zendesk-sourced auto-resolves get a real Zendesk public comment; Jira-sourced auto-resolves get a comment posted on the same Jira issue instead. Escalations follow the same logic in reverse: a Zendesk-sourced escalation creates a *new* Jira issue (since none exists yet); a Jira-sourced escalation comments on the issue that's already open.

**Worth mentioning:** this gap was found by writing thorough end-to-end tests across *both* sources, not by design foresight — a good example of tests surfacing a real architectural bug rather than just confirming what was already assumed to work.

---

## 6. Webhook security — two genuinely different HMAC schemes

**Zendesk:** `base64(HMAC-SHA256(timestamp + raw_body, secret))`, sent across two headers (signature + timestamp separately).

**Jira:** `hex(HMAC-SHA256(raw_body, secret))`, prefixed `sha256=`, sent in one header (`X-Hub-Signature`) — no timestamp involved at all.

**Why call this out specifically:** these are *not* the same pattern reused twice — they're different enough that a copy-paste implementation would silently fail. Both were verified against each provider's actual current documentation (not assumed from memory), including the detail that Zendesk's signature must be computed over the **raw request bytes**, not a re-parsed-and-re-serialized version of the JSON — a subtle bug that would make legitimate requests look forged.

---

## 7. The "ephemeral database" — precision matters here

This is worth being exact about, because two different things could be meant by "ephemeral," and only one of them is true:

- **The knowledge base (ChromaDB) is NOT ephemeral.** It's a persistent, disk-backed vector store (`./chroma_data`). It survives restarts. This is where all the real precedent — seed data, auto-resolved writes, human-verified resolutions — actually lives long-term.

- **What IS genuinely ephemeral: the idempotency store and the pending-escalation store.** Both are in-memory Python objects (a `set` for seen delivery keys, a `dict` for tickets awaiting human resolution) that exist only for the life of the running server process. **Restart the server, and both are wiped.**

**Why this is a disclosed limitation, not an oversight:** for a single dev instance, in-memory is simple and sufficient. But it means (a) a webhook retry that arrives *after* a restart won't be recognized as a duplicate, and (b) an escalated ticket awaiting human resolution is *lost* if the server restarts before someone resolves it. The documented production fix is a shared external store — Redis with a TTL is the standard choice — so this state survives restarts and works across multiple server instances. This tradeoff was made explicitly and is called out in code comments and the README, not discovered later.

---

## 8. The write-back loop — how the system actually improves over time

**Two deliberately different paths, because they have different trust levels:**

- **Auto-resolved tickets write back to the knowledge base immediately**, tagged `origin="auto_resolved", verified=False`. This is a real, named risk: if the auto-resolution was actually wrong, the system is now citing its own mistake as future precedent. Tagging it unverified doesn't eliminate that risk — it makes it *inspectable*, and sets up a natural lever (e.g., weighting verified precedent more heavily in the judge's prompt, or auditing unverified entries against real customer follow-up).

- **Escalated tickets are held pending, not written back at all**, until a human explicitly submits the real fix via `POST /tickets/{id}/resolve`. That becomes `origin="human_verified", verified=True` — the trustworthy path.

**The talking point:** the system doesn't treat "the AI resolved it" and "a human confirmed the fix" as equally trustworthy sources of future precedent, and the data model makes that distinction visible rather than flattening it away.

---

## 9. Real integrations — and a live example of adapting to a changing API

- **Jira**: standard API-token Basic Auth, straightforward.
- **Zendesk**: OAuth 2.0 client-credentials grant — deliberately *not* the simpler API-token flow, because Zendesk stopped issuing new API tokens for new accounts as of July 28, 2026 (full sunset by April 2027). This surfaced mid-project as a real blocker, not a hypothetical.
- **A genuinely tricky detail handled correctly:** OAuth clients created after April 30, 2026 default to a 30-minute access-token expiry, and the client-credentials grant returns *no refresh token* — so the only way to get a new token is to re-request one the same way. `ZendeskOAuthTokenManager` caches the token and transparently re-fetches it before expiry, with the expiry/clock logic fully unit-tested via dependency injection (a fake HTTP call + a controllable clock), so the 30-minute-expiry behavior was proven correct without an actual 30-minute wait.

**Good story to tell:** this is a live example of the project adapting to a real, dated external API change discovered during build — not designed in from the start.

---

## 10. Testing philosophy — why the whole test suite needs zero credentials

**The pattern used everywhere:** every external dependency (embedding model, LLM client, vector store, action sink, auth secret, clock) is injected via a constructor parameter or FastAPI's `Depends()`, never hardcoded.

**What this buys, concretely:**
- The full test suite runs with **zero API keys and zero network access** — fakes and spies stand in for Groq, Jira, Zendesk, and even the embedding model itself.
- Security-critical code (HMAC verification) is tested with **real computed signatures**, including deliberately forged ones, proving rejection actually works — not just that acceptance works.
- Time-based logic (OAuth token expiry) is tested with an **injected clock**, so expiry behavior is proven without a real 30-minute wait.

**The demo UI is a direct product of this same pattern** — `/demo` runs the identical retrieval → judge → routing → write-back pipeline as production traffic, with only the final action sink swapped for one that describes what it would do instead of calling a real API. It's not a separate toy version of the system; it's the same code path with one dependency substituted.

**Important nuance if demoing this live: "mocked" only applies to the sink, not the whole pipeline.** Worth being precise about this if anyone asks "is this safe to click around in":
- **Real and NOT mocked:** the Groq LLM judge call (spends real API credits, hits real rate limits), and the knowledge base itself — `/demo` reads from and writes to the exact same persistent `./chroma_data` that real webhook traffic uses. An auto-resolved demo ticket permanently writes an `auto_resolved`/`unverified` entry; resolving a demo escalation permanently writes a `human_verified` entry. Neither is a preview or a throwaway copy.
- **Mocked:** only the very last step — actually posting to Zendesk or Jira. `DemoActionSink` never makes a `requests.post`/`requests.put` call; it returns a description of what would happen instead.
- **The practical implication:** running a batch of test tickets through `/demo` will genuinely seed the production knowledge base with demo data and spend real Groq credits, even though nothing gets posted anywhere externally visible. It's safe in the sense of "nothing leaks out," not safe in the sense of "leaves system state untouched."

---

## Other points worth having ready

- **The knowledge base grows across sources.** A customer-phrased Zendesk ticket ("getting 429 errors during busy periods") correctly retrieved an engineering-phrased Jira bug ("rate limiter drops requests under 50 req/s") despite almost no shared vocabulary — proof the cross-referencing promise of the original architecture actually works with a small, free, local embedding model.
- **Nothing here required a paid embedding or a large model to work.** The entire retrieval layer runs on a free, local, 384-dimension model. The one paid step in the pipeline is the LLM reasoning call itself.
- **Every known limitation is written down, not hidden** — in code comments and the README's "Current limitations" section. That's a deliberate stance: a system that discloses what it doesn't yet handle is more trustworthy than one that implies it's finished.