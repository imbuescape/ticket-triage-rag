"""
The 'resolved-tickets' knowledge base, backed by Chroma.

Notice this file never imports fastembed, OpenAI, or anything model-
specific. It takes an Embedder (see embeddings.py) as a constructor
argument - dependency injection. This is what makes it testable without
network access, and swappable in production without a rewrite.

Each entry we store is a RESOLVED ticket: the original problem PLUS the
resolution that fixed it. That's the whole point of the knowledge base -
when a new ticket comes in, we're not just finding "similar past tickets",
we're finding past tickets whose RESOLUTION might apply to this new one.
"""

import chromadb
from app.embeddings import Embedder


class ResolvedTicketKB:
    def __init__(self, embedder: Embedder, persist_path: str = "./chroma_data"):
        self._embedder = embedder
        self._client = chromadb.PersistentClient(path=persist_path)
        # We manage embeddings ourselves (via the injected Embedder) rather
        # than letting Chroma manage them internally - this keeps the
        # swap-the-model seam clean and explicit.
        self._collection = self._client.get_or_create_collection(
            name="resolved_tickets",
            metadata={"hnsw:space": "cosine"},  # cosine similarity, standard for text embeddings
        )

    def add_resolved_ticket(
        self, ticket_id: str, subject: str, description: str, resolution: str,
        origin: str = "seed", verified: bool = True,
    ):
        """
        origin/verified track PROVENANCE - where this entry came from and
        whether a human ever confirmed it's actually correct:
          - origin="seed", verified=True: hand-curated seed data (default,
            backward compatible with every existing call site)
          - origin="auto_resolved", verified=False: written back
            immediately after the system auto-resolved a ticket itself -
            nobody has confirmed this was actually the right call yet
          - origin="human_verified", verified=True: a human explicitly
            confirmed this resolution via POST /tickets/{id}/resolve

        This is visible provenance, not a full solution to the risk of a
        wrong auto-resolution reinforcing itself as future "precedent" -
        but it means that risk is at least inspectable rather than hidden.
        """
        embedding_text = f"{subject}\n\n{description}"
        vector = self._embedder.embed([embedding_text])[0]

        self._collection.upsert(
            ids=[ticket_id],
            embeddings=[vector],
            documents=[embedding_text],
            metadatas=[{
                "subject": subject,
                "resolution": resolution,
                "origin": origin,
                "verified": verified,
            }],
        )

    def query(self, ticket_text: str, top_k: int = 5) -> list[dict]:
        query_vector = self._embedder.embed([ticket_text])[0]

        results = self._collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
        )

        matches = []
        for i in range(len(results["ids"][0])):
            metadata = results["metadatas"][0][i]
            matches.append({
                "ticket_id": results["ids"][0][i],
                "subject": metadata["subject"],
                "resolution": metadata["resolution"],
                "distance": results["distances"][0][i],
                "origin": metadata.get("origin", "seed"),
                "verified": metadata.get("verified", True),
            })
        return matches

    def count(self) -> int:
        return self._collection.count()
