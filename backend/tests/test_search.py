"""Task I-6.1: search returns ranked rows, and keeps four promises.

1. It REFUSES, before doing any work, when the index was built for a different model than the one that would embed the question (a different
   model's vectors are nonsense: confident wrong answers, no crash), or when nothing is indexed yet.
2. It never returns a chunk of a flagged file (a file that could not be indexed): the old rows are stale. The answer layer gets the list of
   hidden files with their reasons, so it can say "X could not be indexed".
3. A vector with no row (an orphan) is invisible: rows come from SQLite, which is the truth.
4. It keeps looking until it has k visible results, even if the nearest vectors belong to hidden files or orphans.
The fake embedder's `embed_query` does not wrap the text, so asking with a chunk's exact `embed_text` finds that chunk (score about 1.0).
"""
import pytest

import chunk_store
from chunker import chunk_file
from embedding import FakeEmbedder
from indexing import index_project
from search import IndexOutOfDate, search
from vectorstore import InMemoryVectorStore

FILES = {
    "shop.py": "def order_total(items):\n    return sum(i.price for i in items)\n\n\ndef apply_discount(total, pct):\n    return total * (1 - pct)\n",
    "net.py": "def retry_request(url, attempts=3):\n    for _ in range(attempts):\n        pass\n    return None\n",
    "util.py": "def slugify(text):\n    return text.lower().replace(' ', '-')\n",
}
BIG = "def big():\n    return '" + "x" * 400 + "'\n"


@pytest.fixture
def indexed(conn, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in FILES.items():
        (repo / name).write_text(text)
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, repo, e, store)
    return repo, e, store


def row_text(conn, rel_path, symbol):
    return next(r["embed_text"] for r in chunk_store.chunks_for_file(conn, 1, rel_path) if r["symbol"] == symbol)


def test_asking_with_a_chunks_own_text_finds_that_chunk_first_with_its_full_row(conn, indexed):
    _, e, store = indexed
    result = search(conn, 1, e, store, row_text(conn, "net.py", "retry_request"), k=3)
    top = result.hits[0]
    assert top.chunk["rel_path"] == "net.py" and top.chunk["symbol"] == "retry_request" and top.score == pytest.approx(1.0, abs=1e-6)
    assert {"id", "text", "embed_text", "start_line", "end_line", "kind", "part", "part_count"} <= set(top.chunk)   # the whole stored row
    scores = [h.score for h in result.hits]
    assert scores == sorted(scores, reverse=True) and len(result.hits) == 3


def test_k_limits_the_results_and_a_big_k_returns_everything_there_is(conn, indexed):
    _, e, store = indexed
    question = row_text(conn, "util.py", "slugify")
    assert len(search(conn, 1, e, store, question, k=1).hits) == 1
    everything = search(conn, 1, e, store, question, k=1000).hits
    assert {h.chunk["id"] for h in everything} == chunk_store.all_ids(conn, 1)


def test_the_question_is_embedded_as_a_question_once(conn, indexed):
    _, e, store = indexed
    batches, queries = e.batch_count, e.query_count
    search(conn, 1, e, store, "how do I slugify text?", k=2)
    assert e.query_count == queries + 1 and e.batch_count == batches


def test_a_vector_with_no_row_is_never_returned_even_when_it_is_the_nearest(conn, indexed):
    _, e, store = indexed
    question = row_text(conn, "shop.py", "order_total")
    store.upsert(["orphan-vector"], [e.embed_query(question)])               # exactly the question's vector, but no row exists
    result = search(conn, 1, e, store, question, k=3)
    assert "orphan-vector" not in [h.chunk["id"] for h in result.hits] and len(result.hits) == 3


# ---- hiding flagged files ----

def flag_a_good_file(conn, repo, e, store, rel="net.py"):
    (repo / rel).write_text(BIG)                                             # a good file becomes too long: skipped, flagged, old rows kept
    report = index_project(conn, 1, repo, FakeEmbedder(max_chars=300), store)
    assert rel in chunk_store.failed_files(conn, 1) and chunk_store.ids_for_file(conn, 1, rel)   # the stale rows really are still there
    return report


def test_a_flagged_files_old_chunks_are_never_returned_but_the_reason_is(conn, indexed):
    repo, e, store = indexed
    stale_text = row_text(conn, "net.py", "retry_request")
    stale_ids = chunk_store.ids_for_file(conn, 1, "net.py")
    flag_a_good_file(conn, repo, e, store)
    assert stale_ids & store.ids()                                           # their vectors are still in the store: only the filter hides them
    result = search(conn, 1, e, store, stale_text, k=10)                     # asking for exactly the hidden chunk
    assert not (stale_ids & {h.chunk["id"] for h in result.hits})
    assert {h.chunk["rel_path"] for h in result.hits} == {"shop.py", "util.py"}
    assert set(result.hidden_files) == {"net.py"} and "EmbeddingTooLong" in result.hidden_files["net.py"]


def test_the_hidden_list_is_complete_even_when_no_hit_comes_from_those_files(conn, indexed):
    repo, e, store = indexed
    flag_a_good_file(conn, repo, e, store, "net.py")
    (repo / "util.py").write_text(BIG)
    index_project(conn, 1, repo, FakeEmbedder(max_chars=300), store)
    result = search(conn, 1, e, store, row_text(conn, "shop.py", "order_total"), k=1)
    assert set(result.hidden_files) == {"net.py", "util.py"} and [h.chunk["rel_path"] for h in result.hits] == ["shop.py"]


