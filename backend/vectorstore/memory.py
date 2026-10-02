"""An in-memory vector store: brute-force cosine similarity. For tests, and the reference for what other stores must do."""
import math

from .base import Vector, check_upsert, check_vector


class InMemoryVectorStore:
    def __init__(self):
        self._vectors: dict[str, Vector] = {}

    def upsert(self, ids: list[str], vectors: list[Vector]) -> None:
        check_upsert(ids, vectors)
        if not ids:
            return
        self._check_dim(len(vectors[0]), "upsert")
        for i, v in zip(ids, vectors):
            self._vectors[i] = [float(x) for x in v]

    def delete(self, ids: list[str]) -> None:
        for i in ids:
            self._vectors.pop(i, None)

    def ids(self) -> set[str]:
        return set(self._vectors)

    def count(self) -> int:
        return len(self._vectors)

    def query(self, vector: Vector, k: int) -> list[tuple[str, float]]:
        check_vector(vector, "query vector")
        if k < 0:
            raise ValueError("k must be 0 or more")
        self._check_dim(len(vector), "query")
        scored = [(i, _cosine(vector, v)) for i, v in self._vectors.items()]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))  # best first; equal scores by id so results are stable
        return scored[:k]

    def clear(self) -> None:
        self._vectors.clear()

    def _check_dim(self, dim: int, what: str) -> None:
        # The size is whatever is stored now, so an empty store accepts any size (nothing to compare with).
        if self._vectors:
            held = len(next(iter(self._vectors.values())))
            if dim != held:
                raise ValueError(f"{what} has {dim} numbers per vector but this store holds {held}")


def _cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))))
