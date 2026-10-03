"""Task I-5.2: what index_project does when something goes wrong. The policy (decided with the user, 2026-10-03):

- A file with a chunk that is too long is SKIPPED: nothing of it is written, the reason (with the chunk ids) is stored on its file row and returned in
  the report, and the run goes on. If the skips pass max(3, 5% of the files), the run stops: something systematic is wrong.
- Ollama not reachable, model missing, or a garbage answer STOP the run: every file would fail the same way. `warmup()` runs before any file is
  touched, and nothing at all is changed if it fails. The report says "stopped at X, N done". No exception: the caller gets the report.
- A skipped file's OLD chunks (if it was indexed before) stay in the database, but the file is flagged, and retrieval hides flagged files (I-6).
"""
import pytest

import chunk_store
from embedding import BadResponse, EmbeddingError, FakeEmbedder, ModelNotFound, OllamaUnavailable
from failing_embedders import Raising, mentions
from indexing import index_project
from test_index_project import assert_stores_agree
from vectorstore import InMemoryVectorStore

BIG = "def big():\n    return '" + "x" * 400 + "'\n"       # one chunk over FakeEmbedder(max_chars=300)


def make_repo(tmp_path, n=5, bad=()):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    for i in range(n):
        (root / f"f{i:03d}.py").write_text(BIG if i in bad else f"def fn{i}():\n    return {i}\n")
    return root


def run(conn, root, embedder=None, store=None, **kw):
    embedder = embedder or FakeEmbedder(max_chars=300)
    store = store if store is not None else InMemoryVectorStore()
    return index_project(conn, 1, root, embedder, store, **kw), embedder, store


# ---- a chunk that is too long: skip the file, say why, carry on ----

def test_a_new_file_that_is_too_long_is_skipped_with_the_chunk_ids_in_the_reason(conn, tmp_path):
    root = make_repo(tmp_path, bad={2})
    report, e, store = run(conn, root)
    assert report.stopped is None and [rel for rel, _ in report.skipped] == ["f002.py"]
    reason = report.skipped[0][1]
    assert "EmbeddingTooLong" in reason and all(i in reason for i in report.skipped_chunk_ids["f002.py"]) and report.skipped_chunk_ids["f002.py"]
    assert chunk_store.failed_files(conn, 1) == {"f002.py": reason}               # stored on the file row, not only in the report
    assert chunk_store.ids_for_file(conn, 1, "f002.py") == set()                  # nothing of it was written
    assert {r for r in chunk_store.file_hashes(conn, 1)} == {f"f00{i}.py" for i in range(5)}   # but the file is known, with its reason
    assert_stores_agree(conn, store, e)


def test_a_file_that_was_fine_and_became_too_long_keeps_its_old_rows_but_is_flagged(conn, tmp_path):
    root = make_repo(tmp_path)
    _, e, store = run(conn, root)
    before = chunk_store.chunks_for_file(conn, 1, "f001.py")
    (root / "f001.py").write_text(BIG)
    report, _, _ = run(conn, root, e, store)
    assert [rel for rel, _ in report.skipped] == ["f001.py"]
    assert chunk_store.chunks_for_file(conn, 1, "f001.py") == before              # untouched, vectors included
    assert set(chunk_store.failed_files(conn, 1)) == {"f001.py"}
    assert_stores_agree(conn, store, e)


def test_fixing_the_file_clears_the_flag(conn, tmp_path):
    root = make_repo(tmp_path, bad={1})
    _, e, store = run(conn, root)
    assert set(chunk_store.failed_files(conn, 1)) == {"f001.py"}
    (root / "f001.py").write_text("def fixed():\n    return 1\n")
    report, _, _ = run(conn, root, e, store)
    assert report.skipped == [] and chunk_store.failed_files(conn, 1) == {}
    assert chunk_store.ids_for_file(conn, 1, "f001.py")
    assert_stores_agree(conn, store, e)


