"""Task I-2d: the store on real data. Every fixture, then this whole repo, grouping off and on.

test_chunk_store.py checks each function on one small file. This checks that nothing the chunker
really produces is lost, changed or refused on the way through SQLite.
"""
from pathlib import Path

import pytest

import chunk_store
from chunker import chunk_file, grouping
from file_discovery import discover_files
from test_invariants import source_file  # noqa: F401  (pytest fixture: every dummy, edge and generated file)

REPO = Path(__file__).resolve().parents[2]
STORED_ONLY = {"embed_model", "embed_dim"}


@pytest.fixture(params=[False, True], ids=["grouping-off", "grouping-on"])
def grouping_mode(request, monkeypatch):
    monkeypatch.setattr(grouping, "GROUP_SMALL_CHUNKS", request.param)


def without_stored_only(row):
    return {k: v for k, v in row.items() if k not in STORED_ONLY}


def test_every_fixture_round_trips(conn, source_file, grouping_mode):
    path, _ = source_file
    chunks = chunk_file(str(path), repo_root=str(path.parent))
    chunk_store.save_file_chunks(conn, 1, path.name, "h", chunks)
    back = chunk_store.get_chunks(conn, 1, [c["id"] for c in chunks])
    assert [without_stored_only(b) for b in back] == chunks
    assert chunk_store.ids_for_file(conn, 1, path.name) == {c["id"] for c in chunks}


def index_repo(conn, project_id=1):
    """Chunk and save every file discovery returns for this repo. Returns {rel_path: chunks}."""
    saved = {}
    for f in discover_files(str(REPO)):
        chunks = chunk_file(str(f), repo_root=str(REPO))
        if chunks:
            rel = chunks[0]["rel_path"]
            chunk_store.save_file_chunks(conn, project_id, rel, "h", chunks)
            saved[rel] = chunks
    return saved


def test_whole_repo_round_trips(conn, grouping_mode):
    saved = index_repo(conn)
    assert len(saved) > 30  # the repo really was indexed, not an empty loop passing trivially
    total = 0
    for rel, chunks in saved.items():
        back = chunk_store.get_chunks(conn, 1, [c["id"] for c in chunks])
        assert [without_stored_only(b) for b in back] == chunks, rel
        total += len(chunks)
    assert len(chunk_store.all_ids(conn, 1)) == total  # ids are unique across the repo, so nothing overwrote anything


def test_saving_the_repo_twice_changes_nothing(conn, grouping_mode):
    index_repo(conn)
    first = conn.execute("SELECT * FROM chunks ORDER BY id").fetchall()
    index_repo(conn)
    second = conn.execute("SELECT * FROM chunks ORDER BY id").fetchall()
    assert [tuple(r) for r in first] == [tuple(r) for r in second]


def test_siblings_on_real_chunks(conn, grouping_mode):
    saved = index_repo(conn)
    split = whole = 0
    for chunks in saved.values():
        for c in chunks:
            got = chunk_store.get_siblings(conn, 1, c["id"])
            if c["part_count"] is None:
                whole += 1
                assert [g["id"] for g in got] == [c["id"]], f"{c['rel_path']} {c['symbol']} lines {c['start_line']}"
            else:
                split += 1
                assert [g["part"] for g in got] == list(range(c["part_count"])), f"{c['rel_path']} {c['symbol']}"
    assert whole > 0 and split > 0  # the repo has both kinds, so both branches were exercised


def test_second_project_with_the_same_files_stays_separate(conn, grouping_mode):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    conn.commit()
    saved = index_repo(conn, 1)
    index_repo(conn, 2)
    assert chunk_store.all_ids(conn, 1) == chunk_store.all_ids(conn, 2)
    some_file = next(iter(saved))
    chunk_store.delete_file(conn, 2, some_file)
    assert chunk_store.ids_for_file(conn, 2, some_file) == set()
    assert chunk_store.ids_for_file(conn, 1, some_file) == {c["id"] for c in saved[some_file]}
