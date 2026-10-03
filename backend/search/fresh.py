"""Is the index still true? Compare each result's file with the file as it was indexed.

For the files that appear in the results (at most k of them) the live file's content hash is compared with the hash stored when it was indexed:
- changed (or unreadable, which cannot be verified): the passage is kept and marked STALE: its line numbers and code may be out of date;
- deleted: the passage is dropped (before the budget is spent on it) and the file is listed;
- same: nothing. Content, not modification time: rewriting identical bytes is still fresh.
The same check the stitcher uses before it trusts a pointer into a file, applied to every result.
"""
import dataclasses
import hashlib
from pathlib import Path

import chunk_store


def check_freshness(conn, project_id, repo_path, passages):
    """-> (passages to show, files that changed, files that no longer exist). Order and everything else about the passages is unchanged."""
    status = {}
    for passage in passages:
        if passage.rel_path not in status:
            status[passage.rel_path] = _status(conn, project_id, repo_path, passage.rel_path)
    kept = [dataclasses.replace(p, stale=True) if status[p.rel_path] == "stale" else p for p in passages if status[p.rel_path] != "deleted"]
    stale = sorted({rel for rel, s in status.items() if s == "stale"})
    deleted = sorted({rel for rel, s in status.items() if s == "deleted"})
    return kept, stale, deleted


def _status(conn, project_id, repo_path, rel_path) -> str:
    root = Path(repo_path).resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root):
        return "deleted"                                   # cannot be a file of this repo
    try:
        live = hashlib.sha1(target.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "deleted"
    except OSError:
        return "stale"                                     # exists but cannot be read: cannot be verified, so not trusted
    return "fresh" if live == chunk_store.file_hash(conn, project_id, rel_path) else "stale"
