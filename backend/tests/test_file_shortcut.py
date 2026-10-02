"""Task I-4.6: skip re-chunking a file whose hash has not changed, but only when that is really safe.

Safe means ALL of: same file hash, same chunker (a new chunking rule gives the same file different chunks), and every
chunk of the file already has a vector record for the current model. Anything else and the file is processed as usual.
"""
import chunk_store
import indexing
from chunker import grouping
from embedding import FakeEmbedder
from indexing.fingerprint import chunker_fingerprint, compute_fingerprint
from test_index_project import FILES, assert_stores_agree, repo, run  # noqa: F401  (repo is a pytest fixture)
from vectorstore import InMemoryVectorStore


def count_chunk_calls(monkeypatch):
    calls = []
    real = indexing.index.chunk_file

    def spy(path, repo_root=None):
        calls.append(str(path))
        return real(path, repo_root=repo_root)

    monkeypatch.setattr(indexing.index, "chunk_file", spy)
    return calls


def test_an_unchanged_repo_is_not_chunked_or_embedded_again(conn, repo, monkeypatch):
    _, e, store = run(conn, repo)
    calls = count_chunk_calls(monkeypatch)
    seen = e.text_count
    report, _, _ = run(conn, repo, e, store)
    assert calls == [] and e.text_count == seen
    assert report.files_unchanged == report.files_seen == len(FILES) and report.files_written == 0


def test_only_the_edited_file_is_chunked_again(conn, repo, monkeypatch):
    _, e, store = run(conn, repo)
    (repo / "util.py").write_text("def helper():\n    return 99\n")
    calls = count_chunk_calls(monkeypatch)
    report, _, _ = run(conn, repo, e, store)
    assert [c.rsplit("/", 1)[-1] for c in calls] == ["util.py"]
    assert report.files_unchanged == len(FILES) - 1 and report.files_written == 1
    assert_stores_agree(conn, store, e)


def test_a_changed_chunker_makes_every_file_be_chunked_again_but_embeds_nothing(conn, repo, monkeypatch):
    _, e, store = run(conn, repo)
    seen = e.text_count
    monkeypatch.setattr(indexing.index, "chunker_fingerprint", lambda: "a-new-chunking-rule")
    calls = count_chunk_calls(monkeypatch)
    report, _, _ = run(conn, repo, e, store)
    assert len(calls) == len(FILES) and report.files_unchanged == 0   # same file bytes, but the chunker is different now
    assert e.text_count == seen                                       # the chunks came out the same, so nothing is embedded
    calls.clear()
    run(conn, repo, e, store)                                         # and the new version is remembered
    assert calls == []


def test_a_changed_chunker_that_produces_different_chunks_is_picked_up(conn, repo, monkeypatch):
    _, e, store = run(conn, repo)
    real = indexing.index.chunk_file

    def new_rule(path, repo_root=None):                               # pretend the new chunker adds a prefix to every chunk's text
        return [dict(c, embed_text="NEW " + c["embed_text"], content_hash="new-" + c["content_hash"])
                for c in real(path, repo_root=repo_root)]

    monkeypatch.setattr(indexing.index, "chunk_file", new_rule)
    monkeypatch.setattr(indexing.index, "chunker_fingerprint", lambda: "v2")
    seen = e.text_count
    report, _, _ = run(conn, repo, e, store)
    assert e.text_count - seen == store.count() and report.embedded == store.count()
    assert all(t.startswith("NEW ") for t in e.embedded_texts[seen:])


def test_a_file_with_a_chunk_that_has_no_vector_record_is_not_skipped(conn, repo, monkeypatch):
    _, e, store = run(conn, repo)
    conn.execute("UPDATE chunks SET embed_model = NULL, embed_dim = NULL WHERE rel_path = 'shop.py'")
    conn.commit()
    calls = count_chunk_calls(monkeypatch)
    seen = e.text_count
    report, _, _ = run(conn, repo, e, store)
    assert [c.rsplit("/", 1)[-1] for c in calls] == ["shop.py"]       # same hash, but unfinished: this is the half-embedded file
    assert e.text_count - seen == len(chunk_store.ids_for_file(conn, 1, "shop.py"))
    assert report.files_unchanged == len(FILES) - 1
    assert_stores_agree(conn, store, e)


