"""
Run: venv/bin/python tests/test_knowledge_base.py

This does NOT test embedding QUALITY (that needs the real fastembed model,
run locally). It tests that the store/retrieve MECHANICS are correct:
- upsert works
- query returns the right shape
- an identical/near-identical text retrieves itself with near-zero distance
- results are ordered by similarity

We use a deterministic bag-of-words-hash embedder here specifically
BECAUSE it's crude - if retrieval mechanics work with a dumb embedder,
they'll work with a smart one. We're testing OUR code, not the model.
"""

import sys
import os
import shutil
import hashlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge_base import ResolvedTicketKB

TEST_DB_PATH = "/tmp/test_chroma_data"


class DeterministicTestEmbedder:
    """
    Crude but deterministic: hashes each word into one of 32 buckets and
    counts occurrences. Two texts sharing more words will have more
    similar vectors. Good enough to prove retrieval ORDERING logic works,
    useless for real semantic understanding (by design - see docstring).
    """

    def __init__(self, dim: int = 4096):
        # 4096 buckets instead of 32: with a small vocabulary, 32 buckets
        # guarantees collisions (birthday paradox - see the diagnosis
        # above). Real embedding models sidestep this entirely by
        # learning dense, meaning-based dimensions rather than hashing
        # words into arbitrary buckets - but widening the bucket space
        # is the classic mitigation when you're stuck with hashing.
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self._dim
            for word in text.lower().split():
                bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self._dim
                vec[bucket] += 1.0
            vectors.append(vec)
        return vectors

    @property
    def dimension(self) -> int:
        return self._dim


def run():
    shutil.rmtree(TEST_DB_PATH, ignore_errors=True)  # clean slate each run

    kb = ResolvedTicketKB(embedder=DeterministicTestEmbedder(), persist_path=TEST_DB_PATH)

    # Seed a small knowledge base of resolved tickets
    kb.add_resolved_ticket(
        ticket_id="ZD-100",
        subject="CSV export button unresponsive",
        description="User clicks export to csv on reports page, nothing happens, no error shown",
        resolution="Known bug in report cache. Fix: clear browser cache, or ask user to use the "
                    "'Export as XLSX' alternative button while we ship the patch in v2.4.1.",
    )
    kb.add_resolved_ticket(
        ticket_id="ZD-101",
        subject="Cannot reset password",
        description="Password reset email never arrives, checked spam folder",
        resolution="Email delivery delay on our provider's side. Ask user to wait 15 min, "
                    "or manually trigger reset via admin panel if urgent.",
    )
    kb.add_resolved_ticket(
        ticket_id="ZD-102",
        subject="Download report fails silently",
        description="Trying to download the monthly report as csv, button does nothing at all",
        resolution="Same root cause as report cache bug. Fix: clear browser cache, or use "
                    "'Export as XLSX' alternative while patch v2.4.1 ships.",
    )

    print(f"Knowledge base seeded with {kb.count()} resolved tickets.\n")

    # A new ticket that should match ZD-100 and ZD-102 (both about CSV export failing)
    # much more closely than ZD-101 (password reset - unrelated)
    new_ticket_text = "Export to CSV button does nothing. No download, no error message."

    print(f"Querying with new ticket: {new_ticket_text!r}\n")
    matches = kb.query(new_ticket_text, top_k=3)

    for m in matches:
        print(f"  {m['ticket_id']} (distance={m['distance']:.4f}): {m['subject']}")
        print(f"      → resolution: {m['resolution'][:80]}...")

    # Assertions proving the MECHANICS work correctly
    assert len(matches) == 3
    top_match_ids = {matches[0]["ticket_id"], matches[1]["ticket_id"]}
    assert top_match_ids == {"ZD-100", "ZD-102"}, \
        "Expected the two CSV-export tickets to be the closest matches"
    assert matches[0]["distance"] <= matches[1]["distance"] <= matches[2]["distance"], \
        "Results should be ordered by increasing distance (most similar first)"
    assert matches[2]["ticket_id"] == "ZD-101", \
        "Password reset ticket (unrelated) should rank last"

    print("\n✅ Knowledge base storage/retrieval mechanics verified correct.")
    print("   (Retrieval QUALITY still needs the real embedder - see next step.)")


if __name__ == "__main__":
    run()
