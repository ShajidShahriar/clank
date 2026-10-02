"""The Chroma vector store: one persistent collection per project, cosine distance pinned.

Chroma holds only id -> vector (indexing decision 1). Facts checked against Chroma 1.5.9 (see the devlog):
- its distance for the cosine space is 1 - cosine similarity, so the score is 1 - distance;
- it accepts an all-zero vector silently and raises on empty upserts, empty deletes and k=0, so this wrapper checks first;
- get_or_create_collection ignores the settings you pass when the collection already exists, so an existing one is checked.
"""
from pathlib import Path

import chromadb
from chromadb.config import Settings
from chromadb.errors import InvalidArgumentError

from .base import Vector, check_signature, check_upsert, check_vector


class ChromaVectorStore:
    def __init__(self, path, name: str):
        self._name = name
        # Telemetry off: this is a private tool and nothing should phone home.
        self._client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
        self._collection = self._open()

    def _open(self):
        collection = self._client.get_or_create_collection(
            self._name, embedding_function=None, configuration={"hnsw": {"space": "cosine"}})
        space = (collection.configuration_json or {}).get("hnsw", {}).get("space")
        if space != "cosine":
            raise RuntimeError(
                f"Chroma collection '{self._name}' uses '{space}' distance, but scores here are cosine similarity. "
                f"Delete the collection (or the vector folder) and re-index.")
        return collection

    def upsert(self, ids: list[str], vectors: list[Vector]) -> None:
        check_upsert(ids, vectors)
        if not ids:  # Chroma raises on an empty upsert
            return
        try:
            self._collection.upsert(ids=list(ids), embeddings=[[float(x) for x in v] for v in vectors])
        except InvalidArgumentError as e:  # e.g. a different vector length than the collection holds
            raise ValueError(str(e)) from e

    def delete(self, ids: list[str]) -> None:
        if ids:  # Chroma raises on an empty delete; unknown ids are ignored by Chroma
            self._collection.delete(ids=list(ids))

    def ids(self) -> set[str]:
        return set(self._collection.get(include=[])["ids"])

    def count(self) -> int:
        return self._collection.count()

    def query(self, vector: Vector, k: int) -> list[tuple[str, float]]:
        check_vector(vector, "query vector")
        if k < 0:
            raise ValueError("k must be 0 or more")
        if k == 0:  # Chroma raises on k=0. (A k larger than the store, and an empty store, are fine in Chroma 1.5.9.)
            return []
        try:
            found = self._collection.query(query_embeddings=[[float(x) for x in vector]], n_results=k)
        except InvalidArgumentError as e:
            raise ValueError(str(e)) from e
        # cosine distance = 1 - cosine similarity
        return [(i, max(-1.0, min(1.0, 1.0 - d))) for i, d in zip(found["ids"][0], found["distances"][0])]

    def signature(self) -> tuple[str, int] | None:
        # read fresh: the collection object we hold can have stale metadata
        meta = self._client.get_collection(self._name, embedding_function=None).metadata or {}
        if "signature_model" in meta and "signature_dim" in meta:
            return (meta["signature_model"], int(meta["signature_dim"]))
        return None

    def set_signature(self, model: str, dim: int) -> None:
        check_signature(model, dim)
        self._collection.modify(metadata={"signature_model": model, "signature_dim": dim})

    def clear(self) -> None:
        self._client.delete_collection(self._name)
        self._collection = self._open()


def open_project_store(base_dir, project_id: int) -> ChromaVectorStore:
    """The vector store of one project, kept in <base_dir>/chroma."""
    return ChromaVectorStore(Path(base_dir) / "chroma", f"project_{project_id}")
