"""Task I-4.5c: index_project, the whole pipeline: discover -> chunk -> plan -> embed -> vector store -> SQLite -> delete stale.

Order matters (indexing decision 4): a chunk officially exists only once its SQLite row is committed, so vectors go in BEFORE the
rows and stale ids are deleted AFTER them. A crash then leaves at worst an orphan vector, never a row that claims a vector it does not have.
Tests use the fake embedder and the in-memory store: no model, no heat.
"""
from pathlib import Path

import pytest

import chunk_store
import indexing
from embedding import EmbeddingTooLong, FakeEmbedder
from indexing import index_project
from vectorstore import InMemoryVectorStore

FILES = {
    "shop.py": "import os\n\n\ndef order(x):\n    return x + 1\n\n\ndef refund(x):\n    return x - 1\n",
    "util.py": "def helper():\n    return 1\n",
    "web/app.js": "function render() {\n  return 1;\n}\n",
    "README.md": "# Shop\n\nA tiny shop.\n\n## Usage\n\nRun it.\n",
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    for name, text in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def run(conn, repo, embedder=None, store=None, **kw):
    embedder = embedder or FakeEmbedder()
    store = store if store is not None else InMemoryVectorStore()
    return index_project(conn, 1, repo, embedder, store, **kw), embedder, store


def assert_stores_agree(conn, store, embedder):
    """The invariant: SQLite ids == vector ids, and every row records the model that embedded it."""
    assert store.ids() == chunk_store.all_ids(conn, 1)
    assert chunk_store.models_in_use(conn, 1) == {(embedder.model_name, embedder.dim)}
    assert chunk_store.needs_embedding(conn, 1, embedder.model_name, embedder.dim) == []


def test_first_run_indexes_everything(conn, repo):
    report, e, store = run(conn, repo)
    assert_stores_agree(conn, store, e)
    assert set(chunk_store.file_hashes(conn, 1)) == set(FILES)
    assert report.files_seen == 4 and report.embedded == e.text_count == store.count() > 0
    assert report.skipped == [] and report.deleted_files == 0


def test_a_second_run_embeds_nothing(conn, repo):
    _, e, store = run(conn, repo)
    before = (e.text_count, dict(store._vectors))
    report, _, _ = run(conn, repo, e, store)
    assert (e.text_count, store._vectors) == before and report.embedded == 0
    assert report.unchanged > 0


def test_editing_one_function_embeds_exactly_one_chunk(conn, repo):
    _, e, store = run(conn, repo)
    (repo / "shop.py").write_text(FILES["shop.py"].replace("x + 1", "x + 100"))
    seen = e.text_count
    report, _, _ = run(conn, repo, e, store)
    assert e.text_count - seen == 1 and report.embedded == 1
    assert e.embedded_texts[-1].startswith("shop.py · order") and "x + 100" in e.embedded_texts[-1]
    assert_stores_agree(conn, store, e)


def test_inserting_lines_above_embeds_nothing_but_the_pointers_move(conn, repo):
    _, e, store = run(conn, repo)
    (repo / "shop.py").write_text("\n\n\n" + FILES["shop.py"])
    seen = e.text_count
    report, _, _ = run(conn, repo, e, store)
    assert e.text_count == seen and report.embedded == 0 and report.moved > 0
    for row in chunk_store.chunks_for_file(conn, 1, "shop.py"):           # pointer truth in the database
        lines = (repo / "shop.py").read_text().split("\n")
        if not row["synthetic"]:
            assert "\n".join(lines[row["start_line"] - 1:row["end_line"]]) == row["text"]
    assert_stores_agree(conn, store, e)


def test_adding_a_function_embeds_only_the_new_one(conn, repo):
    _, e, store = run(conn, repo)
    (repo / "util.py").write_text(FILES["util.py"] + "\n\ndef another():\n    return 2\n")
    seen = e.text_count
    run(conn, repo, e, store)
    assert e.text_count - seen == 1 and "another" in e.embedded_texts[-1]
    assert_stores_agree(conn, store, e)


def test_deleting_a_function_removes_its_row_and_its_vector(conn, repo):
    _, e, store = run(conn, repo)
    before = chunk_store.all_ids(conn, 1)
    (repo / "shop.py").write_text(FILES["shop.py"].split("\n\n\ndef refund")[0] + "\n")
    report, _, _ = run(conn, repo, e, store)
    assert len(before) - len(chunk_store.all_ids(conn, 1)) >= 1 and report.deleted_chunks >= 1
    assert_stores_agree(conn, store, e)


def test_a_renamed_file_is_deleted_and_added_not_edited(conn, repo):
    _, e, store = run(conn, repo)
    old_ids = chunk_store.ids_for_file(conn, 1, "util.py")
    (repo / "util.py").rename(repo / "tools.py")
    seen = e.text_count
    run(conn, repo, e, store)
    assert chunk_store.ids_for_file(conn, 1, "util.py") == set() and "util.py" not in chunk_store.file_hashes(conn, 1)
    assert not (old_ids & store.ids())
    assert e.text_count - seen == len(chunk_store.ids_for_file(conn, 1, "tools.py")) > 0
    assert_stores_agree(conn, store, e)


def test_a_file_deleted_from_disk_is_removed_everywhere(conn, repo):
    _, e, store = run(conn, repo)
    ids = chunk_store.ids_for_file(conn, 1, "web/app.js")
    (repo / "web" / "app.js").unlink()
    report, _, _ = run(conn, repo, e, store)
    assert report.deleted_files == 1 and report.deleted_chunks == len(ids)
    assert "web/app.js" not in chunk_store.file_hashes(conn, 1) and not (ids & store.ids())
    assert_stores_agree(conn, store, e)


def test_a_file_emptied_out_keeps_its_row_and_loses_its_chunks(conn, repo):
    _, e, store = run(conn, repo)
    (repo / "util.py").write_text("")
    run(conn, repo, e, store)
    assert chunk_store.ids_for_file(conn, 1, "util.py") == set() and "util.py" in chunk_store.file_hashes(conn, 1)
    assert_stores_agree(conn, store, e)


def test_switching_to_another_model_embeds_everything_again_and_leaves_no_old_vectors(conn, repo):
    _, e8, store = run(conn, repo, FakeEmbedder(dim=8))
    first = chunk_store.all_ids(conn, 1)
    e16 = FakeEmbedder(dim=16)
    report, _, _ = run(conn, repo, e16, store)           # same store, different vector length: must not crash
    assert chunk_store.all_ids(conn, 1) == first and report.embedded == len(first) == e16.text_count
    assert_stores_agree(conn, store, e16)
    assert {len(v) for v in store._vectors.values()} == {16}


def test_a_run_that_died_before_recording_vectors_embeds_those_chunks_next_time(conn, repo):
    # rows exist (committed) but nothing says a vector exists: the crash the I-2 review worried about
    _, e, store = run(conn, repo)
    conn.execute("UPDATE chunks SET embed_model = NULL, embed_dim = NULL WHERE rel_path = 'shop.py'")
    conn.commit()
    n = len(chunk_store.ids_for_file(conn, 1, "shop.py"))
    seen = e.text_count
    report, _, _ = run(conn, repo, e, store)
    assert e.text_count - seen == n and report.embedded == n
    assert_stores_agree(conn, store, e)


# ---- order of operations ----

class SpyStore(InMemoryVectorStore):
    """Looks at SQLite at the moment each vector call happens."""

    def __init__(self, conn):
        super().__init__()
        self.conn, self.log = conn, []

    def upsert(self, ids, vectors):
        in_sqlite = chunk_store.all_ids(self.conn, 1)
        self.log.append(("upsert", sorted(ids), sorted(set(ids) & in_sqlite)))
        super().upsert(ids, vectors)

    def delete(self, ids):
        self.log.append(("delete", sorted(ids), sorted(set(ids) & chunk_store.all_ids(self.conn, 1))))
        super().delete(ids)


def test_vectors_are_written_before_their_rows_and_stale_vectors_deleted_after_their_rows_are_gone(conn, repo):
    store = SpyStore(conn)
    run(conn, repo, store=store)
    for kind, ids, rows_present in store.log:
        assert kind == "upsert" and rows_present == []               # new chunks: no row yet when the vector goes in
    store.log.clear()
    (repo / "util.py").write_text("def helper():\n    return 2\n\n\ndef extra():\n    return 3\n")
    (repo / "shop.py").write_text("import os\n")                      # drops functions: their ids become stale
    run(conn, repo, store=store)
    deletes = [entry for entry in store.log if entry[0] == "delete"]
    assert deletes and all(rows_present == [] for _, _, rows_present in deletes)   # rows are already gone when the vector goes


def test_no_database_transaction_is_open_while_the_embedder_runs(conn, repo):
    class Watching(FakeEmbedder):
        def embed_documents(self, texts, ids=None):
            assert not conn.in_transaction, "a SQLite transaction was open during an embedding call"
            return super().embed_documents(texts, ids)

    run(conn, repo, Watching())


def test_every_embedding_call_is_for_embed_text_not_text(conn, repo):
    _, e, _ = run(conn, repo)
    rows = [r for f in FILES for r in chunk_store.chunks_for_file(conn, 1, f)]
    assert sorted(e.embedded_texts) == sorted(r["embed_text"] for r in rows)


# ---- failures ----

def test_a_chunk_that_is_too_long_stops_the_run_loudly_and_leaves_that_file_untouched(conn, repo):
    (repo / "zbig.py").write_text("def big():\n    return '" + "x" * 400 + "'\n")   # sorts last, after the four good files
    e = FakeEmbedder(max_chars=300)
    store = InMemoryVectorStore()
    with pytest.raises(EmbeddingTooLong) as err:
        run(conn, repo, e, store)
    assert err.value.chunk_ids                                           # names the offending chunk
    assert "zbig.py" not in chunk_store.file_hashes(conn, 1)             # all or nothing per file
    assert set(chunk_store.file_hashes(conn, 1)) == set(FILES)           # the files before it are fully done
    assert_stores_agree(conn, store, e)                                  # and nothing is half-written anywhere


def test_after_the_problem_is_fixed_a_rerun_ends_in_the_same_state_as_a_clean_run(conn, repo):
    (repo / "zbig.py").write_text("def big():\n    return '" + "x" * 400 + "'\n")
    store = InMemoryVectorStore()
    with pytest.raises(EmbeddingTooLong):
        run(conn, repo, FakeEmbedder(max_chars=300), store)
    e = FakeEmbedder()
    run(conn, repo, e, store)                                            # the limit is gone: the re-run finishes the job

    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('clean', '/c', 'now')")
    conn.commit()
    clean_store = InMemoryVectorStore()
    index_project(conn, 2, repo, FakeEmbedder(), clean_store)            # the same repo indexed in one go, in another project

    def state(project_id):
        rows = [r for f in sorted(chunk_store.file_hashes(conn, project_id)) for r in chunk_store.chunks_for_file(conn, project_id, f)]
        return [(r["id"], r["content_hash"], r["start_line"], r["end_line"], r["embed_model"], r["embed_dim"]) for r in rows]

    assert state(1) == state(2) and chunk_store.file_hashes(conn, 1) == chunk_store.file_hashes(conn, 2)
    assert store._vectors == clean_store._vectors


def test_a_file_the_chunker_chokes_on_is_reported_and_skipped_not_fatal(conn, repo, monkeypatch):
    real = indexing.index.chunk_file

    def flaky(path, repo_root=None):
        if str(path).endswith("util.py"):
            raise RuntimeError("boom")
        return real(path, repo_root=repo_root)

    monkeypatch.setattr(indexing.index, "chunk_file", flaky)
    report, e, store = run(conn, repo)
    assert [rel for rel, _ in report.skipped] == ["util.py"] and "boom" in report.skipped[0][1]
    assert "util.py" not in chunk_store.file_hashes(conn, 1) and "shop.py" in chunk_store.file_hashes(conn, 1)


def test_progress_is_reported_once_per_file(conn, repo):
    seen = []
    run(conn, repo, progress=lambda done, total, rel: seen.append((done, total, rel)))
    assert [d for d, _, _ in seen] == [1, 2, 3, 4] and {t for _, t, _ in seen} == {4}
    assert sorted(r for _, _, r in seen) == sorted(FILES)


def test_two_projects_indexing_the_same_repo_stay_apart(conn, repo):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    conn.commit()
    e = FakeEmbedder()
    s1, s2 = InMemoryVectorStore(), InMemoryVectorStore()
    index_project(conn, 1, repo, e, s1)
    index_project(conn, 2, repo, e, s2)
    assert chunk_store.all_ids(conn, 1) == chunk_store.all_ids(conn, 2) == s1.ids() == s2.ids()
    (repo / "util.py").unlink()
    index_project(conn, 1, repo, e, s1)
    assert "util.py" in chunk_store.file_hashes(conn, 2) and s2.ids() == chunk_store.all_ids(conn, 2)


def test_it_indexes_this_whole_repo_consistently_and_a_second_run_does_nothing(conn):
    root = Path(__file__).resolve().parents[2]
    e, store = FakeEmbedder(), InMemoryVectorStore()
    first = index_project(conn, 1, root, e, store)
    assert first.files_seen > 50 and first.skipped == []
    assert_stores_agree(conn, store, e)
    seen = e.text_count
    second = index_project(conn, 1, root, e, store)
    assert e.text_count == seen and second.embedded == 0 and second.moved == 0


# ---- the vector store's signature: a switch, a lost store, an interrupted switch ----

def test_the_store_is_stamped_with_the_model_that_filled_it(conn, repo):
    _, e, store = run(conn, repo)
    assert store.signature() == (e.model_name, e.dim)


def test_a_lost_vector_store_is_rebuilt_from_the_rows_instead_of_trusted(conn, repo):
    _, e, _ = run(conn, repo)
    fresh_store = InMemoryVectorStore()                                  # the Chroma folder was deleted: rows still claim vectors
    seen = e.text_count
    report, _, _ = run(conn, repo, e, fresh_store)
    assert e.text_count - seen == fresh_store.count() == report.embedded > 0
    assert_stores_agree(conn, fresh_store, e)


def test_an_interrupted_model_switch_converges(conn, repo):
    _, e8, store = run(conn, repo, FakeEmbedder(dim=8))
    chunk_store.forget_embeddings(conn, 1)                               # the switch got as far as step 1 and died:
    assert store.signature() == (e8.model_name, 8) and store.count() > 0  # rows say "never embedded", store still holds old 8-number vectors
    e16 = FakeEmbedder(dim=16)
    run(conn, repo, e16, store)
    assert_stores_agree(conn, store, e16)
    assert {len(v) for v in store._vectors.values()} == {16}


def test_a_vector_with_no_row_is_an_orphan_and_is_cleaned_up_at_the_end_of_a_run(conn, repo):
    _, e, store = run(conn, repo)
    store.upsert(["left-over-from-a-crash"], [[1.0] * e.dim])
    report, _, _ = run(conn, repo, e, store)
    assert "left-over-from-a-crash" not in store.ids() and report.orphans_removed == 1
    assert_stores_agree(conn, store, e)


def test_a_run_that_stopped_early_does_not_touch_orphans(conn, repo):
    (repo / "zbig.py").write_text("def big():\n    return '" + "x" * 400 + "'\n")
    store = InMemoryVectorStore()
    store.set_signature("fake-hash-8", 8)
    store.upsert(["maybe-from-the-file-that-failed"], [[1.0] * 8])
    with pytest.raises(EmbeddingTooLong):
        run(conn, repo, FakeEmbedder(max_chars=300), store)
    assert "maybe-from-the-file-that-failed" in store.ids()              # only a COMPLETED run may call a vector an orphan


def test_vectors_of_deleted_functions_are_removed_even_when_a_later_file_stops_the_run(conn, repo):
    # The end-of-run sweep only happens when a run completes, so the per-file "delete stale vectors" step has to do its job on its own.
    _, e, store = run(conn, repo)
    (repo / "shop.py").write_text("import os\n")                                     # shop.py loses its functions
    (repo / "zbig.py").write_text("def big():\n    return '" + "x" * 400 + "'\n")    # and a later file stops the run
    with pytest.raises(EmbeddingTooLong):
        run(conn, repo, FakeEmbedder(max_chars=300), store)
    assert store.ids() == chunk_store.all_ids(conn, 1)


def test_end_to_end_with_the_real_chroma_store_including_reopening_it(conn, repo, tmp_path):
    from vectorstore import open_project_store
    e = FakeEmbedder()
    store = open_project_store(tmp_path / "data", 1)
    first = index_project(conn, 1, repo, e, store)
    assert first.embedded > 0 and store.ids() == chunk_store.all_ids(conn, 1)
    assert store.signature() == (e.model_name, e.dim)

    (repo / "shop.py").write_text(FILES["shop.py"].replace("x - 1", "x - 2"))
    reopened = open_project_store(tmp_path / "data", 1)                  # a new process, the same folder
    seen = e.text_count
    second = index_project(conn, 1, repo, e, reopened)
    assert e.text_count - seen == 1 and second.embedded == 1 and not second.store_rebuilt
    assert reopened.ids() == chunk_store.all_ids(conn, 1)
    top = reopened.query(e.embed_query(e.embedded_texts[-1]), 1)
    assert top[0][0] in chunk_store.ids_for_file(conn, 1, "shop.py") and top[0][1] > 0.99   # the edited chunk finds itself
