"""Saved conversations: a conversation belongs to one project and holds messages (a question, then its answer, and so on).

Every function takes the project's id where a caller could mix projects up, so another project's conversation is never reached by guessing its number.
The answer's extras (sources, notes, model, usage) live in `messages.meta` as JSON; the message text stays plain.
"""
import json
from datetime import datetime, timezone

TITLE_CHARS = 60


class ConversationNotFound(LookupError):
    """No such conversation in this project (the same for one that does not exist and one that belongs to another project)."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _title_from(question: str) -> str:
    return " ".join(question.split())[:TITLE_CHARS]


def create_conversation(conn, project_id: int, title: str | None = None) -> int:
    now = _now()
    cursor = conn.execute("INSERT INTO conversations (project_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)", (project_id, title, now, now))
    conn.commit()
    return cursor.lastrowid


def list_conversations(conn, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.title, c.created_at, c.updated_at, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
        "FROM conversations c WHERE c.project_id = ? ORDER BY c.updated_at DESC, c.id DESC", (project_id,)).fetchall()
    return [dict(row) for row in rows]


def get_conversation(conn, project_id: int, conversation_id: int) -> dict | None:
    """The conversation with its messages in the order they were written, or None when it is not THIS project's."""
    row = conn.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE id = ? AND project_id = ?", (conversation_id, project_id)).fetchone()
    if row is None:
        return None
    messages = conn.execute("SELECT id, role, content, meta, created_at FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
    return {**dict(row), "messages": [{**dict(m), "meta": None if m["meta"] is None else json.loads(m["meta"])} for m in messages]}


def add_exchange(conn, conversation_id: int, question: str, answer: str, meta: dict) -> None:
    """Save the question and its answer together: both rows or neither. The title is set from the first question and then left alone."""
    meta_json = json.dumps(meta)                       # before any write: an unsavable meta saves nothing
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)", (conversation_id, question, now))
        conn.execute("INSERT INTO messages (conversation_id, role, content, meta, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                     (conversation_id, answer, meta_json, now))
        conn.execute("UPDATE conversations SET updated_at = ?, title = COALESCE(title, ?) WHERE id = ?", (now, _title_from(question), conversation_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def delete_conversation(conn, project_id: int, conversation_id: int) -> bool:
    """Remove the conversation and its messages (no cascade: messages first). False when it is not this project's."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        owned = conn.execute("SELECT 1 FROM conversations WHERE id = ? AND project_id = ?", (conversation_id, project_id)).fetchone() is not None
        if owned:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
        return owned
    except BaseException:
        conn.rollback()
        raise


def recent_turns(conn, conversation_id: int) -> list[tuple[str, str]]:
    """(question, answer) pairs, oldest first, of the turns where the answer model was really called. A turn answered with "no matching code" says nothing
    worth remembering."""
    rows = conn.execute("SELECT role, content, meta FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
    turns, question = [], None
    for row in rows:
        if row["role"] == "user":
            question = row["content"]
        elif question is not None and row["meta"] is not None and json.loads(row["meta"]).get("llm_called"):
            turns.append((question, row["content"]))
            question = None
    return turns
