"""
RUN THIS ON YOUR OWN MACHINE, after seed_knowledge_base.py.

    python -m scripts.query_demo

The whole point of this script: prove that a REAL embedding model
catches semantic similarity that word-overlap methods (TF-IDF, hashing,
bag-of-words) cannot. So the test query below deliberately shares almost
NO exact words with the ticket it should match.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.embeddings import FastEmbedder
from app.knowledge_base import ResolvedTicketKB


TEST_QUERIES = [
    # Original tests - real semantic paraphrase matches
    "My data pull to spreadsheet just spins forever and never finishes",
    "App force closes right after I type my password and hit sign in",
    "How do I change my billing address on my account",

    # EDGE CASE: lexical trap - shares almost every word with ZD-100/108,
    # but is actually a DIFFERENT feature (contacts vs reports export).
    # Watch whether this gets confused with the reports-export cluster.
    "Cannot export my contacts to a CSV file, button does not respond",

    # EDGE CASE: same broad domain (auth) as ZD-101, but a different
    # problem (lockout vs delayed email). Tests fine-grained discrimination.
    "My account got locked out after typing my password wrong a few times",

    # EDGE CASE: near-duplicate of ZD-100/108 - should be the TIGHTEST
    # match in the whole test set, a sanity floor.
    "The CSV export button on reports does not respond when I click it",

    # EDGE CASE: deliberately vague, no real answer should exist.
    # Watch for uniformly high, unseparated distances.
    "Something is wrong and I need this fixed asap",

    # EDGE CASE: cross-source bridging - customer language for a bug
    # that is documented in engineering terms (ENG-193). This is the
    # actual point of the whole system: connect support tickets to
    # engineering bug history even when the vocabulary differs.
    "Getting 429 errors from your API during busy periods, way under our rate limit",

    # EDGE CASE: harder cross-source bridging - customer describes a
    # SYMPTOM (double charge) of an engineering root cause (ENG-201)
    # using payments language, not 'duplicate database rows' language.
    "I was billed twice for one purchase, please refund the extra charge",
]


def main():
    embedder = FastEmbedder()
    kb = ResolvedTicketKB(embedder=embedder, persist_path="./chroma_data")

    if kb.count() == 0:
        print("Knowledge base is empty - run scripts/seed_knowledge_base.py first.")
        return

    for query in TEST_QUERIES:
        print("=" * 70)
        print(f"NEW TICKET: {query!r}")
        print("=" * 70)
        matches = kb.query(query, top_k=3)
        for m in matches:
            print(f"  [{m['distance']:.4f}] {m['ticket_id']}: {m['subject']}")
            print(f"           → {m['resolution'][:100]}...")
        print()


if __name__ == "__main__":
    main()
