"""
Business demo tour — stage-safe walkthrough of the ticket triage RAG.

One source of narrative truth for BOTH:
  - the terminal tour:   venv/bin/python scripts/demo_business_tour.py
  - the browser tour:    venv/bin/python scripts/demo_server.py  ->  http://localhost:8001

Scenarios run through the PRODUCTION routing logic (real Chroma
retrieval, real route() + CONFIDENCE_FLOOR, real CompositeActionSink)
but with a DEMO sink (no live Zendesk/Jira writes) and a SCRIPTED judge
(no Groq key, no network — the story is deterministic and stage-safe).

run_scenario() is stateless and NON-mutating: it reports what the
write-back loop WOULD tag (origin/verified) without actually upserting
into ./chroma_data, so repeated clicks/replays leave the seeded KB
pristine.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

from app.models import Ticket
from app.embeddings import FastEmbedder
from app.knowledge_base import ResolvedTicketKB
from app.llm_judge import TriageDecision
from app.router import route, CompositeActionSink, CONFIDENCE_FLOOR
from app.demo_action_sink import DemoActionSink


# --- Tickets for each scenario ---------------------------------------------

def _ticket(source_id, subject, description):
    return Ticket(
        source="zendesk", source_id=source_id, subject=subject,
        description=description, requester_email="jane.doe@customerco.com",
        created_at=datetime.now(), raw={},
    )


SCENARIOS = {
    "48213": _ticket(
        "48213",
        "Export to CSV button does nothing",
        "I click 'Export to CSV' on the reports page and nothing happens. "
        "No download, no error message. This is on Chrome, latest version.",
    ),
    "48214": _ticket(
        "48214",
        "Everything is broken and I need urgent help",
        "Please help, nothing is working for me, I don't know what's going "
        "on. It really needs to work, this is urgent.",
    ),
    "48215": _ticket(
        "48215",
        "CSV export maybe broken?",
        "Not sure if this is the same export problem I read about, but our "
        "reports CSV does not seem to download today. It might just be me.",
    ),
}

TITLES = {
    "48213": "1 · A clear match → auto-resolved",
    "48214": "2 · A vague ticket → escalated, then taught by a human",
    "48215": "3 · Low confidence → the safety net escalates anyway",
}


class ScriptedJudge:
    """Deterministic stand-in for the Groq TriageJudge — drives the intended
    narrative outcome per ticket, but still receives the REAL retrieved
    matches and feeds them through the real route()/floor logic."""

    def judge(self, ticket, matches):
        sid = ticket.source_id
        if sid == "48214":
            # Human-confirmed precedent present -> now resolvable.
            verified = next((m for m in matches if m["ticket_id"] == "48214"), None)
            if verified:
                return TriageDecision(
                    matched_ticket_id="48214", confidence=90,
                    reasoning="Human-verified precedent now applies (root cause "
                              "identified by support).",
                    recommended_action="auto_resolve",
                    customer_facing_draft="Thanks for flagging — we've confirmed "
                                         "the fix; it's handled.",
                )
            return TriageDecision(
                matched_ticket_id=None, confidence=62,
                reasoning="Too vague to match ANY past resolution safely — "
                          "insufficient detail for an automated fix.",
                recommended_action="escalate_to_human", customer_facing_draft=None,
            )
        if sid == "48215":
            # Leans auto-resolve but confidence is below the floor.
            return TriageDecision(
                matched_ticket_id="ZD-100", confidence=40,
                reasoning="Wording suggests the known report-cache export bug, "
                          "but this is a different CSV and I'm not certain.",
                recommended_action="auto_resolve",
                customer_facing_draft="We think this may be the cache issue...",
            )
        # Default (48213): high-confidence auto-resolve on a real match.
        top = matches[0]["ticket_id"] if matches else "?no-match?"
        return TriageDecision(
            matched_ticket_id=top, confidence=92,
            reasoning="Matches the known report-cache export bug (ZD-100/102/108).",
            recommended_action="auto_resolve",
            customer_facing_draft="Hi Jane, this is the known report-cache export "
                                  "bug. Please clear your browser cache, or use "
                                  "'Export as XLSX' while we ship the patch in "
                                  "v2.4.1 — your monthly report is safe.",
        )


JUDGE = ScriptedJudge()


# --- Narrative helpers ------------------------------------------------------

def _retrieval_text(matches):
    if not matches:
        return "No similar past tickets retrieved."
    lines = [f"Found {len(matches)} past RESOLVED tickets (vector search over the KB):"]
    for m in matches:
        proven = "✓ human-verified" if m.get("verified") else "unverified"
        lines.append(f"  · {m['ticket_id']} — {m['subject']!r}  "
                     f"(distance {m['distance']:.3f}, {m.get('origin','?')}, {proven})")
    return "\n".join(lines)


def _judge_text(decision):
    return (f"recommended_action={decision.recommended_action}\n"
            f"confidence={decision.confidence}/100\n"
            f"matched_ticket={decision.matched_ticket_id or 'none'}\n"
            f"reasoning: {decision.reasoning}")


def _action_text(ticket, decision, routing):
    if routing.final_action == "auto_resolve":
        target = "a comment on the Jira issue" if ticket.source == "jira" \
                 else "a public customer reply on the Zendesk ticket"
        return (f"POST customer reply → {target}\n\n"
                f"\"{decision.customer_facing_draft}\"")
    return "POST internal triage note → a Jira issue for an engineer to pick up."


def _writeback_text(scenario_id):
    if scenario_id == "48215":
        return "Recorded as pending escalation; no KB write until a human confirms."
    return "KB gains origin='auto_resolved', verified=False — immediate but UNVERIFIED precedent."


# --- Public runner ----------------------------------------------------------

def run_scenario(scenario_id: str, kb: ResolvedTicketKB) -> dict:
    """Stateless, non-mutating. Returns a narrative for one scenario."""
    if scenario_id not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario_id!r}")
    sink = CompositeActionSink(customer_sink=DemoActionSink(), escalation_sink=DemoActionSink())
    ticket = SCENARIOS[scenario_id]
    matches = kb.query(ticket.to_embedding_text(), top_k=3)

    decision = JUDGE.judge(ticket, matches)
    routing = route(ticket, decision, matches, sink)

    steps = [
        {"title": "Incoming ticket", "body": f'"{ticket.subject}" — {ticket.description}'},
        {"title": "Retrieval (real KB search)", "body": _retrieval_text(matches)},
        {"title": "LLM judge", "body": _judge_text(decision)},
    ]
    if routing.override_applied:
        steps.append({"title": "Independent safety check", "body": routing.override_reason})

    if scenario_id == "48214":
        # Learning loop: show the original escalation, a human fix, then the re-run.
        steps.append({"title": "First pass", "body": f"FINAL ACTION → {routing.final_action}. "
                      + _action_text(ticket, decision, routing)})
        steps.append({
            "title": "A human provides the real fix (POST /tickets/48214/resolve)",
            "body": "Root cause: bespoke upload integration not compatible with the "
                    "new SSO flow. Written back as origin='human_verified', verified=True.",
        })
        vm = [{"ticket_id": "48214", "subject": ticket.subject,
               "resolution": "SSO mapping re-created; fixed in v2.4.1",
               "distance": 0.15, "origin": "human_verified", "verified": True}]
        d2 = JUDGE.judge(ticket, vm)
        r2 = route(ticket, d2, vm, sink)
        steps.append({"title": "Re-run the same ticket", "body": _judge_text(d2)})
        steps.append({"title": "Action", "body": _action_text(ticket, d2, r2)})
        return {
            "scenario_id": scenario_id, "title": TITLES[scenario_id],
            "outcome": "AUTO-RESOLVED (LEARNED)", "steps": steps,
        }

    steps.append({"title": "Action", "body": _action_text(ticket, decision, routing)})
    steps.append({"title": "Write-back", "body": _writeback_text(scenario_id)})
    outcome = "AUTO-RESOLVED" if routing.final_action == "auto_resolve" else "ESCALATED TO HUMAN"
    return {"scenario_id": scenario_id, "title": TITLES[scenario_id],
            "outcome": outcome, "steps": steps}


def load_kb() -> ResolvedTicketKB:
    return ResolvedTicketKB(embedder=FastEmbedder(), persist_path="./chroma_data")


# --- Terminal rendering -----------------------------------------------------

def narrate(heading):
    print("\n" + "=" * 70)
    print(f"  {heading}")
    print("=" * 70)


def main():
    narrate("TICKET TRIAGE RAG — business tour")
    print(f"Confidence floor: {CONFIDENCE_FLOOR}   sink: DemoActionSink (no live "
          f"writes)   judge: scripted")

    kb = load_kb()
    if kb.count() == 0:
        print("\n  ./chroma_data is empty — seed it first:\n"
              "    venv/bin/python -m scripts.seed_knowledge_base")
        return

    for sid in ["48213", "48214", "48215"]:
        narrate(TITLES[sid])
        result = run_scenario(sid, kb)
        for step in result["steps"]:
            print(f"\n  [{step['title']}]")
            print("  " + "\n  ".join(step["body"].split("\n")))
        print(f"\n  >>> OUTCOME: {result['outcome']}")

    print("\n" + "=" * 70)
    print("  THE TAKEAWAY")
    print("=" * 70)
    print("  •  Known problems resolve themselves in ~2s with a clean customer reply.")
    print("  •  Vague tickets don't get blindly matched — they go to a human.")
    print("  •  An independent confidence floor overrides even a confident-sounding")
    print("     auto-resolve. One LLM call never holds the final say alone.")
    print("  •  One human fix permanently teaches the KB.")
    print("  Number for the room: auto-resolved ≈ seconds vs. an agent's manual")
    print("  search+draft+post. The system gets faster every time support does its job.")


if __name__ == "__main__":
    main()