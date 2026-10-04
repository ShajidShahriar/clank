"""Hand-made 2-d worlds for search tests: a vector [s, sqrt(1 - s^2)] has cosine s with the question [1, 0], so every score is exact."""
import hashlib
import math

import chunk_store
from chunker import chunk_file
from embedding import FakeEmbedder
from vectorstore import InMemoryVectorStore

MODEL = "fake-hash-2"


class Question2D(FakeEmbedder):
    def __init__(self):
        super().__init__(dim=2)

    def embed_query(self, text):
        return [1.0, 0.0]


class CountingStore(InMemoryVectorStore):
    """Remembers how many results each query asked for."""

    def __init__(self):
        super().__init__()
        self.asked = []

    def query(self, vector, k):
        self.asked.append(k)
        return super().query(vector, k)


def vector(score):
    return [score, math.sqrt(1 - score * score)]


def make_world(conn, tmp_path, store=None):
    """build(spec): spec is {rel_path: dict(scores=[...], test=False, log=False)}; chunk ids are `<path>#<i>`; real files are written under build.repo."""
    template = tmp_path / "t.py"
    template.write_text("def f():\n    return 1\n")
    base = chunk_file(str(template), repo_root=str(tmp_path))[0]
    store = store or InMemoryVectorStore()
    store.set_signature(MODEL, 2)
    repo = tmp_path / "repo"
    repo.mkdir()

    def build(spec):
        for rel, info in spec.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(rel)
            file_hash = hashlib.sha1((repo / rel).read_bytes()).hexdigest()
            chunks = [dict(base, id=f"{rel}#{i}", rel_path=rel, symbol=f"s{i}", names=[f"s{i}"], start_line=i + 1, end_line=i + 1,
                           text=f"line {i}", embed_text=f"{rel} · s{i}\nline {i}", content_hash=f"h-{rel}-{i}") for i in range(len(info["scores"]))]
            chunk_store.save_file_chunks(conn, 1, rel, file_hash, chunks, is_test=info.get("test", False), is_changelog=info.get("log", False),
                                         embedded={c["id"]: (MODEL, 2) for c in chunks})
            store.upsert([c["id"] for c in chunks], [vector(s) for s in info["scores"]])
            if info.get("hidden"):
                chunk_store.mark_file_failed(conn, 1, rel, "EmbeddingTooLong: x", file_hash=file_hash, chunker_version="v")
        return store
    build.repo, build.store = repo, store
    return build
