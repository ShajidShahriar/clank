"""Task I-4.1: a chunk row records which model embedded it, in the same transaction as the row.

Why: "same content_hash" alone must never mean "done". A row saved without a vector record has to show up
as still needing one, or a half-finished file is skipped forever (the silent drop, again).
"""
import sqlite3

import pytest

import chunk_store
from chunker import chunk_file

MODEL, DIM = "m1", 1024


def make_chunks(tmp_path, name="a.py", n=4):
    src = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(n))
    (tmp_path / name).write_text(src)
    return chunk_file(str(tmp_path / name), repo_root=str(tmp_path))


@pytest.fixture
def chunks(tmp_path):
    out = make_chunks(tmp_path)
    assert len(out) == 4
    return out


def save(conn, chunks, rel_path="a.py", project_id=1, embedded=None, file_hash="h"):
    return chunk_store.save_file_chunks(conn, project_id, rel_path, file_hash, chunks, embedded=embedded)


def records(conn, project_id=1):
    rows = conn.execute("SELECT id, embed_model, embed_dim FROM chunks WHERE project_id = ?", (project_id,))
    return {r["id"]: (r["embed_model"], r["embed_dim"]) for r in rows}


def test_embedded_records_are_saved_with_the_rows(conn, chunks):
    save(conn, chunks, embedded={c["id"]: (MODEL, DIM) for c in chunks})
    assert records(conn) == {c["id"]: (MODEL, DIM) for c in chunks}
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == []


def test_only_the_chunks_named_get_a_record(conn, chunks):
    done = chunks[:2]
    save(conn, chunks, embedded={c["id"]: (MODEL, DIM) for c in done})
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == [c["id"] for c in chunks[2:]]


def test_rows_saved_without_a_record_still_need_embedding(conn, chunks):
    # The crash scenario: rows committed, nothing records that a vector exists. They must not look finished.
    save(conn, chunks)
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == [c["id"] for c in chunks]


def test_a_different_model_or_dimension_means_embed_again(conn, chunks):
    save(conn, chunks, embedded={c["id"]: (MODEL, DIM) for c in chunks})
    everything = [c["id"] for c in chunks]
    assert chunk_store.needs_embedding(conn, 1, "other-model", DIM) == everything
    assert chunk_store.needs_embedding(conn, 1, MODEL, 768) == everything
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == []


def test_resaving_unchanged_chunks_without_a_record_keeps_theirs(conn, chunks):
    save(conn, chunks, embedded={c["id"]: (MODEL, DIM) for c in chunks})
    save(conn, chunks)  # nothing changed, nothing re-embedded
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == []


def test_a_changed_chunk_needs_embedding_until_a_new_record_is_given(conn, chunks):
    save(conn, chunks, embedded={c["id"]: (MODEL, DIM) for c in chunks})
    edited = [dict(c) for c in chunks]
    edited[1]["content_hash"] = "changed"
    save(conn, edited)
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == [chunks[1]["id"]]
    save(conn, edited, embedded={chunks[1]["id"]: (MODEL, DIM)})
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == []


def test_needs_embedding_is_per_project_and_in_file_and_line_order(conn, tmp_path):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    conn.commit()
    b, a = make_chunks(tmp_path, "b.py", 2), make_chunks(tmp_path, "a.py", 2)
    save(conn, b, rel_path="b.py")
    save(conn, a, rel_path="a.py")
    save(conn, a, rel_path="a.py", project_id=2)
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == [c["id"] for c in a + b]  # a.py first, by line
    assert chunk_store.needs_embedding(conn, 2, MODEL, DIM) == [c["id"] for c in a]
    assert chunk_store.needs_embedding(conn, 3, MODEL, DIM) == []


def test_an_empty_store_needs_nothing(conn):
    assert chunk_store.needs_embedding(conn, 1, MODEL, DIM) == []


@pytest.mark.parametrize("bad", [
    {"not-a-chunk-id": (MODEL, DIM)},   # an id that is not being saved
    "use the real chunk id",            # replaced below with a record that has an empty model
    "zero dimension",
    "model is not a string",
])
def test_bad_records_are_refused_and_nothing_is_written(conn, chunks, bad):
    first = chunks[0]["id"]
    if bad == "use the real chunk id":
        bad = {first: ("", DIM)}
    elif bad == "zero dimension":
        bad = {first: (MODEL, 0)}
    elif bad == "model is not a string":
        bad = {first: (None, DIM)}
    with pytest.raises(ValueError):
        save(conn, chunks, embedded=bad)
    assert chunk_store.all_ids(conn, 1) == set()
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


def test_a_failed_save_does_not_record_embeddings_either(conn, chunks):
    save(conn, chunks)
    broken = [dict(c) for c in chunks]
    broken[-1]["end_line"] = 0  # the database refuses this row after earlier ones went in
    with pytest.raises(sqlite3.IntegrityError):
        save(conn, broken, embedded={c["id"]: (MODEL, DIM) for c in chunks}, file_hash="h2")
    assert records(conn) == {c["id"]: (None, None) for c in chunks}