def test_a_model_switch_processes_every_file(conn, repo, monkeypatch):
    run(conn, repo, FakeEmbedder(dim=8))
    calls = count_chunk_calls(monkeypatch)
    e16 = FakeEmbedder(dim=16)
    report, _, store = run(conn, repo, e16, InMemoryVectorStore())     # a new store for a new model
    assert len(calls) == len(FILES) and report.files_unchanged == 0
    assert_stores_agree(conn, store, e16)


def test_a_file_that_was_skipped_because_of_a_chunker_error_is_tried_again(conn, repo, monkeypatch):
    real = indexing.index.chunk_file
    flaky = {"on": True}

    def maybe_fail(path, repo_root=None):
        if flaky["on"] and str(path).endswith("util.py"):
            raise RuntimeError("boom")
        return real(path, repo_root=repo_root)

    monkeypatch.setattr(indexing.index, "chunk_file", maybe_fail)
    first, e, store = run(conn, repo)
    assert [r for r, _ in first.skipped] == ["util.py"]
    flaky["on"] = False
    second, _, _ = run(conn, repo, e, store)
    assert second.skipped == [] and "util.py" in chunk_store.file_hashes(conn, 1)
    assert_stores_agree(conn, store, e)


def test_files_saved_without_a_chunker_version_are_never_skipped(conn, repo, monkeypatch):
    _, e, store = run(conn, repo)
    conn.execute("UPDATE files SET chunker_version = NULL")
    conn.commit()
    calls = count_chunk_calls(monkeypatch)
    run(conn, repo, e, store)
    assert len(calls) == len(FILES)                                   # unknown version: do the work, never guess


# ---- the fingerprint ----

def test_fingerprint_is_stable_and_has_a_fixed_shape():
    a = chunker_fingerprint()
    assert a == chunker_fingerprint() and len(a) == 16 and int(a, 16) >= 0


def test_fingerprint_changes_when_the_chunking_code_changes(tmp_path):
    (tmp_path / "a.py").write_text("RULE = 1\n")
    (tmp_path / "b.py").write_text("OTHER = 1\n")
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    base = compute_fingerprint(paths, {"tree-sitter": "0.26"}, True)
    assert base == compute_fingerprint(list(reversed(paths)), {"tree-sitter": "0.26"}, True)   # the order of files does not matter
    (tmp_path / "a.py").write_text("RULE = 2\n")
    assert compute_fingerprint(paths, {"tree-sitter": "0.26"}, True) != base


def test_fingerprint_changes_with_the_grammar_versions_and_the_grouping_switch(tmp_path):
    (tmp_path / "a.py").write_text("RULE = 1\n")
    paths = [tmp_path / "a.py"]
    base = compute_fingerprint(paths, {"tree-sitter": "0.26"}, True)
    assert compute_fingerprint(paths, {"tree-sitter": "0.27"}, True) != base
    assert compute_fingerprint(paths, {"tree-sitter": "0.26"}, False) != base


def test_the_real_fingerprint_follows_the_grouping_switch(monkeypatch):
    monkeypatch.setattr(grouping, "GROUP_SMALL_CHUNKS", True)
    on = chunker_fingerprint()
    monkeypatch.setattr(grouping, "GROUP_SMALL_CHUNKS", False)
    assert chunker_fingerprint() != on


def test_the_real_fingerprint_covers_the_chunker_package_and_the_language_rules():
    from indexing.fingerprint import chunker_source_files
    names = {p.name for p in chunker_source_files()}
    assert {"code.py", "core.py", "grouping.py", "markdown.py", "splitting.py", "__init__.py", "languages.py"} <= names


# ---- a damaged install must not stop indexing (fix #2 from the I-4 review) ----

def test_broken_package_metadata_is_skipped_not_fatal(monkeypatch):
    from indexing import fingerprint

    class Good:
        metadata = {"Name": "tree-sitter-x"}
        version = "1.0"

    class NoName:                                   # a half-uninstalled package folder
        metadata = {"Name": None}
        version = "9"

    class BadVersion:
        metadata = {"Name": "tree-sitter-y"}

        @property
        def version(self):
            raise RuntimeError("corrupt METADATA")

    class NoMetadata:
        @property
        def metadata(self):
            raise OSError("unreadable")

    monkeypatch.setattr(fingerprint.metadata, "distributions", lambda: [NoName(), Good(), BadVersion(), NoMetadata()])
    assert fingerprint.grammar_versions() == {"tree-sitter-x": "1.0"}
    assert len(fingerprint.chunker_fingerprint()) == 16
