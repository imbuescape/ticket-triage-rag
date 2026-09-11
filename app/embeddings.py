"""
The embedding layer is defined as an INTERFACE (a Protocol), not a concrete
class. Why this matters: your knowledge base code (knowledge_base.py) will
depend on "something that turns text into vectors" - it should never
import fastembed, OpenAI, or Cohere directly. That's what lets you swap
the model in Phase 4+ without touching retrieval logic, and it's what
lets us TEST the vector store here without needing network access to
download real model weights.

This is the same principle as app/models.py's Ticket schema: define the
seam, then let concrete implementations plug into it.
"""

from typing import Protocol


class Embedder(Protocol):
    """Anything with this shape can be used as our embedding backend."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    @property
    def dimension(self) -> int:
        ...


class FastEmbedder:
    """
    Real production embedder using BAAI/bge-small-en-v1.5 via fastembed
    (ONNX runtime, no PyTorch needed).

    NOTE: downloads ~130MB of model weights from huggingface.co on first
    run. This sandbox's network allowlist blocks huggingface.co, so this
    class must be run on YOUR machine, not in this chat. Once downloaded,
    weights are cached locally (~/.cache/fastembed) and it never needs
    network access again.
    """

    _DIM = 384  # bge-small's known output dimension

    def __init__(self):
        from fastembed import TextEmbedding  # imported lazily so this
        # module can still be imported (and tested) even where fastembed
        # itself can't successfully initialize.
        self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(texts)]

    @property
    def dimension(self) -> int:
        return self._DIM


if __name__ == "__main__":
    # Run this locally: venv/bin/python -m app.embeddings
    embedder = FastEmbedder()
    vectors = embedder.embed(["export to CSV is broken", "cannot download report"])
    print(f"Got {len(vectors)} vectors, dimension {len(vectors[0])}")
    print("First 5 values of vector 1:", vectors[0][:5])
