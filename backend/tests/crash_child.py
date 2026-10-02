"""Run by test_real_kill.py in a separate process: index a repo with the REAL Chroma store and a file-backed SQLite database, and die
abruptly (os._exit, no cleanup, like kill -9) at the given step. Usage: python crash_child.py <repo> <crash_at_step>
The data folder comes from CLANK_DATA_DIR."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

import datadir  # noqa: E402
import db  # noqa: E402
from crash_hooks import HookedEmbedder, HookedStore, Hooks, install  # noqa: E402
from indexing import index_project  # noqa: E402
from indexing.fingerprint import chunker_fingerprint  # noqa: E402
from vectorstore import open_project_store  # noqa: E402

repo, crash_at = sys.argv[1], int(sys.argv[2])
print(f"FINGERPRINT {chunker_fingerprint()}", flush=True)   # the parent checks it is running the SAME chunker (grouping setting included)
hooks = Hooks(crash_at=crash_at, hard=True)
install(hooks)
conn = db.get_connection()
store = HookedStore(open_project_store(datadir.data_dir(), 1), hooks)
index_project(conn, 1, repo, HookedEmbedder(hooks), store)
print("finished without being killed")   # the parent expects this line NOT to appear
