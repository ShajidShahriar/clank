"""The embedder interface. Everything else in Clank talks to this, never to a specific model.

Swapping the model means writing one new class that has these members (indexing decision 2).
"""
from typing import Protocol, runtime_checkable

Vector = list[float]


@runtime_checkable
class Embedder(Protocol):
    model_name: str  # stored on every chunk row; a change means every old vector is stale (decision 5)
    dim: int         # length of every vector this embedder returns

    def embed_documents(self, texts: list[str], ids: list[str] | None = None) -> list[Vector]:
        """One vector per text, IN THE SAME ORDER as `texts`. `ids` (chunk ids, same length) are only
        used to name the chunk in an error message."""
        ...

    def embed_query(self, text: str) -> Vector:
        """The vector for a user's question. Models that want an instruction around the question add it here."""
        ...

    def warmup(self) -> None:
        """Get ready for the first real call (load the model). A no-op for embedders with nothing to load."""
        ...