def test_putting_the_old_content_back_also_clears_the_flag(conn, tmp_path):
    # same bytes and same chunker as the last GOOD version: the shortcut must not skip a flagged file, or the flag would stay forever
    root = make_repo(tmp_path)
    _, e, store = run(conn, root)
    good = (root / "f001.py").read_text()
    (root / "f001.py").write_text(BIG)
    run(conn, root, e, store)
    assert set(chunk_store.failed_files(conn, 1)) == {"f001.py"}
    (root / "f001.py").write_text(good)
    run(conn, root, e, store)
    assert chunk_store.failed_files(conn, 1) == {}


def test_a_flagged_file_is_tried_again_on_every_run(conn, tmp_path):
    root = make_repo(tmp_path, bad={1})
    e = Raising(max_chars=300)
    _, _, store = run(conn, root, e)
    e.events.clear()
    report, _, _ = run(conn, root, e, store)
    assert e.events == [("warmup",), ("embed", 1)]                                  # one attempt, for the flagged file only
    assert [rel for rel, _ in report.skipped] == ["f001.py"] and report.files_unchanged == 4   # the four good files were skipped by the shortcut


def test_a_file_the_chunker_chokes_on_is_flagged_too(conn, tmp_path, monkeypatch):
    import indexing
    root = make_repo(tmp_path)
    real = indexing.index.chunk_file

    def choke_on_f003(path, repo_root=None):
        if str(path).endswith("f003.py"):
            raise RuntimeError("boom")
        return real(path, repo_root=repo_root)

    monkeypatch.setattr(indexing.index, "chunk_file", choke_on_f003)
    report, e, store = run(conn, root)
    assert [rel for rel, _ in report.skipped] == ["f003.py"]
    assert "RuntimeError: boom" in chunk_store.failed_files(conn, 1)["f003.py"]
    assert_stores_agree(conn, store, e)


# ---- too many skips: something systematic is wrong ----

@pytest.mark.parametrize("files,limit", [(40, 3), (200, 10)])
def test_the_run_goes_on_up_to_the_limit_and_stops_just_past_it(conn, tmp_path, files, limit):
    # limit = max(3, 5% of the files)
    root = make_repo(tmp_path, n=files, bad=set(range(limit)))
    report, e, store = run(conn, root)
    assert len(report.skipped) == limit and report.stopped is None
    assert_stores_agree(conn, store, e)


@pytest.mark.parametrize("files,limit", [(40, 3), (200, 10)])
def test_one_skip_past_the_limit_stops_the_run(conn, tmp_path, files, limit):
    root = make_repo(tmp_path, n=files, bad=set(range(2, 2 * (limit + 1) + 1, 2)))   # every other file from f002 on, so good files come first
    report, e, store = run(conn, root)
    assert report.stopped and report.stopped.kind == "too_many_skips"
    assert len(report.skipped) == limit + 1 and report.stopped.at == report.skipped[-1][0]
    assert f"{limit + 1} files skipped" in report.stopped.reason and str(limit) in report.stopped.reason
    assert report.stopped.files_done < files                                       # the rest were not touched
    assert_stores_agree(conn, store, e)                                            # what was done is consistent


def test_a_stopped_run_does_not_sweep_or_delete_but_the_next_complete_one_does(conn, tmp_path):
    root = make_repo(tmp_path, n=8)
    _, e, store = run(conn, root, FakeEmbedder(), InMemoryVectorStore())            # a clean first run to have something to clean up after
    (root / "f007.py").unlink()
    store.upsert(["stray"], [[1.0] * e.dim])
    for i in range(4):
        (root / f"f{i:03d}.py").write_text(BIG)
    report, _, _ = run(conn, root, FakeEmbedder(max_chars=300), store)
    assert report.stopped and report.stopped.kind == "too_many_skips"
    assert "stray" in store.ids() and "f007.py" in chunk_store.file_hashes(conn, 1)  # left for a run that completes
    for i in range(4):
        (root / f"f{i:03d}.py").write_text(f"def fn{i}():\n    return {i}\n")
    done, _, _ = run(conn, root, FakeEmbedder(max_chars=300), store)
    assert done.stopped is None and "stray" not in store.ids() and "f007.py" not in chunk_store.file_hashes(conn, 1)


