"""
RUN THIS ON YOUR OWN MACHINE, with GROQ_API_KEY set and ./chroma_data
already seeded (scripts/seed_knowledge_base.py).

    python -m scripts.judge_demo_single

Sanity-checks the full real pipeline on ONE clean, high-confidence case:
the original mock Zendesk ticket, which should tightly match ZD-100.
This is deliberately the EASIEST case - the point right now is proving
the output shape round-trips correctly through a real Groq call, not
stress-testing reasoning quality yet. A confident auto_resolve decision
populates every field (nothing is null), so if this comes back clean,
the schema, prompt, and parsing are all working end to end.
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.embeddings import FastEmbedder
from app.knowledge_base import ResolvedTicketKB
from app.sources.zendesk import normalize_zendesk_payload
from app.llm_judge import TriageJudge


def main():
    with open("data/mock_zendesk_ticket.json") as f:
        payload = json.load(f)
    ticket = normalize_zendesk_payload(payload)

    print(f"NEW TICKET: {ticket.subject!r}")
    print(f"  Description: {ticket.description}\n")

    embedder = FastEmbedder()
    kb = ResolvedTicketKB(embedder=embedder, persist_path="./chroma_data")
    matches = kb.query(ticket.to_embedding_text(), top_k=3)

    print("RETRIEVED CANDIDATES:")
    for m in matches:
        print(f"  [{m['distance']:.4f}] {m['ticket_id']}: {m['subject']}")
    print()

    print("Calling Groq judge...")
    judge = TriageJudge()
    decision = judge.judge(ticket, matches)

    print("\n" + "=" * 60)
    print("DECISION (full schema - checking nothing is unexpectedly null)")
    print("=" * 60)
    print(decision.model_dump_json(indent=2))

    print("\n" + "=" * 60)
    print("SHAPE CHECK")
    print("=" * 60)
    for field, value in decision.model_dump().items():
        status = "⚠️  NULL" if value is None else "✓ populated"
        print(f"  {field}: {status}")


if __name__ == "__main__":
    main()

