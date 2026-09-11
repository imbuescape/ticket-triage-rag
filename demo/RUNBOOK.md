# Demo Runbook — Ticket Triage RAG

Step-by-step instructions to run the demo for stakeholders.

This RAG triages every new support ticket against a knowledge base of past **resolved**
tickets, has an LLM decide whether it can **auto-resolve** or must **escalate to a human**,
and learns from the results. The demo walks through four stories:

1. **A clear match** → auto-resolved with a customer reply (the "wow")
2. **A vague ticket** → escalated, no forced garbage match (the "trust")
3. **Low confidence** → the safety floor escalates anyway (the "safety net")
4. **A human fix** → the same vague ticket now auto-resolves (the "learning loop")

The demo uses a **demo sink** (it never posts to a real Zendesk/Jira) and a **scripted
judge** (no API key, no network), so it is safe to run and replay anywhere.

---

## 0. Before you start (one-time setup)

```bash
cd <this project root>

# 1. Create the virtualenv and install dependencies
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 2. Seed the knowledge base (first run downloads ~130MB model weights, then cached)
./venv/bin/python -m scripts.seed_knowledge_base
```

Verify it worked — you should see output ending in "Knowledge base seeded with N resolved
tickets":

```bash
./venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from app.knowledge_base import ResolvedTicketKB
from app.embeddings import FastEmbedder
print('seeded tickets:', ResolvedTicketKB(embedder=FastEmbedder(), persist_path='./chroma_data').count())
"
```

> **No credentials are needed.** The demo does not require `GROQ_API_KEY` or any
> Zendesk/Jira token. You only need the seeded KB above.

---

## 1. Preferred way — browser demo (for stakeholders)

**Quickest:** double-click `demo/start_demo.sh` (or `./demo/start_demo.sh`) — it seeds the
KB if needed, then launches the server for you.

Or start it manually:

```bash
./venv/bin/python scripts/demo_server.py
```

Open **http://localhost:8001** in a browser.

- If you get `curl: (7) Failed to connect` / the page won't load, the port is busy — stop
  the other process or change the port (see Troubleshooting).
- Three buttons appear. Click them **in order** for the full narrative.

### What to say at each button

| # | Button | Click and say |
|---|--------|---------------|
| 1 | **A clear match → auto-resolved** | "A known problem arrives. The system pulls the relevant past resolved ticket, drafts a customer reply, and posts it — seconds, not an agent's 20-minute search." |
| 2 | **A vague ticket → escalated, then taught by a human** | "A vague 'nothing works' ticket. Notice it does **not** force a match — it escalates to a human instead of guessing. Wording similarity is not proof its answer applies." |
| 3 | **Low confidence → the safety net escalates anyway** | "Even when the model says auto-resolve, an independent safety floor overrides it when confidence is low. One LLM call never holds the final say alone." |
| 4 | *(the second half of the vague ticket run)* | "A human confirms the real fix → it becomes trusted precedent. Re-run the same ticket → now it resolves itself. The system gets smarter every time support does its job." |

**Close with the one-number framing:**
> "Known problems resolve in ~2 seconds instead of a manual search + draft + post — and
> every human-confirmed fix makes tomorrow a little faster."

---

## 2. Alternative — terminal tour

Same four stories, printed as text. Good for a quick rehearsal or a remote call:

```bash
./venv/bin/python scripts/demo_business_tour.py
```

---

## 3. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `KeyError: ZENDESK_WEBHOOK_SECRET` or a crash on import | You ran `main.py`'s server, not the demo. Use `scripts/demo_server.py`. The demo needs no secrets. |
| `./chroma_data is empty — seed it first` | Run step 2 of Setup. |
| Port 8001 already in use | `lsof -i :8001` to find the process, or edit the port in `scripts/demo_server.py`. Start the demo server again. |
| Page loads but buttons do nothing | Check the browser console (Ctrl/Cmd+Shift+J). The page must be served from the running demo server, not opened as a file. |
| Confidence floor shows 85, not 75 | Correct — it reads `ROUTING_CONFIDENCE_FLOOR` from `.env`. That only affects the number shown, not the demo. |

---

## 4. Reset the demo to a clean state

The demo is **non-mutating** — it reports what the knowledge base *would* learn without
actually writing. To fully clear any earlier test runs and start from the pristine seed:

```bash
rm -rf chroma_data
./venv/bin/python -m scripts.seed_knowledge_base
```

---

## 5. Files you touch

| File | Purpose |
|------|---------|
| `scripts/demo_server.py` | Browser server (the recommended surface). Start with `./venv/bin/python scripts/demo_server.py`. |
| `scripts/demo_business_tour.py` | Shared scenario logic + the terminal tour. |
| `scripts/demo_business_tour.py` `run_scenario()` | The four stories, driven by real retrieval (`app/knowledge_base.py`) + real routing (`app/router.py`). |

**What the demo is *not* doing** (so you can say so if asked): it stages a `DemoActionSink`
in place of live Zendesk/Jira posting, and a scripted judge in place of a live Groq call —
so nothing is posted to a real customer and no API key is consumed. Retrieval is real
(against the seeded KB); only the external effects are simulated.