# ---- Ollama problems stop the run ----

@pytest.mark.parametrize("error", [OllamaUnavailable("Ollama at http://x did not answer"), ModelNotFound("run: ollama pull m"),
                                    BadResponse("not valid JSON"), EmbeddingError("Ollama answered 400: odd")],
                         ids=lambda e: type(e).__name__)
def test_these_errors_stop_the_run_and_the_report_says_where(conn, tmp_path, error):
    root = make_repo(tmp_path, n=6)
    e = Raising(error, when=mentions("fn3"))
    store = InMemoryVectorStore()
    report, _, _ = run(conn, root, e, store)                                       # returns, does not raise
    assert report.stopped and report.stopped.kind == "embedder" and report.stopped.at == "f003.py" and report.stopped.files_done == 3
    assert type(error).__name__ in report.stopped.reason and str(error) in report.stopped.reason
    assert set(chunk_store.file_hashes(conn, 1)) == {"f000.py", "f001.py", "f002.py"}   # finished files stay finished; f003 onwards untouched
    assert_stores_agree(conn, store, e)
    assert report.skipped == [] and chunk_store.failed_files(conn, 1) == {}        # a stop is not a per-file failure: nothing is flagged


def test_after_the_embedder_is_back_a_rerun_ends_in_the_same_state_as_a_clean_run(conn, tmp_path):
    root = make_repo(tmp_path, n=6)
    store = InMemoryVectorStore()
    first, _, _ = run(conn, root, Raising(OllamaUnavailable("down"), when=mentions("fn3")), store)
    assert first.stopped
    e = FakeEmbedder(max_chars=300)
    second, _, _ = run(conn, root, e, store)
    assert second.stopped is None and second.files_unchanged == 3
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('clean', '/c', 'now')")
    conn.commit()
    clean = InMemoryVectorStore()
    index_project(conn, 2, root, FakeEmbedder(max_chars=300), clean)

    def state(pid):
        return [(r["id"], r["content_hash"], r["embed_model"]) for f in sorted(chunk_store.file_hashes(conn, pid)) for r in chunk_store.chunks_for_file(conn, pid, f)]
    assert state(1) == state(2) and store._vectors == clean._vectors


def test_a_stopped_run_does_not_sweep_orphans_or_delete_vanished_files(conn, tmp_path):
    root = make_repo(tmp_path, n=6)
    _, e, store = run(conn, root)
    (root / "f005.py").unlink()
    store.upsert(["stray"], [[1.0] * e.dim])
    (root / "f001.py").write_text("def changed():\n    return 'changed'\n")
    report, _, _ = run(conn, root, Raising(OllamaUnavailable("down"), when=mentions("changed")), store)
    assert report.stopped and "stray" in store.ids() and "f005.py" in chunk_store.file_hashes(conn, 1)


# ---- warmup ----

def test_warmup_runs_once_before_anything_is_embedded(conn, tmp_path):
    root = make_repo(tmp_path)
    e = Raising()
    run(conn, root, e)
    assert e.events[0] == ("warmup",) and [x for x in e.events if x == ("warmup",)] == [("warmup",)]
    assert len(e.events) > 1


@pytest.mark.parametrize("error", [OllamaUnavailable("down"), ModelNotFound("run: ollama pull m")], ids=lambda e: type(e).__name__)
def test_a_failing_warmup_stops_before_anything_is_changed(conn, tmp_path, error):
    root = make_repo(tmp_path)
    e8 = FakeEmbedder(dim=8)
    store = InMemoryVectorStore()
    run(conn, root, e8, store)
    rows_before = {f: chunk_store.chunks_for_file(conn, 1, f) for f in chunk_store.file_hashes(conn, 1)}
    vectors_before, signature_before = dict(store._vectors), store.signature()
    report, _, _ = run(conn, root, Raising(error, on_warmup=True, dim=16), store)      # a DIFFERENT model whose server is down
    assert report.stopped and report.stopped.at is None and report.stopped.files_done == 0 and report.stopped.kind == "embedder"
    assert store._vectors == vectors_before and store.signature() == signature_before     # the store was not cleared for a model we cannot reach
    assert {f: chunk_store.chunks_for_file(conn, 1, f) for f in chunk_store.file_hashes(conn, 1)} == rows_before


