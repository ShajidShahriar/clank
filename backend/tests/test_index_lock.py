"""One index run per project at a time (task I-5.0, fix #4 from the I-4 review).

The startup catch-up and the Index button can both ask for a run. Two parallel runs both embedded everything (double work) and only
ended consistent by luck. The lock is a file with a non-blocking flock, so it also covers two backend processes, and the operating
system releases it if the holder dies, so a crash never leaves a stuck lock.
"""
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import db
import indexing.lock as lock_module
from embedding import FakeEmbedder
from indexing import IndexAlreadyRunning, index_project, project_lock
from vectorstore import InMemoryVectorStore

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("def f():\n    return 1\n")
    return root


def test_a_second_holder_is_refused_at_once_and_the_lock_is_free_afterwards(tmp_path):
    with project_lock(tmp_path, 1):
        with pytest.raises(IndexAlreadyRunning, match="project 1"):
            with project_lock(tmp_path, 1):
                pytest.fail("the second holder must not get in")
    with project_lock(tmp_path, 1):          # released
        pass


def test_the_lock_is_released_when_the_work_inside_fails(tmp_path):
    with pytest.raises(RuntimeError):
        with project_lock(tmp_path, 1):
            raise RuntimeError("boom")
    with project_lock(tmp_path, 1):
        pass


def test_different_projects_do_not_block_each_other(tmp_path):
    with project_lock(tmp_path, 1), project_lock(tmp_path, 2):
        pass


def test_without_flock_the_in_process_lock_still_refuses_a_second_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(lock_module, "fcntl", None)           # e.g. a platform without fcntl
    with project_lock(tmp_path, 1):
        with pytest.raises(IndexAlreadyRunning):
            with project_lock(tmp_path, 1):
                pass
    with project_lock(tmp_path, 1):
        pass


def test_a_lock_held_by_another_process_is_respected_and_dies_with_that_process(tmp_path):
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, sys.argv[1]);"
         "from indexing.lock import project_lock\n"
         "with project_lock(sys.argv[2], 1):\n"
         "    print('locked', flush=True); time.sleep(30)", str(BACKEND), str(tmp_path)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(IndexAlreadyRunning):
            with project_lock(tmp_path, 1):
                pass
    finally:
        holder.kill()                                          # a crash: no chance to clean up
        holder.wait()
    with project_lock(tmp_path, 1):                            # the operating system freed it
        pass


class Blocking(FakeEmbedder):
    """Holds the first embedding call until the test lets go."""

    def __init__(self):
        super().__init__()
        self.started, self.release = threading.Event(), threading.Event()

    def embed_documents(self, texts, ids=None):
        self.started.set()
        assert self.release.wait(10)
        return super().embed_documents(texts, ids)


def test_two_index_runs_for_one_project_cannot_overlap(conn, repo, tmp_path):
    first_embedder, outcome = Blocking(), {}

    def first_run():
        c = db.get_connection()                                # a connection cannot be used from another thread
        try:
            outcome["report"] = index_project(c, 1, repo, first_embedder, InMemoryVectorStore(), lock_dir=tmp_path / "locks")
        except Exception as problem:                           # report it to the main thread instead of losing it
            outcome["error"] = problem
        finally:
            c.close()

    runner = threading.Thread(target=first_run)
    runner.start()
    assert first_embedder.started.wait(10)                     # the first run is in the middle of embedding
    second_embedder = FakeEmbedder()
    with pytest.raises(IndexAlreadyRunning):
        index_project(conn, 1, repo, second_embedder, InMemoryVectorStore(), lock_dir=tmp_path / "locks")
    assert second_embedder.text_count == 0 and second_embedder.warmup_count == 0   # refused before doing any work
    first_embedder.release.set()
    runner.join(10)
    assert "error" not in outcome and outcome["report"].embedded > 0
    again = index_project(conn, 1, repo, FakeEmbedder(), InMemoryVectorStore(), lock_dir=tmp_path / "locks")
    assert again.embedded > 0                                  # the lock was released


def test_index_project_uses_the_data_folder_for_its_lock_by_default(conn, repo):
    import datadir
    index_project(conn, 1, repo, FakeEmbedder(), InMemoryVectorStore())
    assert (datadir.lock_dir() / "index-1.lock").exists()
