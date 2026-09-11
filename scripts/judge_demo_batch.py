"""
RUN THIS ON YOUR OWN MACHINE, with GROQ_API_KEY set and ./chroma_data
already seeded (scripts/seed_knowledge_base.py).

    python -m scripts.judge_demo_batch

Runs the full pipeline (retrieve -> judge) against the 8 edge-case
tickets from query_demo.py. The point isn't just "does it run" - it's
whether the LLM reasoning step actually corrects for the two known
retrieval flaws we already observed with raw distance alone:

  1. ZD-106 lexical trap: a tight distance match (contacts-export vs
     reports-export) that shares wording but not root cause.
  2. ZD-109 vague-ticket false positive: a query matched another vague
     ticket by STYLE, not by shared technical content.

Each case below includes a WATCH_FOR note - read the judge's reasoning
field against that note, not just whether recommended_action looks
reasonable at a glance.
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.embeddings import FastEmbedder
from app.knowledge_base import ResolvedTicketKB
from app.llm_judge import TriageJudge
from app.models import Ticket


TEST_CASES = [
    {
        "id": "TEST-01",
        "text": "My data pull to spreadsheet just spins forever and never finishes",
        "watch_for": "Real semantic paraphrase match (ZD-100/102/108, no shared words). Should auto_resolve confidently.",
    },
    {
        "id": "TEST-02",
        "text": "App force closes right after I type my password and hit sign in",
        "watch_for": "Real match (ZD-104, mobile crash). Should NOT get pulled toward ZD-105 (notification settings), which ranked 2nd by distance but is unrelated.",
    },
    {
        "id": "TEST-03",
        "text": "How do I change my billing address on my account",
        "watch_for": "NO real answer exists in the KB. Best outcome: escalate_to_human, or very low confidence even if it names a candidate.",
    },
    {
        "id": "TEST-04",
        "text": "Cannot export my contacts to a CSV file, button does not respond",
        "watch_for": "THE LEXICAL TRAP. Correct answer is ZD-106 (contacts module bug), NOT ZD-100/108 (reports module bug) even though those are close in distance and share almost identical wording.",
    },
    {
        "id": "TEST-05",
        "text": "My account got locked out after typing my password wrong a few times",
        "watch_for": "Real match (ZD-107, lockout). Should NOT get confused with ZD-101 (password reset email delay) - different problem, same broad domain.",
    },
    {
        "id": "TEST-06",
        "text": "The CSV export button on reports does not respond when I click it",
        "watch_for": "Clean near-duplicate of ZD-100/108. Should be the most confident auto_resolve in this whole batch.",
    },
    {
        "id": "TEST-07",
        "text": "Something is wrong and I need this fixed asap",
        "watch_for": "THE VAGUE-TICKET TRAP. Distance-only retrieval matched ZD-109 (also vague) fairly tightly, but there's no actual shared technical content. Correct behavior: recognize there's no real substance to match on and escalate - not because ZD-109 is a bad candidate, but because there's nothing to verify the match against.",
    },
    {
        "id": "TEST-08",
        "text": "Getting 429 errors from your API during busy periods, way under our rate limit",
        "watch_for": "Cross-source bridging test. Should surface ENG-193 (engineer's rate-limiter bug) and recognize it as the same root cause despite different (customer vs engineering) vocabulary.",
    },
    {
        "id": "TEST-09",
        "text": "I was billed twice for one purchase, please refund the extra charge",
        "watch_for": "Harder cross-source bridging test. Should surface ENG-201 (webhook idempotency bug) despite almost no shared vocabulary with 'duplicate database rows'.",
    },
]


def main():
    embedder = FastEmbedder()
    kb = ResolvedTicketKB(embedder=embedder, persist_path="./chroma_data")
    judge = TriageJudge()

    results = []

    for case in TEST_CASES:
        ticket = Ticket(
            source="zendesk",
            source_id=case["id"],
            subject=case["text"][:80],
            description=case["text"],
            requester_email="test@example.com",
            created_at=datetime.now(),
        )

        matches = kb.query(ticket.to_embedding_text(), top_k=3)
        decision = judge.judge(ticket, matches)

        results.append({"case": case, "matches": matches, "decision": decision})

        print("=" * 70)
        print(f"{case['id']}: {case['text']!r}")
        print(f"WATCH FOR: {case['watch_for']}")
        print("-" * 70)
        print("Retrieved (by distance):")
        for m in matches:
            print(f"  [{m['distance']:.4f}] {m['ticket_id']}: {m['subject']}")
        print("\nJUDGE DECISION:")
        print(f"  matched_ticket_id : {decision.matched_ticket_id}")
        print(f"  confidence        : {decision.confidence}")
        print(f"  recommended_action: {decision.recommended_action}")
        print(f"  reasoning         : {decision.reasoning}")
        print()

    # --- Summary table at the end for quick scanning ---
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        top_distance = r["matches"][0]["distance"] if r["matches"] else float("nan")
        d = r["decision"]
        print(f"{r['case']['id']:10s} top_dist={top_distance:.4f}  "
              f"-> matched={str(d.matched_ticket_id):10s} "
              f"conf={d.confidence:3d}  action={d.recommended_action}")


if __name__ == "__main__":
    main()