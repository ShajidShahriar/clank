"""The chunk store (task I-2c): save, read back, siblings, ids, delete. SQLite is the truth."""
import sqlite3

import pytest

import chunk_store
from chunker import chunk_file


def source_with_split_function(tmp_path):
    """imports, one function long enough to split into parts, one small function."""
    body = "\n".join(f"    x{i} = compute({i}) + other_function_name({i})" for i in range(120))
    path = tmp_path / "big.py"
    path.write_text("import os\n\ndef big():\n" + body + "\n\ndef small():\n    return 1\n")
    return path


@pytest.fixture
def chunks(tmp_path):
    out = chunk_file(str(source_with_split_function(tmp_path)), repo_root=str(tmp_path))
    assert [c["part_count"] for c in out if c["symbol"] == "big"] == [3, 3, 3]  # the setup really splits
    return out


def save(conn, chunks, rel_path="big.py", project_id=1, file_hash="h1"):
    return chunk_store.save_file_chunks(conn, project_id, rel_path, file_hash, chunks)


def test_chunk_goes_in_and_comes_out_equal(conn, chunks):
    save(conn, chunks)
    back = chunk_store.get_chunks(conn, 1, [c["id"] for c in chunks])
    for original, stored in zip(chunks, back):
        extra = {"embed_model", "embed_dim"}
        assert {k: v for k, v in stored.items() if k not in extra} == original
        assert stored["embed_model"] is None and stored["embed_dim"] is None


def test_types_survive_the_round_trip(conn, chunks):
    save(conn, chunks)
    back = {c["symbol"]: c for c in chunk_store.get_chunks(conn, 1, [c["id"] for c in chunks])}
    assert back["big"]["names"] == ["big"] and isinstance(back["big"]["names"], list)
    assert back["small"]["synthetic"] is False and back["small"]["parse_error"] is False
    assert back["small"]["part"] is None and back["small"]["parent"] is None


def test_get_chunks_keeps_the_order_asked_for(conn, chunks):
    save(conn, chunks)
    ids = [c["id"] for c in chunks][::-1]
    assert [c["id"] for c in chunk_store.get_chunks(conn, 1, ids)] == ids


def test_get_chunks_skips_unknown_ids(conn, chunks):
    save(conn, chunks)  # search relies on this: an orphan vector's id has no row, so it just drops out
    ids = ["nope", chunks[0]["id"], "also-nope"]
    assert [c["id"] for c in chunk_store.get_chunks(conn, 1, ids)] == [chunks[0]["id"]]
    assert chunk_store.get_chunks(conn, 1, []) == []


def test_siblings_are_all_parts_in_part_order(conn, chunks):
    save(conn, chunks)
    parts = [c for c in chunks if c["symbol"] == "big"]
    middle = parts[1]["id"]
    got = chunk_store.get_siblings(conn, 1, middle)
    assert [c["part"] for c in got] == [0, 1, 2]
    assert [c["id"] for c in got] == [p["id"] for p in parts]


def test_siblings_of_a_whole_chunk_is_just_itself(conn, chunks):
    save(conn, chunks)
    small = next(c for c in chunks if c["symbol"] == "small")
    assert [c["id"] for c in chunk_store.get_siblings(conn, 1, small["id"])] == [small["id"]]


def test_siblings_of_unknown_id_is_empty(conn, chunks):
    save(conn, chunks)
    assert chunk_store.get_siblings(conn, 1, "nope") == []


def test_siblings_do_not_mix_same_named_functions(conn, tmp_path):
    path = tmp_path / "dup.py"
    path.write_text("def f():\n    return 1\n\ndef f():\n    return 2\n")
    two = chunk_file(str(path), repo_root=str(tmp_path))
    assert len(two) == 2 and two[0]["symbol"] == two[1]["symbol"] == "f"
    save(conn, two, rel_path="dup.py")
    assert [c["id"] for c in chunk_store.get_siblings(conn, 1, two[0]["id"])] == [two[0]["id"]]


def test_ids_for_file_and_all_ids(conn, chunks, tmp_path):
    other = tmp_path / "other.py"
    other.write_text("def g():\n    return 1\n")
    other_chunks = chunk_file(str(other), repo_root=str(tmp_path))
    save(conn, chunks)
    save(conn, other_chunks, rel_path="other.py")
    assert chunk_store.ids_for_file(conn, 1, "big.py") == {c["id"] for c in chunks}
    assert chunk_store.ids_for_file(conn, 1, "other.py") == {c["id"] for c in other_chunks}
    assert chunk_store.ids_for_file(conn, 1, "missing.py") == set()
    assert chunk_store.all_ids(conn, 1) == {c["id"] for c in chunks + other_chunks}


def test_two_projects_can_hold_the_same_file_path(conn, chunks):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    conn.commit()
    save(conn, chunks, project_id=1)
    save(conn, chunks, project_id=2)
    assert chunk_store.all_ids(conn, 1) == chunk_store.all_ids(conn, 2)
    chunk_store.delete_file(conn, 1, "big.py")
    assert chunk_store.all_ids(conn, 1) == set()
    assert len(chunk_store.all_ids(conn, 2)) == len(chunks)


