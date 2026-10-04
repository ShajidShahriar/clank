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
- Tests and changelogs are demoted in the ORDER (never in the score: every hit keeps its raw cosine) by a margin that was measured for one model
  (see `policy.py`). The policy ranks a pool of max(3k, 30) hits and then cuts to k, so an answer far down the raw list can come up; hidden files and
  orphans never use up pool slots. For any other model nothing is demoted and `ranking_note` says why. `test_policy=None` is the plain raw order.
"""
from dataclasses import dataclass, field

import chunk_store
from .hit import Hit
from .policy import DEFAULT_DEMOTION, DemotionPolicy, apply_test_policy

POOL_MINIMUM = 30      # the demotion ranks at least this many hits before cutting to k


class IndexOutOfDate(RuntimeError):
    """The index cannot be searched with this embedder (or does not exist yet). The message says what to do."""


@dataclass
class SearchResult:
    hits: list[Hit]                                        # best first, at most k. Every score is the raw cosine, even when the order is not by raw score
    hidden_files: dict = field(default_factory=dict)       # {rel_path: reason} flagged files whose chunks were left out of the hits
    ranking: str = "raw"                                   # "demoted": tests and changelogs were demoted in the ORDER; "raw": the order is by score
    ranking_note: str | None = None                        # why a requested demotion was not applied (for the caller, not for the LLM)


def search(conn, project_id, embedder, store, question, k=10, *, test_policy=DEFAULT_DEMOTION) -> SearchResult:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("the question must be a non-empty string")
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError(f"k must be a positive integer, got {k!r}")
    if test_policy is not None and not isinstance(test_policy, DemotionPolicy):
        raise ValueError(f"test_policy must be a DemotionPolicy or None, got {test_policy!r}")
    _require_a_matching_index(store, embedder)

    note = None
    demote = test_policy is not None and embedder.model_name == test_policy.calibrated_for
    if test_policy is not None and not demote:
        note = (f"tests and changelogs were not demoted: the margin was calibrated for {test_policy.calibrated_for}, "
                f"this index uses {embedder.model_name}; measure a margin for this model first")
    wanted = max(3 * k, POOL_MINIMUM) if demote else k       # the policy ranks a wider pool than k, then cuts to k

    hidden = chunk_store.failed_files(conn, project_id)
    vector = embedder.embed_query(question)

    total = store.count()
    fetch = max(2 * wanted, 20)
    while True:
        ranked = store.query(vector, min(fetch, total))
        rows = {row["id"]: row for row in chunk_store.get_chunks(conn, project_id, [i for i, _ in ranked])}   # orphans have no row
        visible = [Hit(rows[i], score) for i, score in ranked if i in rows and rows[i]["rel_path"] not in hidden]
        if len(visible) >= wanted or len(ranked) >= total:
            break
        fetch *= 2        # too many hits were hidden or orphans: look further down the list
    if not demote:
        return SearchResult(hits=visible[:k], hidden_files=dict(hidden), ranking_note=note)
    pool = visible[:wanted]
    tags = chunk_store.file_tags(conn, project_id, {h.chunk["rel_path"] for h in pool})      # one batched lookup, not one per hit
    ordered = apply_test_policy(pool, question, tags, margin=test_policy.margin, changelog_margin=test_policy.changelog_margin)
    return SearchResult(hits=[a.hit for a in ordered[:k]], hidden_files=dict(hidden), ranking="demoted")


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