def test_a_failing_warmup_on_a_first_run_writes_nothing(conn, tmp_path):
    root = make_repo(tmp_path)
    store = InMemoryVectorStore()
    report, _, _ = run(conn, root, Raising(OllamaUnavailable("down"), on_warmup=True), store)
    assert report.stopped.at is None and chunk_store.file_hashes(conn, 1) == {} and store.count() == 0 and store.signature() is None


# ---- the report in words ----

def test_describe_says_where_it_stopped_and_how_many_were_done(conn, tmp_path):
    root = make_repo(tmp_path, n=6)
    report, _, _ = run(conn, root, Raising(OllamaUnavailable("Ollama at http://x did not answer"), when=mentions("fn3")))
    text = report.describe()
    assert text.startswith("stopped at f003.py after 3 of 6 files") and "OllamaUnavailable" in text and "did not answer" in text


def test_describe_for_a_run_that_could_not_start(conn, tmp_path):
    report, _, _ = run(conn, make_repo(tmp_path), Raising(OllamaUnavailable("down"), on_warmup=True))
    assert report.describe().startswith("stopped before the first file")


def test_describe_for_a_complete_run_lists_the_skipped_files(conn, tmp_path):
    report, _, _ = run(conn, make_repo(tmp_path, bad={1}))
    text = report.describe()
    assert text.startswith("done: 5 files") and "1 skipped" in text and "f001.py" in text


# ---- storage details ----

def test_the_file_row_keeps_the_reason_and_a_save_clears_it(conn):
    chunk_store.mark_file_failed(conn, 1, "a.py", "EmbeddingTooLong: c1", file_hash="h1", chunker_version="v1")
    assert chunk_store.failed_files(conn, 1) == {"a.py": "EmbeddingTooLong: c1"}
    assert chunk_store.file_hashes(conn, 1) == {"a.py": "h1"}                         # a never-seen file gets a row to hold the reason
    chunk_store.save_file_chunks(conn, 1, "a.py", "h2", [])
    assert chunk_store.failed_files(conn, 1) == {}


def test_marking_an_existing_file_keeps_its_hash_and_chunks(conn):
    chunk_store.save_file_chunks(conn, 1, "a.py", "good-hash", [], chunker_version="v-good")
    chunk_store.mark_file_failed(conn, 1, "a.py", "reason", file_hash="new-bad-hash", chunker_version="v-new")
    assert chunk_store.file_hashes(conn, 1) == {"a.py": "good-hash"}                  # the hash still describes the stored chunks
    assert chunk_store.file_states(conn, 1) == {"a.py": ("good-hash", "v-good")}
    assert chunk_store.failed_files(conn, 1) == {"a.py": "reason"}


def test_failed_files_are_per_project(conn):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    conn.commit()
    chunk_store.mark_file_failed(conn, 1, "a.py", "r", file_hash="h", chunker_version="v")
    assert chunk_store.failed_files(conn, 2) == {}


# ---- permanently bad files must not block the healthy ones forever (I-5 review, 2026-10-03) ----
# A flagged file is tried again on every run (correct), but it used to count toward the skip limit every time, and it sorted among the
# healthy files. A few permanently bad files early in the alphabet then stopped EVERY run before it reached the healthy majority.
# Now: files that failed last time are tried LAST, and only NEW failures count toward the limit.

