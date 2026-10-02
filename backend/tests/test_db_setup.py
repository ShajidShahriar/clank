"""Task I-4.2: how the database is opened and versioned.

- WAL mode and a busy timeout, because the app writes chat messages while indexing writes chunks.
- A schema version: chunks and files are DERIVED data, so when the version changes we drop and rebuild
  them (and the caller wipes the vector store). Projects, conversations and messages are never dropped.
"""
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

import chunk_store
import db
from chunker import chunk_file

REPO = Path(__file__).resolve().parents[2]


def journal_mode(c):
    return c.execute("PRAGMA journal_mode").fetchone()[0]


def test_every_connection_uses_wal_a_busy_timeout_and_foreign_keys(conn):
    c = db.get_connection()  # a brand new connection, the way a background job would open one
    try:
        assert journal_mode(c) == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] >= 1000
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        c.close()


def test_a_second_writer_waits_instead_of_failing_with_database_is_locked(conn):
    # A SQLite connection can only be used in the thread that made it, so the thread holding the lock opens its own.
    locked, release = threading.Event(), threading.Event()

    def hold_the_write_lock():
        other = db.get_connection()
        other.execute("BEGIN IMMEDIATE")
        other.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('held', '/h', 'now')")
        locked.set()
        release.wait(5)
        other.commit()
        other.close()

    holder = threading.Thread(target=hold_the_write_lock)
    holder.start()
    assert locked.wait(5)
    threading.Timer(0.3, release.set).start()
    started = time.monotonic()
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('waiter', '/w', 'now')")
    conn.commit()
    waited = time.monotonic() - started
    holder.join()
    assert 0.2 < waited < 3, waited  # it waited for the other writer, and did not give up
    assert conn.execute("SELECT COUNT(*) FROM projects WHERE name IN ('held', 'waiter')").fetchone()[0] == 2


def test_wal_files_are_git_ignored():
    for name in ("backend/app.db", "backend/app.db-wal", "backend/app.db-shm"):
        result = subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO)
        assert result.returncode == 0, f"{name} is not ignored by git"


def test_store_calls_never_leave_a_transaction_open(conn, tmp_path):
    # Rule for the indexer: never hold a SQLite transaction while calling the embedder.
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    chunks = chunk_file(str(tmp_path / "a.py"), repo_root=str(tmp_path))
    chunk_store.save_file_chunks(conn, 1, "a.py", "h", chunks)
    assert not conn.in_transaction
    chunk_store.get_chunks(conn, 1, [c["id"] for c in chunks])
    chunk_store.get_siblings(conn, 1, chunks[0]["id"])
    chunk_store.needs_embedding(conn, 1, "m", 4)
    chunk_store.ids_for_file(conn, 1, "a.py")
    chunk_store.all_ids(conn, 1)
    assert not conn.in_transaction
    chunk_store.delete_file(conn, 1, "a.py")
    assert not conn.in_transaction


# ---- schema version ----

def add_some_data(c):
    c.execute("INSERT INTO files (project_id, rel_path, hash) VALUES (1, 'a.py', 'h')")
    c.execute("INSERT INTO chunks (id, project_id, rel_path, content_hash, kind, start_line, end_line, text, embed_text) "
              "VALUES ('c1', 1, 'a.py', 'h', 'function', 1, 2, 't', 'e')")
    c.execute("INSERT INTO conversations (project_id, title, created_at, updated_at) VALUES (1, 'chat', 'now', 'now')")
    c.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (1, 'user', 'hello', 'now')")
    c.commit()


def user_version(c):
    return c.execute("PRAGMA user_version").fetchone()[0]


def test_a_fresh_database_gets_the_current_version_and_reports_no_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fresh.db"))
    assert db.init_db() is False
    c = db.get_connection()
    assert user_version(c) == db.SCHEMA_VERSION >= 1
    c.close()


def test_running_init_db_again_keeps_everything(conn):
    add_some_data(conn)
    assert db.init_db() is False
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


@pytest.mark.parametrize("old_version", [0, db.SCHEMA_VERSION + 1], ids=["old-unversioned", "newer-than-this-code"])
def test_a_different_version_rebuilds_the_chunk_tables_and_says_so(conn, old_version):
    add_some_data(conn)
    conn.execute(f"PRAGMA user_version = {old_version}")
    conn.commit()
    assert db.init_db() is True                     # the caller must now wipe the vector store too
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    assert user_version(conn) == db.SCHEMA_VERSION
    # the user's own data is untouched
    assert conn.execute("SELECT content FROM messages").fetchone()["content"] == "hello"
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    # and the rebuilt tables work
    add_some_data_again = conn.execute("INSERT INTO files (project_id, rel_path, hash) VALUES (1, 'b.py', 'h')")
    conn.commit()
    assert add_some_data_again.rowcount == 1
    assert db.init_db() is False                    # and it is a one-time event


def test_an_old_database_without_chunk_tables_just_gets_them(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)  # what app.db looked like before the indexing work: user tables only, version 0
    old.executescript("CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                      "repo_path TEXT NOT NULL, created_at TEXT NOT NULL); "
                      "INSERT INTO projects (name, repo_path, created_at) VALUES ('keep me', '/r', 'now');")
    old.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    assert db.init_db() is False                    # nothing derived existed, so nothing to wipe
    c = db.get_connection()
    assert c.execute("SELECT name FROM projects").fetchone()["name"] == "keep me"
    assert c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert user_version(c) == db.SCHEMA_VERSION
    c.close()
