"""Read-only view of the lines of one project file, for the window's source viewer (`POST /projects/{id}/source`).

This is the one place where a caller chooses a path and the backend reads that file, so it is strict, in this order:
1. the range is checked (whole numbers, start at least 1, end not before start, at most MAX_VIEW_LINES lines): refused, never clamped;
2. the path must be EXACTLY a `rel_path` the index stores for THIS project. Nothing is built from the caller's text: `..`, an absolute path, `.env`, `.git/config`,
   a file that was never indexed, another project's file: none of them has a row, so none is ever opened;
3. the real file (symlinks resolved) must lie inside the project's folder (an indexed file may have been replaced by a symlink since);
4. it is opened without following a last-moment symlink and without blocking on a pipe, and must be a regular file; at most MAX_VIEW_BYTES + 1 bytes are
   ever read (so a huge file is never loaded, and one that grew meanwhile is caught);
5. it must be text (no NUL byte at the start) and at most MAX_VIEW_BYTES.
Every reason a file cannot be shown (no row, outside, missing, a folder or pipe, unreadable) raises the SAME `SourceNotAvailable`, so the answer cannot be used
to ask "does this file exist on the disk?". No message repeats the path. Lines are counted like the chunker counts them (`decode_text`, split on "\\n").
"""
import hashlib
import os
import stat
from pathlib import Path

import chunk_store
import db
from chunker.core import decode_text
from jobs import ProjectNotFound, RepoNotFound

MAX_VIEW_BYTES = 1_000_000          # the indexer's own limit for a source file: anything the index holds is smaller
MAX_VIEW_LINES = 500
MAX_LINE_CHARS = 2000               # a minified bundle is one huge line: the viewer shows its start
MAX_PATH_CHARS = 4096
BINARY_SNIFF_BYTES = 8192

RepoGone = RepoNotFound


class SourceNotAvailable(LookupError):
    """This file cannot be shown (the one answer for every reason: see the module text)."""


class SourceTooBig(ValueError):
    """The file is bigger than MAX_VIEW_BYTES."""


class SourceNotText(ValueError):
    """The file is binary."""


class SourceChanged(RuntimeError):
    """The file has fewer lines than the source says: it changed after it was indexed."""


class BadRange(ValueError):
    """The requested lines are not a valid range. The message is for people."""


def _whole(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool)


def _check_range(start, end) -> None:
    if not _whole(start) or not _whole(end) or start < 1:
        raise BadRange("Give the first and last line as whole numbers, starting at 1.")
    if end < start:
        raise BadRange("The last line cannot come before the first.")
    if end - start + 1 > MAX_VIEW_LINES:
        raise BadRange(f"Ask for at most {MAX_VIEW_LINES} lines at a time.")


def _read_file(root: Path, rel_path: str) -> bytes:
    try:
        target = (root / rel_path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise SourceNotAvailable from None
    if not target.is_relative_to(root):
        raise SourceNotAvailable
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise SourceNotAvailable from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SourceNotAvailable
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_VIEW_BYTES + 1)         # one more than allowed: a file that grew while it was opened is caught too
    except OSError:
        raise SourceNotAvailable from None
    finally:
        os.close(fd)
    if len(data) > MAX_VIEW_BYTES:
        raise SourceTooBig
    return data


def read_source(conn, project_id: int, rel_path, start_line, end_line) -> dict:
    """The lines start_line..end_line of the indexed file `rel_path` (an end past the end of the file is cut to it), and whether the file changed since."""
    _check_range(start_line, end_line)
    project = db.get_project(conn, project_id)
    if project is None:
        raise ProjectNotFound(f"no project {project_id}")
    if not isinstance(rel_path, str):
        raise SourceNotAvailable                            # (an empty, NUL or giant string simply has no row)
    indexed_hash = chunk_store.file_hash(conn, project_id, rel_path)
    if indexed_hash is None:
        raise SourceNotAvailable
    try:
        root = Path(project["repo_path"]).resolve()
        is_folder = root.is_dir()
    except (OSError, RuntimeError):
        is_folder = False
    if not is_folder:
        raise RepoGone(f"project {project_id}'s folder is gone")
    data = _read_file(root, rel_path)
    if b"\0" in data[:BINARY_SNIFF_BYTES]:
        raise SourceNotText
    lines = decode_text(data).split("\n")
    if lines and lines[-1] == "":
        lines.pop()                                         # the newline that ends the last line does not start another
    if start_line > len(lines):
        raise SourceChanged
    end = min(end_line, len(lines))
    shown = [line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…" for line in lines[start_line - 1:end]]
    return {"path": rel_path, "start_line": start_line, "end_line": end, "total_lines": len(lines),
            "stale": hashlib.sha1(data).hexdigest() != indexed_hash, "lines": shown}
