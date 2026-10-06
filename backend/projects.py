"""Projects: adding a folder, listing what is known about each one, removing one with everything that belongs to it.

A project is a folder on this machine. The folder is stored RESOLVED (no `..`, no symlink, no trailing slash) so the same folder cannot become two projects
under two spellings. Messages here are written for people and never repeat the path the caller sent.
"""
from datetime import datetime, timezone
from pathlib import Path

MAX_PATH_CHARS = 4096


class InvalidProjectPath(ValueError):
    """The folder cannot be a project. The message says why and never contains the path."""


class ProjectExists(RuntimeError):
    """This folder is already a project."""


class ProjectBusy(RuntimeError):
    """An index job is running for this project."""


def resolve_folder(raw: str) -> Path:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text or "\x00" in text or len(text) > MAX_PATH_CHARS:
        raise InvalidProjectPath("Give the full path of an existing folder.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise InvalidProjectPath("Give the full path of the folder, starting with / or ~.")
    try:
        resolved = path.resolve()
        is_folder = resolved.is_dir()
    except (OSError, RuntimeError):
        raise InvalidProjectPath("That path could not be read.") from None
    if not is_folder:
        raise InvalidProjectPath("That path is not an existing folder.")
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise InvalidProjectPath("That folder is too broad. Pick the project's own folder.")        # indexing a whole disk or a whole home folder is never what is meant
    return resolved


def _same_folder(stored: str, resolved: Path) -> bool:
    try:
        return Path(stored).resolve() == resolved
    except (OSError, RuntimeError):
        return stored == str(resolved)


def add_project(conn, raw_path: str, name: str | None = None) -> int:
    """Add a folder as a project and return its id. The check for a duplicate and the insert happen under one write lock."""
    resolved = resolve_folder(raw_path)
    label = (name or "").strip() or resolved.name or str(resolved)
    created = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in conn.execute("SELECT repo_path FROM projects").fetchall():
            if _same_folder(row["repo_path"], resolved):
                raise ProjectExists("this folder is already a project")
        project_id = conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES (?, ?, ?)", (label, str(resolved), created)).lastrowid
        conn.commit()
        return project_id
    except BaseException:
        conn.rollback()
        raise


_SUMMARY_SQL = """
    SELECT p.id, p.name, p.repo_path, p.created_at,
           (SELECT COUNT(*) FROM files f WHERE f.project_id = p.id) AS files,
           (SELECT COUNT(*) FROM chunks c WHERE c.project_id = p.id) AS chunks,
           (SELECT COUNT(*) FROM files f WHERE f.project_id = p.id AND f.index_error IS NOT NULL) AS flagged
    FROM projects p {where} ORDER BY p.id
"""


def _summary(row, index_state: str) -> dict:
    return {"id": row["id"], "name": row["name"], "path": row["repo_path"], "created_at": row["created_at"], "indexed": row["files"] > 0,
            "files": row["files"], "chunks": row["chunks"], "flagged_files": row["flagged"], "index_state": index_state}


def list_projects(conn, index_state) -> list[dict]:
    """Every project, in id order. `index_state(project_id)` gives the state of its index job (idle, running, done, ...)."""
    return [_summary(row, index_state(row["id"])) for row in conn.execute(_SUMMARY_SQL.format(where="")).fetchall()]


def get_project_summary(conn, project_id: int, index_state) -> dict | None:
    row = conn.execute(_SUMMARY_SQL.format(where="WHERE p.id = ?"), (project_id,)).fetchone()
    return None if row is None else _summary(row, index_state(project_id))


def remove_project(conn, project_id: int) -> None:
    """Delete the project and all its rows: messages, conversations (no cascade for those) and, by cascade, its files and chunks."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE project_id = ?)", (project_id,))
        conn.execute("DELETE FROM conversations WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
