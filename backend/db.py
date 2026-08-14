import sqlite3
from datetime import datetime

DB_PATH = "app.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")  
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            repo_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        );
    """)
    conn.commit()
    conn.close()

# create / read functions
def create_project(name: str, repo_path: str) -> int:
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        "INSERT INTO projects (name, repo_path, created_at) VALUES (?, ?, ?)",
        (name, repo_path, now)
    )
    conn.commit()
    project_id = cursor.lastrowid
    conn.close()
    return project_id

def create_conversation(project_id: int, title: str = "New conversation") -> int:
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        "INSERT INTO conversations (project_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (project_id, title, now, now)
    )
    conn.commit()
    conversation_id = cursor.lastrowid
    conn.close()
    return conversation_id

def create_message(conversation_id: int, role: str, content: str) -> int:
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, now)
    )
    conn.commit()
    message_id = cursor.lastrowid
    conn.close()
    return message_id

def get_conversations_for_project(project_id: int):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE project_id = ? ORDER BY updated_at DESC",
        (project_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_messages_for_conversation(conversation_id: int):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (conversation_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
  