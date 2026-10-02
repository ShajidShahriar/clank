"""The vector store interface. One store holds the vectors of ONE project.

SQLite is the truth about chunks; a vector store only maps chunk id -> vector and finds nearest neighbours
(indexing decision 1). Scores are cosine SIMILARITY: higher is better, 1.0 means the same direction. Pinned here so a
library's default distance (Chroma's is L2) can never change what a score means.
"""
import math
from typing import Protocol, runtime_checkable

Vector = list[float]


@runtime_checkable
class VectorStore(Protocol):
    def upsert(self, ids: list[str], vectors: list[Vector]) -> None:
        """Store or replace one vector per id. Refuses bad input (see check_vectors) and then stores nothing."""
        ...

    def delete(self, ids: list[str]) -> None:
        """Remove these ids. Unknown ids are ignored."""
        ...

    def ids(self) -> set[str]:
        """Every stored id (a copy). The reconcile step compares this with SQLite's ids."""
        ...

    def count(self) -> int:
        ...

    def query(self, vector: Vector, k: int) -> list[tuple[str, float]]:
        """The k nearest stored ids with their cosine similarity, best first. Fewer if the store is smaller."""
        ...

    def clear(self) -> None:
        """Remove everything (after a schema or model change). A different vector length is allowed afterwards.
        The signature is forgotten too."""
        ...

    def signature(self) -> tuple[str, int] | None:
        """(model name, vector length) this store was filled for, or None if nobody has said. The indexer compares it
        with the embedder in use and rebuilds the store when they differ (a different model's vectors are nonsense)."""
        ...

    def set_signature(self, model: str, dim: int) -> None:
        ...


def check_vector(vector: Vector, what: str = "vector") -> None:
    """Refuse a vector that cannot be compared by cosine: empty, non-numbers, NaN, infinity, or all zeros."""
    if not isinstance(vector, (list, tuple)) or not vector:
        raise ValueError(f"{what} is empty or not a list")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vector):
        raise ValueError(f"{what} contains something other than finite numbers")
    if not any(vector):
        raise ValueError(f"{what} is all zeros, so its direction (cosine) means nothing")


def check_signature(model, dim) -> None:
    if not isinstance(model, str) or not model:
        raise ValueError(f"signature model must be a non-empty string, got {model!r}")
    if not isinstance(dim, int) or isinstance(dim, bool) or dim < 1:
        raise ValueError(f"signature dim must be a positive integer, got {dim!r}")


def check_upsert(ids: list[str], vectors: list[Vector]) -> None:
    if len(ids) != len(vectors):
        raise ValueError(f"{len(ids)} ids but {len(vectors)} vectors")
    if len(set(ids)) != len(ids):
        raise ValueError("the same id appears twice in one upsert")
    for i, v in zip(ids, vectors):
        check_vector(v, f"vector for {i}")
    if len({len(v) for v in vectors}) > 1:
        raise ValueError("vectors in one upsert have different lengths")
