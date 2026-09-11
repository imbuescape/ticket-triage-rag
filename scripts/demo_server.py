"""
One-page browser demo of the ticket triage RAG, for stakeholders.

Run:  venv/bin/python scripts/demo_server.py   then open http://localhost:8001

Serves the same narrative as scripts/demo_business_tour.py over HTTP:
four clickable scenarios, each run through the PRODUCTION routing logic
(real Chroma retrieval + real route()/CONFIDENCE_FLOOR) but with the
DEMO sink and a SCRIPTED judge — so it's fully offline, needs only the
seeded KB (no GROQ key), never posts to a real Zendesk/Jira, and leaves
./chroma_data untouched.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from scripts.demo_business_tour import (
    run_scenario, load_kb, TITLES, SCENARIOS,
)

app = FastAPI(title="Ticket Triage RAG — demo")

_kb = None


def _get_kb():
    """Lazy-loaded; SQLAlchemy-style single init. Requires ./chroma_data seeded."""
    global _kb
    if _kb is None:
        _kb = load_kb()
    return _kb


@app.get("/health")
async def health():
    return {"status": "ok", "seeded": _get_kb().count() > 0}


@app.get("/run/{scenario_id}")
async def run(scenario_id: str):
    if scenario_id not in SCENARIOS:
        return JSONResponse({"error": f"unknown scenario {scenario_id!r}"}, status_code=404)
    kb = _get_kb()
    if kb.count() == 0:
        return JSONResponse({
            "error": "not_seeded",
            "message": "./chroma_data is empty — run: "
                       "venv/bin/python -m scripts.seed_knowledge_base",
        }, status_code=503)
    return run_scenario(scenario_id, kb)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>Ticket Triage RAG — demo</title>
<style>
  body { font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
         background: #f6f7f9; color: #1a1a2e; }
  header { background: #1a1a2e; color: #eae6ff; padding: 22px 28px; }
  header h1 { margin: 0 0 4px; font-size: 22px; }
  header p { margin: 0; color: #b9b3d8; font-size: 13px; }
  main { max-width: 860px; margin: 26px auto; padding: 0 16px; }
  .scenarios { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px; }
  .scenarios button { flex: 1 1 220px; padding: 14px 12px; font-size: 14px; text-align: left;
         font-weight: 600; color: #fff; background: #3b3b73; border: 0; border-radius: 8px;
         cursor: pointer; transition: background .15s; }
  .scenarios button:hover { background: #5151a3; }
  .scenarios button:disabled { opacity: .5; cursor: wait; }
  #setup, #error { display: none; background: #fff4e6; border: 1px solid #f0c; padding: 12px 16px;
         border-radius: 8px; margin-bottom: 16px; font-family: ui-monospace, Menlo, monospace; font-size: 13px; }
  .card { background: #fff; border: 1px solid #e3e4ee; border-radius: 10px; padding: 14px 18px;
         margin-bottom: 14px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
  .card h3 { margin: 0 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: .04em;
         color: #6a6a8a; }
  .card pre { margin: 0; white-space: pre-wrap; font: 13px/1.5 ui-monospace, Menlo, monospace; }
  .result-header { display: flex; align-items: center; justify-content: space-between;
         background: #eef0ff; border: 1px solid #d5d9ff; border-radius: 10px; padding: 12px 18px; margin-bottom: 14px; }
  .result-header .title { font-weight: 700; }
  .badge { font-weight: 700; font-size: 13px; padding: 6px 12px; border-radius: 999px; }
  .badge.green { background: #d7f5dd; color: #127a32; }
  .badge.amber { background: #ffe9c7; color: #8a5a00; }
  .badge.red { background: #ffdcdc; color: #a01818; }
  footer { max-width: 860px; margin: 10px auto 40px; padding: 0 16px; color: #8a8aa8; font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>Ticket Triage RAG — what it does</h1>
  <p>Every new ticket is matched against past <b>resolved</b> tickets, judged by an
     LLM, then auto-resolved or escalated. Pick a story to see it run live.</p>
</header>
<main>
  <div class="scenarios" id="buttons"></div>
  <div id="setup"></div>
  <div id="error"></div>
  <div id="results"></div>
</main>
<footer>
  Demo mode: uses the Demo sink (nothing posts to a real Zendesk/Jira) and a scripted
  judge, so every run is safe and replayable. Retrieval hits the real seeded KB; the
  confidence floor ({floor}) is the industry-independent safety net.
</footer>
<script>
  const TITLES = __TITLES__;
  const buttons = document.getElementById('buttons');
  for (const [id, title] of Object.entries(TITLES)) {
    const b = document.createElement('button');
    b.textContent = title; b.onclick = () => runScenario(id, b);
    buttons.appendChild(b);
  }

  const badge = o => o.startsWith('AUTO') ? 'green' : o.includes('ESCALAT') ? 'amber' : 'red';

  function runScenario(id, btn) {
    btn.disabled = true;
    fetch('/run/' + id).then(r => r.json()).then(data => {
      btn.disabled = false;
      if (data.error) {
        if (data.error === 'not_seeded') {
          document.getElementById('setup').style.display = 'block';
          document.getElementById('setup').textContent = data.message;
        } else {
          document.getElementById('error').style.display = 'block';
          document.getElementById('error').textContent = data.message || data.error;
        }
        return;
      }
      const wrap = document.getElementById('results');
      wrap.innerHTML = '';
      const hdr = document.createElement('div');
      hdr.className = 'result-header';
      hdr.innerHTML = `<span class="title">${data.title}</span>` +
        `<span class="badge ${badge(data.outcome)}">${data.outcome}</span>`;
      wrap.appendChild(hdr);
      for (const step of data.steps) {
        const c = document.createElement('div');
        c.className = 'card';
        c.innerHTML = `<h3>${step.title}</h3><pre>${step.body}</pre>`;
        wrap.appendChild(c);
      }
    }).catch(e => {
      btn.disabled = false;
      document.getElementById('error').style.display = 'block';
      document.getElementById('error').textContent = 'Request failed: ' + e;
    });
  }
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def page():
    js_titles = "{" + ",".join(f'"{k}":"{v}"' for k, v in TITLES.items()) + "}"
    return HTMLResponse(_PAGE.replace("__TITLES__", js_titles)
                            .replace("{floor}", str(int(os.environ.get(
                                "ROUTING_CONFIDENCE_FLOOR", "75")))))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8001")))