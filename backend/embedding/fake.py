"""A fake embedder for tests: deterministic, meaningless, instant. It cannot test search quality."""
import hashlib
import math

from .base import Vector


class FakeEmbedder:
    def __init__(self, dim: int = 8):
        self.dim = dim
        self.model_name = f"fake-hash-{dim}"  # the name carries dim, so changing dim looks like a model change
        self.embedded_texts: list[str] = []   # every document text, in the order embedded
        self.batch_count = 0                  # embed_documents calls
        self.query_count = 0                  # embed_query calls

    @property
    def text_count(self) -> int:
        return len(self.embedded_texts)

    def embed_documents(self, texts: list[str], ids: list[str] | None = None) -> list[Vector]:
        if ids is not None and len(ids) != len(texts):
            raise ValueError(f"{len(texts)} texts but {len(ids)} ids")
        for t in texts:
            if not isinstance(t, str):
                raise TypeError(f"can only embed str, got {type(t).__name__}")
        if not texts:
            return []
        self.batch_count += 1
        self.embedded_texts.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> Vector:
        # No instruction wrapper here (the real embedder has one), so a test can find a chunk
        # by asking with that chunk's exact text.
        self.query_count += 1
        return self._vector(text)

    def _vector(self, text: str) -> Vector:
        raw = []
        block = 0
        while len(raw) < self.dim:  # sha256 gives 32 bytes per block; ask for as many blocks as dim needs
            raw.extend(hashlib.sha256(f"{block}:{text}".encode("utf-8")).digest())
            block += 1
        values = [b / 127.5 - 1.0 for b in raw[:self.dim]]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
