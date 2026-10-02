"""The chunks and files tables (SQLite is the truth, see docs/indexing-decisions.md #1)."""
import sqlite3
from pathlib import Path

import pytest

import db
from chunker import chunk_file

EDGE = Path(__file__).parent / "fixtures" / "edge"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    c = db.get_connection()
    c.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('p', '/r', 'now')")
    c.commit()
    yield c
    c.close()


def columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_init_db_is_safe_to_run_twice(conn):
    db.init_db()
    db.init_db()
    assert columns(conn, "chunks")


def test_files_table_columns(conn):
    assert {"project_id", "rel_path", "hash", "is_test", "is_changelog", "language"} <= columns(conn, "files")


def test_chunks_has_a_column_for_every_chunk_field(conn):
    chunks = chunk_file(str(EDGE / "b.py"), repo_root=str(EDGE))
    assert chunks
    missing = set(chunks[0]) - columns(conn, "chunks")
    assert not missing, f"chunk fields with no column: {missing}"


def test_chunks_has_model_tracking_columns(conn):
    assert {"embed_model", "embed_dim", "project_id"} <= columns(conn, "chunks")


def add_file(conn, rel_path="a.py"):
    conn.execute(
        "INSERT INTO files (project_id, rel_path, hash, is_test, is_changelog, language) "
        "VALUES (1, ?, 'h', 0, 0, 'python')", (rel_path,))


def add_chunk(conn, chunk_id="c1", rel_path="a.py", project_id=1):
    conn.execute(
        "INSERT INTO chunks (id, project_id, rel_path, content_hash, kind, start_line, end_line, text, embed_text) "
        "VALUES (?, ?, ?, 'h', 'function', 1, 2, 't', 'e')", (chunk_id, project_id, rel_path))


def test_chunk_needs_its_file(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_chunk(conn)


def test_deleting_a_file_deletes_its_chunks(conn):
    add_file(conn)
    add_chunk(conn, "c1")
    add_chunk(conn, "c2")
    conn.commit()
    conn.execute("DELETE FROM files WHERE rel_path = 'a.py'")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_same_chunk_id_allowed_in_two_projects(conn):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    add_file(conn)
    conn.execute("INSERT INTO files (project_id, rel_path, hash, is_test, is_changelog, language) "
                 "VALUES (2, 'a.py', 'h', 0, 0, 'python')")
    add_chunk(conn, "same", project_id=1)
    add_chunk(conn, "same", project_id=2)
    with pytest.raises(sqlite3.IntegrityError):
        add_chunk(conn, "same", project_id=1)


def test_model_columns_start_empty(conn):
    add_file(conn)
    add_chunk(conn)
    row = conn.execute("SELECT embed_model, embed_dim FROM chunks").fetchone()
    assert row["embed_model"] is None and row["embed_dim"] is None
