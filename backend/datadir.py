"""Where Clank keeps its data: one absolute folder, never "wherever the backend happened to start".

~/.clank by default; CLANK_DATA_DIR overrides it (Electron passes its user-data path through that variable, and tests point it at a
temp folder). A folder this function creates is private (0700) because it holds a database of the user's code and its embeddings.
A folder that already exists is left exactly as it is: it may belong to somebody else.
"""
import os
from pathlib import Path


def data_dir() -> Path:
    raw = os.environ.get("CLANK_DATA_DIR", "")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            # a relative path would mean a different folder for every working folder: the bug this module exists to prevent
            raise ValueError(f"CLANK_DATA_DIR must be an absolute path, got {raw!r}")
    else:
        path = Path.home() / ".clank"
    path = path.resolve()
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if created:
        path.chmod(0o700)   # mkdir's mode is trimmed by the umask; make it exact
    return path


def db_path() -> Path:
    return data_dir() / "app.db"


def lock_dir() -> Path:
    path = data_dir() / "locks"
    path.mkdir(exist_ok=True)
    return path
