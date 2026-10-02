"""One index run per project at a time.

A file lock (flock, non-blocking) so it also covers two backend processes, and so the operating system releases it if the holder
dies: a crash never leaves a stuck lock. Where `fcntl` does not exist the in-process lock alone is used.
"""
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # e.g. Windows: only the in-process lock is available
    fcntl = None

_guard = threading.Lock()
_held_here: set[tuple[str, int]] = set()


class IndexAlreadyRunning(RuntimeError):
    """Another index run holds this project's lock."""


@contextmanager
def project_lock(lock_dir, project_id: int):
    folder = Path(lock_dir)
    key = (str(folder.resolve()), project_id)
    refusal = IndexAlreadyRunning(f"project {project_id} is already being indexed")
    with _guard:
        if key in _held_here:
            raise refusal
        _held_here.add(key)
    handle = None
    try:
        if fcntl is not None:
            folder.mkdir(parents=True, exist_ok=True)
            handle = open(folder / f"index-{project_id}.lock", "a+")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:  # another process holds it
                raise refusal from None
        yield
    finally:
        if handle is not None:
            handle.close()  # closing releases the flock
        with _guard:
            _held_here.discard(key)
