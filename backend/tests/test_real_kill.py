"""Task I-5.4, layer 2: a REAL kill. The in-process matrix (test_crash_matrix.py) simulates a crash with an exception. This test runs the
pipeline in a separate process, with the real Chroma store and a file-backed SQLite database, and ends that process abruptly with
os._exit(137) (no cleanup, no `finally`, like kill -9) at chosen steps. Then it recovers in this process and compares with a clean run.
What it proves that the simulation cannot: that SQLite (WAL) and Chroma really come back from a hard stop, and that the lock is released.
"""
import gc
import os
import subprocess
import sys
from pathlib import Path

import pytest
from chromadb.api.shared_system_client import SharedSystemClient

import chunk_store
import datadir
import db
from chunker import grouping
from crash_hooks import HookedEmbedder, HookedStore, Hooks, install
from embedding import FakeEmbedder
from indexing import index_project
from indexing.fingerprint import chunker_fingerprint
from vectorstore import open_project_store

CHILD = Path(__file__).resolve().parent / "crash_child.py"
FILES = {
    "a.py": "import os\n\n\ndef first():\n    return 1\n\n\ndef second():\n    return 2\n",
    "b.py": "def helper():\n    return 'b'\n",
    "c.js": "function render() {\n  return 1;\n}\n",
    "README.md": "# Shop\n\nA tiny shop.\n\n## Usage\n\nRun it.\n",
}


def release_chroma():
    """Chroma keeps one cached client per folder inside a process. In the real app ONE process owns the store and a recovery run is a new
    process that reads the folder fresh. This test has a parent and a child, so the parent must let go of the folder while the child works
    and must not reuse a cached client afterwards, or it would read its own stale in-memory index (this was the cause of a false failure)."""
    SharedSystemClient.clear_system_cache()
    gc.collect()


def make_world(base, monkeypatch, scenario):
    data, repo = base / "data", base / "repo"
    repo.mkdir(parents=True)
    for name, text in FILES.items():
        (repo / name).write_text(text)
    monkeypatch.setenv("CLANK_DATA_DIR", str(data))
    monkeypatch.setattr(db, "DB_PATH", None)                    # use the data folder, exactly as the child will
    db.init_db()
    db.create_project("p", str(repo))
    if scenario == "update":
        run_here(repo)
        (repo / "a.py").write_text(FILES["a.py"].replace("return 2", "return 22"))
        (repo / "d.py").write_text("def added():\n    return 'd'\n")
        (repo / "b.py").unlink()
        (repo / "README.md").write_text("\n\n" + FILES["README.md"])
    release_chroma()
    return repo


def run_here(repo, hooks=None):
    hooks = hooks or Hooks()
    conn, undo = db.get_connection(), install(hooks)
    try:
        store = open_project_store(datadir.data_dir(), 1)
        return index_project(conn, 1, repo, HookedEmbedder(hooks), HookedStore(store, hooks))
    finally:
        undo()
        conn.close()
        release_chroma()


def snapshot():
    conn = db.get_connection()
    try:
        rows = [tuple(r) for r in conn.execute(
            "SELECT id, rel_path, content_hash, start_line, end_line, text, embed_text, embed_model, embed_dim FROM chunks ORDER BY rel_path, id")]
        files = [tuple(r) for r in conn.execute("SELECT rel_path, hash, chunker_version, index_error FROM files ORDER BY rel_path")]
    finally:
        conn.close()
    release_chroma()
    store = open_project_store(datadir.data_dir(), 1)
    fake = FakeEmbedder()
    for row in rows:      # each chunk's own text must find its own stored vector: the vectors are the right ones, not just present
        (found, score), = store.query(fake.embed_documents([row[6]])[0], 1)
        assert found == row[0] and score > 0.9999, f"the vector stored for {row[1]} is not the vector of its text"
    state = {"rows": rows, "files": files, "ids": store.ids(), "signature": store.signature()}
    del store
    release_chroma()
    return state


@pytest.mark.parametrize("scenario", ["first_run", "update"])
def test_a_process_killed_at_chosen_steps_recovers_to_the_clean_state(scenario, tmp_path, monkeypatch):
    # conftest switches grouping OFF for the tests, but the child process runs with the production default (ON). Two different chunkers
    # would have a different fingerprint, so the recovery run would re-process every file and MASK what this test is for (found by a
    # mutation that survived). Use the production setting here, and check below that the child really runs the same chunker.
    monkeypatch.setattr(grouping, "GROUP_SMALL_CHUNKS", True)
    clean_repo = make_world(tmp_path / "clean", monkeypatch, scenario)
    hooks = Hooks()
    run_here(clean_repo, hooks)
    reference, labels = snapshot(), hooks.labels
    assert reference["ids"] == {row[0] for row in reference["rows"]}

    def first(label):
        return labels.index(label)

    points = {"embedding": first("embedder.embed:before"),
              "vectors written, rows not yet": first("chunk_store.save_file_chunks:before"),
              "rows committed, stale vectors not yet deleted": first("chunk_store.save_file_chunks:after"),
              "middle of the run": len(labels) // 2,
              "very last step": len(labels) - 1}
    if "store.delete:before" in labels:
        points["about to delete stale vectors"] = first("store.delete:before")

    for name, k in points.items():
        repo = make_world(tmp_path / f"kill-{k}", monkeypatch, scenario)
        child = subprocess.run([sys.executable, str(CHILD), str(repo), str(k)], capture_output=True, text=True,
                               env={**os.environ, "CLANK_DATA_DIR": str(datadir.data_dir())}, timeout=120)
        assert child.returncode == 137, f"[{name}] the child was meant to be killed at step {k} but exited {child.returncode}: {child.stderr[-400:]}"
        assert "finished without being killed" not in child.stdout
        assert f"FINGERPRINT {chunker_fingerprint()}" in child.stdout, f"[{name}] the child runs a different chunker than this process"

        report = run_here(repo)                                          # recovery: the lock was freed by the OS, the stores reopen
        assert report.stopped is None, f"[{name}] {report.describe()}"
        assert snapshot() == reference, f"[{name}] killed at step {k} ({labels[k]}), recovered state differs from a clean run"
        settled = run_here(repo)
        assert settled.embedded == 0 and settled.files_written == 0, f"[{name}] the recovered index was not settled"
        assert snapshot() == reference
        assert chunk_store.failed_files(db.get_connection(), 1) == {}