def test_permanently_bad_files_stop_the_first_run_but_never_block_the_healthy_ones_again(conn, tmp_path):
    root = make_repo(tmp_path, n=40, bad={0, 1, 2, 3})                            # four oversized files, sorted FIRST
    first, e, store = run(conn, root)
    # On first contact nothing is flagged yet, so these are 4 NEW failures (limit 3): the run stops, honestly, before any healthy file.
    assert first.stopped and first.stopped.kind == "too_many_skips" and first.stopped.files_done == 4
    assert set(chunk_store.failed_files(conn, 1)) == {f"f00{i}.py" for i in range(4)}
    assert "run again" in first.stopped.reason

    second, _, _ = run(conn, root, e, store)                                      # the four are known now: tried last, and not counted
    assert second.stopped is None and second.new_skips == 0
    assert len(second.skipped) == 4                                               # still reported, still flagged, still retried
    assert len(chunk_store.file_hashes(conn, 1)) == 40 and len(chunk_store.all_ids(conn, 1)) == 36
    assert_stores_agree(conn, store, e)

    third, _, _ = run(conn, root, e, store)                                       # and it is stable: healthy files are skipped by the shortcut
    assert third.stopped is None and third.files_unchanged == 36 and third.embedded == 0 and len(third.skipped) == 4


def test_files_that_failed_last_time_are_tried_after_all_the_others(conn, tmp_path):
    root = make_repo(tmp_path, n=10, bad={0, 1})
    _, e, store = run(conn, root)
    order = []
    run(conn, root, e, store, progress=lambda done, total, rel: order.append(rel))
    assert order[-2:] == ["f000.py", "f001.py"] and order[:-2] == sorted(order[:-2])


def test_only_new_failures_count_toward_the_limit(conn, tmp_path):
    root = make_repo(tmp_path, n=40, bad={0, 1, 2})                               # 3 failures: at the limit, not past it
    first, e, store = run(conn, root)
    assert first.stopped is None and first.new_skips == 3
    for i in (10, 11, 12):
        (root / f"f{i:03d}.py").write_text(BIG)                                   # 3 NEW failures on top of the 3 known ones
    second, _, _ = run(conn, root, e, store)
    assert len(second.skipped) == 6 and second.new_skips == 3 and second.stopped is None
    for i in (20, 21, 22, 23):
        (root / f"f{i:03d}.py").write_text(BIG)                                   # 4 new ones at once: past the limit
    third, _, _ = run(conn, root, e, store)
    assert third.new_skips == 4 and third.stopped and third.stopped.kind == "too_many_skips"


def test_a_limit_tripped_on_the_very_last_file_still_finishes_the_run_and_does_the_cleanup(conn, tmp_path):
    root = make_repo(tmp_path, n=9)
    _, e, store = run(conn, root, FakeEmbedder(), InMemoryVectorStore())          # a clean first state
    (root / "f000.py").unlink()                                                   # a vanished file, to be cleaned up
    store.upsert(["stray"], [[1.0] * e.dim])                                      # an orphan vector, to be swept
    for i in (5, 6, 7, 8):
        (root / f"f{i:03d}.py").write_text(BIG)                                   # four new failures, the last four files
    report, _, _ = run(conn, root, FakeEmbedder(max_chars=300), store)
    assert report.new_skips == 4 and report.skip_limit_exceeded                   # the signal is kept ...
    assert report.stopped is None                                                 # ... but nothing was left to skip, so the run is complete
    assert "f000.py" not in chunk_store.file_hashes(conn, 1) and "stray" not in store.ids()
    assert "more than the limit" in report.describe() and report.describe().startswith("done:")


def test_a_limit_tripped_with_files_still_to_come_does_stop_and_skips_the_cleanup(conn, tmp_path):
    root = make_repo(tmp_path, n=12)
    _, e, store = run(conn, root, FakeEmbedder(), InMemoryVectorStore())
    (root / "f011.py").unlink()
    for i in (1, 2, 3, 4):
        (root / f"f{i:03d}.py").write_text(BIG)
    report, _, _ = run(conn, root, FakeEmbedder(max_chars=300), store)
    assert report.stopped and report.stopped.kind == "too_many_skips" and report.stopped.files_done < report.files_seen
    assert "f011.py" in chunk_store.file_hashes(conn, 1)                          # not cleaned up: the next complete run does that
