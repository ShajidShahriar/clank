"""Task: saved conversations, the storage layer (`conversations.py`).

What is promised:
- a conversation belongs to ONE project; the other project's conversations are never listed, read or deleted through this one;
- an exchange (the question, then the answer) is saved in ONE transaction: both messages or neither;
- the answer's extras (sources, notes, model) are kept with the message as JSON in `messages.meta` and come back as a dict;
- the title is the first question (cut to a short length) unless a title was given; it is set once and never changed by later questions;
- a list shows the newest conversation first, with the number of messages; messages come back in the order they were written;
- deleting a conversation removes its messages and nothing else;
- the `meta` column is added to an existing database without losing a single saved message.
"""
import sqlite3

import pytest

import conversations
import db


@pytest.fixture
def two_projects(conn):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', '/s', 'now')")
    conn.commit()
    return conn


def test_a_new_conversation_is_empty_and_listed(conn):
    cid = conversations.create_conversation(conn, 1)
    assert conversations.get_conversation(conn, 1, cid)["messages"] == []
    [row] = conversations.list_conversations(conn, 1)
    assert row["id"] == cid and row["message_count"] == 0 and row["title"] is None


def test_an_exchange_saves_two_messages_in_order_with_meta(conn):
    cid = conversations.create_conversation(conn, 1)
    meta = {"sources": [{"path": "a.py", "start": 1, "end": 4}], "model": "m"}
    conversations.add_exchange(conn, cid, "what is a?", "a is a thing", meta)
    messages = conversations.get_conversation(conn, 1, cid)["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [("user", "what is a?"), ("assistant", "a is a thing")]
    assert messages[0]["meta"] is None
    assert messages[1]["meta"] == meta


def test_the_title_is_the_first_question_cut_short_and_never_changes(conn):
    cid = conversations.create_conversation(conn, 1)
    conversations.add_exchange(conn, cid, "x" * 500, "a", {})
    conversations.add_exchange(conn, cid, "second", "b", {})
    title = conversations.get_conversation(conn, 1, cid)["title"]
    assert title == "x" * conversations.TITLE_CHARS
    given = conversations.create_conversation(conn, 1, "My title")
    conversations.add_exchange(conn, given, "question", "a", {})
    assert conversations.get_conversation(conn, 1, given)["title"] == "My title"


def test_the_list_is_newest_first_and_counts_messages(conn):
    first = conversations.create_conversation(conn, 1)
    second = conversations.create_conversation(conn, 1)
    conversations.add_exchange(conn, first, "q", "a", {})          # the older conversation was used last
    rows = conversations.list_conversations(conn, 1)
    assert [r["id"] for r in rows] == [first, second]
    assert [r["message_count"] for r in rows] == [2, 0]


def test_another_projects_conversations_are_out_of_reach(two_projects):
    mine = conversations.create_conversation(two_projects, 1)
    conversations.add_exchange(two_projects, mine, "q", "a", {})
    assert conversations.list_conversations(two_projects, 2) == []
    assert conversations.get_conversation(two_projects, 2, mine) is None
    assert conversations.delete_conversation(two_projects, 2, mine) is False
    assert len(conversations.get_conversation(two_projects, 1, mine)["messages"]) == 2


def test_delete_removes_the_messages_and_only_this_conversation(conn):
    gone = conversations.create_conversation(conn, 1)
    kept = conversations.create_conversation(conn, 1)
    conversations.add_exchange(conn, gone, "q", "a", {})
    conversations.add_exchange(conn, kept, "q", "a", {})
    assert conversations.delete_conversation(conn, 1, gone) is True
    assert conversations.get_conversation(conn, 1, gone) is None
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (gone,)).fetchone()[0] == 0
    assert len(conversations.get_conversation(conn, 1, kept)["messages"]) == 2
    assert conversations.delete_conversation(conn, 1, gone) is False


def test_an_exchange_is_all_or_nothing(conn, monkeypatch):
    cid = conversations.create_conversation(conn, 1)
    real_execute = conn.execute

    class Wrapper:
        """Fails the assistant message's insert, after the user message went in."""
        def __init__(self, inner):
            self.inner, self.inserts = inner, 0

        def execute(self, sql, *args):
            if sql.lstrip().startswith("INSERT INTO messages"):
                self.inserts += 1
                if self.inserts == 2:
                    raise sqlite3.OperationalError("disk is full")
            return self.inner.execute(sql, *args)

        def commit(self):
            self.inner.commit()

        def rollback(self):
            self.inner.rollback()

    with pytest.raises(sqlite3.OperationalError):
        conversations.add_exchange(Wrapper(conn), cid, "q", "a", {})
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert real_execute("SELECT title FROM conversations WHERE id = ?", (cid,)).fetchone()["title"] is None


def test_meta_that_cannot_be_saved_as_json_saves_nothing(conn):
    cid = conversations.create_conversation(conn, 1)
    with pytest.raises(TypeError):
        conversations.add_exchange(conn, cid, "q", "a", {"bad": object()})
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_the_meta_column_is_added_to_an_old_database_and_keeps_every_message(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, repo_path TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, title TEXT, created_at TEXT NOT NULL,
                                    updated_at TEXT NOT NULL, FOREIGN KEY (project_id) REFERENCES projects(id));
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                               created_at TEXT NOT NULL, FOREIGN KEY (conversation_id) REFERENCES conversations(id));
        INSERT INTO projects VALUES (1, 'p', '/r', 'now');
        INSERT INTO conversations VALUES (1, 1, 'old chat', 'now', 'now');
        INSERT INTO messages VALUES (1, 1, 'user', 'kept', 'now');
    """)
    old.commit()
    old.close()
    db.init_db()
    db.init_db()                                                     # a second start must not fail on the existing column
    c = db.get_connection()
    [message] = conversations.get_conversation(c, 1, 1)["messages"]
    c.close()
    assert message["content"] == "kept" and message["meta"] is None
