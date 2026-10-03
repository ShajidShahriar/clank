"""search: the question -> the best matching stored chunks, as rows from SQLite.

What it promises (each is tested):
- It REFUSES before doing any work if the index was built for a different embedder than the one asked to embed the question: vectors from
  another model (or other weights behind the same tag) are comparable-looking nonsense, which would give confident wrong answers. It also
  refuses a project that has not been indexed yet.
- It never returns a chunk of a FLAGGED file (one that could not be indexed; its old rows are stale). The full list of flagged files with
  their reasons is returned, so the answer layer can say "X could not be indexed".
- Rows come from SQLite, the truth: a vector with no row (an orphan from a crash) is invisible.
- It keeps asking the vector store for more until it has k visible results, or the store is exhausted: the nearest vectors may all belong
  to hidden files or be orphans.
"""
from dataclasses import dataclass, field

import chunk_store


class IndexOutOfDate(RuntimeError):
    """The index cannot be searched with this embedder (or does not exist yet). The message says what to do."""


@dataclass
class Hit:
    chunk: dict        # the stored row (see chunk_store.get_chunks)
    score: float       # cosine similarity, higher is better


@dataclass
class SearchResult:
    hits: list[Hit]                                        # best first, at most k
    hidden_files: dict = field(default_factory=dict)       # {rel_path: reason} flagged files whose chunks were left out


def search(conn, project_id, embedder, store, question, k=10) -> SearchResult:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("the question must be a non-empty string")
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError(f"k must be a positive integer, got {k!r}")
    _require_a_matching_index(store, embedder)

    hidden = chunk_store.failed_files(conn, project_id)
    vector = embedder.embed_query(question)

    total = store.count()
    fetch = max(2 * k, 20)
    while True:
        ranked = store.query(vector, min(fetch, total))
        rows = {row["id"]: row for row in chunk_store.get_chunks(conn, project_id, [i for i, _ in ranked])}   # orphans have no row
        visible = [Hit(rows[i], score) for i, score in ranked if i in rows and rows[i]["rel_path"] not in hidden]
        if len(visible) >= k or len(ranked) >= total:
            break
        fetch *= 2        # too many hits were hidden or orphans: look further down the list
    return SearchResult(hits=visible[:k], hidden_files=dict(hidden))


def _require_a_matching_index(store, embedder):
    built_for = store.signature()
    if built_for is None:
        raise IndexOutOfDate("this project has not been indexed yet: run indexing first")
    current = (embedder.model_name, embedder.dim)
    if built_for == current:
        return
    message = (f"the index is out of date: it was built for {built_for[0]} ({built_for[1]} numbers per vector) but the embedder is "
               f"{current[0]} ({current[1]}): re-index this project")
    if "@" in built_for[0] and "@" not in current[0] and built_for[0].split("@")[0] == current[0]:
        message += " (the embedder has no digest yet: was warmup() called at startup? It only learns its digest then)"
    raise IndexOutOfDate(message)