def test_nothing_is_hidden_when_nothing_failed(conn, indexed):
    _, e, store = indexed
    assert search(conn, 1, e, store, "anything", k=3).hidden_files == {}


def test_a_fixed_file_comes_back(conn, indexed):
    repo, e, store = indexed
    flag_a_good_file(conn, repo, e, store)
    (repo / "net.py").write_text(FILES["net.py"])
    index_project(conn, 1, repo, e, store)
    result = search(conn, 1, e, store, row_text(conn, "net.py", "retry_request"), k=3)
    assert result.hidden_files == {} and result.hits[0].chunk["rel_path"] == "net.py"


def test_it_keeps_looking_when_the_nearest_vectors_are_all_hidden_or_orphans(conn, tmp_path):
    # Hand-made 2-d vectors: 40 hidden chunks and 40 orphans are all NEARER to the question than the 5 visible ones.
    template = tmp_path / "t.py"
    template.write_text("def f():\n    return 1\n")
    base = chunk_file(str(template), repo_root=str(tmp_path))[0]

    def chunk(i, rel):
        return dict(base, id=f"{rel}-{i:03d}", rel_path=rel, symbol=f"f{i}", names=[f"f{i}"], start_line=i + 1, end_line=i + 1,
                    text=f"line {i}", embed_text=f"{rel} · f{i}\nline {i}", content_hash=f"h-{rel}-{i}")

    store = InMemoryVectorStore()
    store.set_signature("fake-hash-2", 2)
    spec = {"bad.py": (40, 0.0), "good.py": (5, 1.0)}                          # (how many, angle offset): bad.py is the nearest
    for rel, (n, far) in spec.items():
        chunks = [chunk(i, rel) for i in range(n)]
        chunk_store.save_file_chunks(conn, 1, rel, "h", chunks, embedded={c["id"]: ("fake-hash-2", 2) for c in chunks})
        store.upsert([c["id"] for c in chunks], [[1.0, far + 0.001 * (i + 1)] for i, c in enumerate(chunks)])
    store.upsert([f"orphan-{i}" for i in range(40)], [[1.0, 0.0005 * (i + 1)] for i in range(40)])   # nearest of all, no rows
    chunk_store.mark_file_failed(conn, 1, "bad.py", "EmbeddingTooLong: x", file_hash="h", chunker_version="v")

    class Question(FakeEmbedder):
        def __init__(self):
            super().__init__(dim=2)

        def embed_query(self, text):
            return [1.0, 0.0]

    result = search(conn, 1, Question(), store, "q", k=5)
    assert len(result.hits) == 5 and {h.chunk["rel_path"] for h in result.hits} == {"good.py"}
    assert set(result.hidden_files) == {"bad.py"}


# ---- refusing ----

def test_it_refuses_a_store_built_for_another_model_before_embedding_anything(conn, indexed):
    _, _, store = indexed
    other = FakeEmbedder(digest="new-weights")                               # same tag and size, replaced weights
    with pytest.raises(IndexOutOfDate, match=r"fake-hash-8.*fake-hash-8@new-weights.*re-index"):
        search(conn, 1, other, store, "anything", k=3)
    assert other.query_count == 0                                            # the question was not even embedded


def test_it_refuses_a_different_vector_size(conn, indexed):
    _, _, store = indexed
    with pytest.raises(IndexOutOfDate):
        search(conn, 1, FakeEmbedder(dim=16), store, "anything", k=3)


def test_it_refuses_when_nothing_has_been_indexed_yet(conn):
    e = FakeEmbedder()
    with pytest.raises(IndexOutOfDate, match="not been indexed"):
        search(conn, 1, e, InMemoryVectorStore(), "anything", k=3)
    assert e.query_count == 0


def test_a_missing_digest_hints_at_warmup(conn, tmp_path):
    # The real embedder only knows its digest after warmup(). If the app forgot to warm it up, its identity is the bare tag.
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    store = InMemoryVectorStore()
    index_project(conn, 1, repo, FakeEmbedder(digest="d1"), store)
    with pytest.raises(IndexOutOfDate, match="warmup"):
        search(conn, 1, FakeEmbedder(), store, "q", k=1)                     # no digest yet


# ---- bad arguments ----

@pytest.mark.parametrize("question,k", [("", 3), ("   ", 3), (None, 3), ("fine", 0), ("fine", -2), ("fine", 2.5), ("fine", True)])
def test_bad_arguments_are_refused_before_any_work(conn, indexed, question, k):
    _, e, store = indexed
    with pytest.raises(ValueError):
        search(conn, 1, e, store, question, k=k)
    assert e.query_count == 0


def test_another_projects_rows_are_never_mixed_in(conn, indexed, tmp_path):
    repo, e, store = indexed
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    conn.commit()
    other_store = InMemoryVectorStore()
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    (other_repo / "only.py").write_text("def only_here():\n    return 1\n")
    index_project(conn, 2, other_repo, e, other_store)
    result = search(conn, 2, e, other_store, "anything", k=10)
    assert {h.chunk["rel_path"] for h in result.hits} == {"only.py"}
