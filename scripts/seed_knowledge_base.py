"""
RUN THIS ON YOUR OWN MACHINE (not in the chat sandbox).

First run downloads ~130MB model from huggingface.co and caches it in
~/.cache/fastembed - after that it's fully offline.

    pip install -r requirements.txt
    python -m scripts.seed_knowledge_base
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.embeddings import FastEmbedder
from app.knowledge_base import ResolvedTicketKB


def main():
    with open("data/resolved_tickets.json") as f:
        resolved_tickets = json.load(f)

    print("Loading embedding model (first run downloads ~130MB, then it's cached)...")
    embedder = FastEmbedder()

    kb = ResolvedTicketKB(embedder=embedder, persist_path="./chroma_data")

    for t in resolved_tickets:
        kb.add_resolved_ticket(
            ticket_id=t["ticket_id"],
            subject=t["subject"],
            description=t["description"],
            resolution=t["resolution"],
        )
        print(f"  ingested {t['ticket_id']}: {t['subject']}")

    print(f"\nKnowledge base seeded with {kb.count()} resolved tickets at ./chroma_data")
    print("Next: run scripts/query_demo.py to test retrieval quality with a real new ticket.")


if __name__ == "__main__":
    main()
