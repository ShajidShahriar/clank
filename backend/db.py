import sqlite3
from datetime import datetime

DB_PATH = "app.db"
SCHEMA_VERSION = 2  # bump when the files/chunks tables change: they are rebuilt, see init_db

# Projects, conversations and messages are the user's data: never dropped.
USER_TABLES_SQL = """
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
"""

# Derived from the repo, so they can be dropped and rebuilt when SCHEMA_VERSION changes.
CHUNK_TABLES_SQL = """
        -- One row per indexed file. is_test / is_changelog are tags only: the
        -- chunker ignores them, retrieval decides what to do with them.
        CREATE TABLE IF NOT EXISTS files (
            project_id INTEGER NOT NULL,
            rel_path TEXT NOT NULL,
            hash TEXT NOT NULL,
            is_test INTEGER NOT NULL DEFAULT 0,
            is_changelog INTEGER NOT NULL DEFAULT 0,
            language TEXT,
            chunker_version TEXT,
            PRIMARY KEY (project_id, rel_path),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        -- SQLite is the truth about a chunk. Chroma holds only id -> vector.
        -- Chunk ids hash the path, not the project, so the key includes project_id.
        -- names is a JSON list. embed_model / embed_dim stay NULL until a vector exists.
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            rel_path TEXT NOT NULL,
            file_path TEXT,
            content_hash TEXT NOT NULL CHECK (content_hash <> ''),
            ordinal INTEGER,
            kind TEXT NOT NULL CHECK (kind <> ''),
            symbol TEXT,
            parent TEXT,
            names TEXT NOT NULL DEFAULT '[]',
            part INTEGER,
            part_count INTEGER,
            start_line INTEGER NOT NULL CHECK (start_line >= 1),
            end_line INTEGER NOT NULL,
            text TEXT NOT NULL CHECK (text <> ''),
            embed_text TEXT NOT NULL CHECK (embed_text <> ''),
            synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0, 1)),
            parse_error INTEGER NOT NULL DEFAULT 0 CHECK (parse_error IN (0, 1)),
            embed_model TEXT,
            embed_dim INTEGER,
            PRIMARY KEY (project_id, id),
            CHECK (end_line >= start_line),
            -- a split piece has both part and part_count; a whole chunk has neither
            CHECK ((part IS NULL AND part_count IS NULL)
                OR (part IS NOT NULL AND part_count IS NOT NULL AND part >= 0 AND part < part_count)),
            FOREIGN KEY (project_id, rel_path) REFERENCES files(project_id, rel_path) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(project_id, rel_path);
        CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(project_id, symbol);
"""


BUSY_TIMEOUT_SECONDS = 5


def get_connection():
    # The app writes chat messages while indexing writes chunks. WAL lets readers and one writer work at once,
    # and the timeout makes a second writer wait (up to 5 s) instead of failing at once with "database is locked".
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_SECONDS)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> bool:
    """Create the tables. Returns True if existing chunk data was dropped because the schema version changed.

    The caller must then also wipe the vector store: its ids no longer match any SQLite row.
    Projects, conversations and messages are the user's data and are never dropped.
    """
    conn = get_connection()
    conn.executescript(USER_TABLES_SQL)
    had_chunk_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('files', 'chunks')").fetchone()[0] > 0
    reset = False
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        # files and chunks are derived from the repo, so rebuilding them loses nothing that re-indexing cannot restore
        conn.executescript("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS files;")
        reset = had_chunk_tables
    conn.executescript(CHUNK_TABLES_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    conn.close()
    return reset

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
  