def test_saving_again_replaces_the_files_chunks(conn, chunks):
    save(conn, chunks)
    keep = [c for c in chunks if c["symbol"] != "small"]
    save(conn, keep, file_hash="h2")
    assert chunk_store.ids_for_file(conn, 1, "big.py") == {c["id"] for c in keep}
    assert conn.execute("SELECT hash FROM files WHERE rel_path = 'big.py'").fetchone()["hash"] == "h2"


def test_resaving_unchanged_chunk_keeps_its_embedding_record(conn, chunks):
    save(conn, chunks)
    conn.execute("UPDATE chunks SET embed_model = 'm', embed_dim = 4")
    conn.commit()
    save(conn, chunks)
    rows = chunk_store.get_chunks(conn, 1, [c["id"] for c in chunks])
    assert {(r["embed_model"], r["embed_dim"]) for r in rows} == {("m", 4)}


def test_changed_chunk_loses_its_embedding_record(conn, chunks):
    save(conn, chunks)
    conn.execute("UPDATE chunks SET embed_model = 'm', embed_dim = 4")
    conn.commit()
    edited = [dict(c) for c in chunks]
    small = next(c for c in edited if c["symbol"] == "small")
    small["content_hash"] = "new-hash"
    small["text"] = small["text"] + "\n"
    save(conn, edited)
    rows = {r["symbol"]: r for r in chunk_store.get_chunks(conn, 1, [c["id"] for c in chunks])}
    assert rows["small"]["embed_model"] is None and rows["small"]["embed_dim"] is None  # vector is stale
    assert rows["big"]["embed_model"] == "m"  # untouched chunks keep theirs


def test_failed_save_changes_nothing(conn, chunks):
    save(conn, chunks)
    before = chunk_store.get_chunks(conn, 1, sorted(chunk_store.all_ids(conn, 1)))
    broken = [dict(c) for c in chunks]
    broken[-1]["end_line"] = 0  # the database refuses this row, after the earlier ones went in
    with pytest.raises(sqlite3.IntegrityError):
        save(conn, broken, file_hash="h2")
    assert chunk_store.get_chunks(conn, 1, sorted(chunk_store.all_ids(conn, 1))) == before
    assert conn.execute("SELECT hash FROM files WHERE rel_path = 'big.py'").fetchone()["hash"] == "h1"


def test_delete_file_removes_file_and_chunks(conn, chunks):
    save(conn, chunks)
    chunk_store.delete_file(conn, 1, "big.py")
    assert chunk_store.ids_for_file(conn, 1, "big.py") == set()
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    chunk_store.delete_file(conn, 1, "big.py")  # deleting twice is fine


def test_two_whole_chunks_with_the_same_key_are_not_siblings(conn, chunks):
    # Found by test_chunk_store_roundtrip on db.py: two `group` chunks in one file share kind, symbol (None),
    # parent (None) and ordinal (0). Their ids differ, but they are not parts of one chunk.
    small = next(c for c in chunks if c["symbol"] == "small")
    twin = dict(small, id="twin-id", start_line=200, end_line=201)
    save(conn, [c for c in chunks if c["symbol"] != "small"] + [small, twin])
    assert [c["id"] for c in chunk_store.get_siblings(conn, 1, small["id"])] == [small["id"]]
    assert [c["id"] for c in chunk_store.get_siblings(conn, 1, "twin-id")] == ["twin-id"]


# ---- hardening, from the I-2 review ----

def other_connection(conn, row_factory=sqlite3.Row):
    """A second connection to the same file, the way a background job would open one: foreign keys OFF."""
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    c = sqlite3.connect(path)
    c.row_factory = row_factory
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 0  # SQLite's default; the trap is real
    return c


def test_delete_file_removes_chunks_even_with_foreign_keys_off(conn, chunks):
    save(conn, chunks)
    c = other_connection(conn)
    chunk_store.delete_file(c, 1, "big.py")
    assert c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    c.close()


def test_store_works_on_a_connection_without_row_factory(conn, chunks):
    save(conn, chunks)
    c = other_connection(conn, row_factory=None)
    ids = [x["id"] for x in chunks]
    assert [x["id"] for x in chunk_store.get_chunks(c, 1, ids)] == ids
    assert len(chunk_store.get_siblings(c, 1, ids[1])) == 3
    assert chunk_store.ids_for_file(c, 1, "big.py") == set(ids)
    assert chunk_store.all_ids(c, 1) == set(ids)
    c.close()
    assert conn.row_factory is sqlite3.Row  # and the store did not change the caller's connection


def test_duplicate_ids_in_one_save_are_refused(conn, chunks):
    twin = dict(chunks[0], text="different text")
    with pytest.raises(ValueError, match=chunks[0]["id"]):
        save(conn, chunks + [twin])
    assert chunk_store.all_ids(conn, 1) == set()  # nothing was written


def test_save_returns_the_ids_it_removed(conn, chunks):
    assert save(conn, chunks) == set()
    keep = [c for c in chunks if c["symbol"] != "small"]
    gone = {c["id"] for c in chunks if c["symbol"] == "small"}
    assert save(conn, keep, file_hash="h2") == gone


def test_delete_file_returns_the_ids_it_removed(conn, chunks):
    save(conn, chunks)
    assert chunk_store.delete_file(conn, 1, "big.py") == {c["id"] for c in chunks}
    assert chunk_store.delete_file(conn, 1, "big.py") == set()
