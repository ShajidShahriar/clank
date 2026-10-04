"""Task 8.3: background indexing.

- `POST /projects/{id}/index` answers 202 at once and runs `index_project` on a worker thread with its OWN SQLite connection (closed afterwards);
- progress comes from the existing callback; a clean cancel flag is checked BETWEEN files (a cancelled run is a stopped run: it deletes and sweeps nothing);
- one job per project: a second start while one runs is refused at once (409), different projects may run side by side; the file lock covers other processes;
- the endpoints are plain `def` (never `async def` around blocking calls);
- what goes back to the caller never holds a raw exception text or a path outside the repo; the real reason goes to the log.
"""
import inspect
import threading

import pytest
from fastapi.testclient import TestClient

import db
import chunk_store
from embedding import FakeEmbedder
from embedding.errors import OllamaUnavailable
from indexing import index_project
from jobs import IndexJobs
from main import create_app
from services import Services
from vectorstore import InMemoryVectorStore


class Gate(FakeEmbedder):
    """embed_documents() blocks until `release` is set, so a test can look at a run while it is in the middle of a file."""

    def __init__(self):
        super().__init__(dim=4, digest="d1")
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed_documents(self, texts, ids=None):
        self.entered.set()
        assert self.release.wait(10), "the test never released the gate"
        return super().embed_documents(texts, ids)


def write_repo(root, names=("a.py", "b.py", "c.py")):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_text(f"def {name[:-3]}_fn():\n    return '{name}'\n")
    return root


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Two projects with small repos. Returns a namespace-like dict; `make(embedder)` builds (client, services, jobs)."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    db.init_db()
    repos = [write_repo(tmp_path / "repo1"), write_repo(tmp_path / "repo2", ("x.py", "y.py"))]
    ids = [db.create_project(f"p{i}", str(r)) for i, r in enumerate(repos, 1)]
    clients = []

    def make(embedder=None, connect=None):
        stores = {}
        services = Services(embedder or FakeEmbedder(dim=4, digest="d1"), connect=connect,
                            store_factory=lambda pid: stores.setdefault(pid, InMemoryVectorStore()))
        jobs = IndexJobs(services, lock_dir=tmp_path / "locks")
        client = TestClient(create_app(services, jobs))
        client.__enter__()
        clients.append(client)
        services.wait_for_warmup()
        return client, services, jobs
    make.ids, make.repos, make.tmp, make.clients = ids, repos, tmp_path, clients
    yield make
    for c in clients:
        c.__exit__(None, None, None)


def start(client, pid):
    return client.post(f"/projects/{pid}/index")


def status(client, pid):
    return client.get(f"/projects/{pid}/index").json()


# ---- the happy path

def test_start_answers_202_and_the_job_ends_done_with_its_progress(env):
    client, services, jobs = env()
    r = start(client, env.ids[0])
    assert r.status_code == 202 and r.json()["state"] in ("running", "done")
    jobs.wait(env.ids[0])
    s = status(client, env.ids[0])
    assert s["state"] == "done" and s["project_id"] == env.ids[0]
    assert s["files_done"] == s["files_total"] == 3 and s["message"].startswith("done: 3 files")
    assert services.store_for(env.ids[0]).count() > 0
    conn = db.get_connection()
    assert chunk_store.all_ids(conn, env.ids[0]), "the chunks are in SQLite"
    conn.close()


def test_a_project_nobody_started_is_idle(env):
    client, _, _ = env()
    assert status(client, env.ids[0]) == {"state": "idle", "project_id": env.ids[0]}


def test_the_job_can_be_started_again_when_it_is_over(env):
    client, services, jobs = env()
    start(client, env.ids[0])
    jobs.wait(env.ids[0])
    assert start(client, env.ids[0]).status_code == 202
    jobs.wait(env.ids[0])
    assert status(client, env.ids[0])["state"] == "done"


def test_the_endpoints_are_plain_def_functions(env):
    from routes_index import router                                # this FastAPI version hides included routes behind a wrapper, so look at the router itself
    routes = [r for r in router.routes if r.path.startswith("/projects")]
    assert len(routes) == 3 and not any(inspect.iscoroutinefunction(r.endpoint) for r in routes)


# ---- its own connection

def test_the_worker_uses_its_own_connection_on_its_own_thread_and_closes_it(env):
    made = []

    class Spy:
        def __init__(self, real):
            self.real, self.closed, self.thread = real, False, threading.get_ident()

        def __getattr__(self, name):
            return getattr(self.real, name)

        def close(self):
            self.closed = True
            self.real.close()

    def connect():
        made.append(Spy(db.get_connection(shared_across_threads=True)))
        return made[-1]
    client, _, jobs = env(connect=connect)
    made.clear()
    start(client, env.ids[0])
    jobs.wait(env.ids[0])
    workers = [c for c in made if c.thread != threading.get_ident()]
    assert len(workers) >= 1 and all(c.closed for c in made), "a connection was made on another thread than the test's, and every connection is closed"


# ---- progress, cancel

def test_progress_shows_while_the_run_is_in_the_middle_of_a_file(env):
    gate = Gate()
    client, _, jobs = env(gate)
    start(client, env.ids[0])
    assert gate.entered.wait(5)
    s = status(client, env.ids[0])
    assert s["state"] == "running" and s["files_done"] == 0 and s["files_total"] == 3
    gate.release.set()
    jobs.wait(env.ids[0])
    assert status(client, env.ids[0])["files_done"] == 3


def test_cancel_stops_between_files_and_says_so(env):
    gate = Gate()
    client, services, jobs = env(gate)
    start(client, env.ids[0])
    assert gate.entered.wait(5)
    r = client.post(f"/projects/{env.ids[0]}/index/cancel")
    assert r.status_code == 202 and r.json()["state"] == "cancelling"
    gate.release.set()                                             # file 1 finishes cleanly; file 2 never starts
    jobs.wait(env.ids[0])
    s = status(client, env.ids[0])
    assert s["state"] == "cancelled" and s["files_done"] == 1 and s["files_total"] == 3 and "cancelled" in s["message"]
    start(client, env.ids[0])                                      # the next run finishes the work
    jobs.wait(env.ids[0])
    assert status(client, env.ids[0])["state"] == "done"


def test_a_cancelled_run_deletes_and_sweeps_nothing(env, tmp_path):
    embedder = FakeEmbedder(dim=4, digest="d1")
    store = InMemoryVectorStore()
    repo = write_repo(tmp_path / "solo", ("a.py", "b.py"))
    conn = db.get_connection()
    pid = db.create_project("solo", str(repo))
    first = index_project(conn, pid, str(repo), embedder, store, lock_dir=tmp_path / "locks")
    assert first.stopped is None
    (repo / "b.py").unlink()                                       # b.py vanished: a COMPLETED run would delete its rows
    write_repo(repo, ("c.py",))
    calls = []
    cancelled = index_project(conn, pid, str(repo), embedder, store, lock_dir=tmp_path / "locks", cancel=lambda: calls.append(1) or True)
    assert cancelled.stopped is not None and cancelled.stopped.kind == "cancelled" and cancelled.stopped.files_done == 0
    assert cancelled.deleted_files == 0 and "b.py" in chunk_store.file_hashes(conn, pid), "a stopped run never deletes"
    assert calls == [1], "the flag is checked before a file, and the run stops at the first yes"
    done = index_project(conn, pid, str(repo), embedder, store, lock_dir=tmp_path / "locks")
    assert done.stopped is None and "b.py" not in chunk_store.file_hashes(conn, pid) and "c.py" in chunk_store.file_hashes(conn, pid)
    conn.close()


def test_cancel_with_nothing_running_is_a_409(env):
    client, _, _ = env()
    r = client.post(f"/projects/{env.ids[0]}/index/cancel")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_index_running"


def test_cancel_after_the_job_is_over_is_a_409_and_changes_nothing(env):
    client, _, jobs = env()
    start(client, env.ids[0])
    jobs.wait(env.ids[0])
    r = client.post(f"/projects/{env.ids[0]}/index/cancel")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_index_running"
    assert status(client, env.ids[0])["state"] == "done", "a finished job stays done"


# ---- one job per project

def test_a_second_start_while_one_runs_is_refused_at_once(env):
    gate = Gate()
    client, _, jobs = env(gate)
    start(client, env.ids[0])
    assert gate.entered.wait(5)
    r = start(client, env.ids[0])
    assert r.status_code == 409 and r.json()["error"]["code"] == "index_already_running"
    assert status(client, env.ids[0])["state"] == "running", "the first job is untouched"
    gate.release.set()
    jobs.wait(env.ids[0])
    assert status(client, env.ids[0])["state"] == "done"


def test_two_projects_can_be_indexed_side_by_side(env):
    gate = Gate()
    client, _, jobs = env(gate)
    start(client, env.ids[0])
    assert gate.entered.wait(5)
    assert start(client, env.ids[1]).status_code == 202
    assert status(client, env.ids[0])["state"] == "running" and status(client, env.ids[1])["state"] in ("running", "done")
    gate.release.set()
    jobs.wait(env.ids[0])
    jobs.wait(env.ids[1])
    assert status(client, env.ids[0])["state"] == status(client, env.ids[1])["state"] == "done"


def test_a_run_in_another_process_is_found_through_the_file_lock(env):
    client, _, jobs = env()
    import fcntl                                                   # what another backend process would hold: the OS lock only, not this process's registry
    (env.tmp / "locks").mkdir(exist_ok=True)
    handle = open(env.tmp / "locks" / f"index-{env.ids[0]}.lock", "a+")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert start(client, env.ids[0]).status_code == 202
        jobs.wait(env.ids[0])
        s = status(client, env.ids[0])
        assert s["state"] == "failed" and s["error"]["code"] == "index_already_running"
    finally:
        handle.close()
    assert start(client, env.ids[0]).status_code == 202            # the failed job does not block the next start
    jobs.wait(env.ids[0])
    assert status(client, env.ids[0])["state"] == "done"


# ---- things that go wrong

def test_an_unknown_project_is_404_and_starts_nothing(env):
    client, _, jobs = env()
    r = start(client, 999)
    assert r.status_code == 404 and r.json()["error"]["code"] == "project_not_found"
    assert status(client, 999) == {"state": "idle", "project_id": 999}


def test_a_missing_repo_folder_is_409_and_starts_nothing(env):
    client, _, _ = env()
    import shutil
    shutil.rmtree(env.repos[0])
    r = start(client, env.ids[0])
    assert r.status_code == 409 and r.json()["error"]["code"] == "repo_not_found"
    assert str(env.repos[0]) not in r.text, "the path is not repeated back"
    assert status(client, env.ids[0])["state"] == "idle"


def test_ollama_down_ends_the_job_stopped_with_a_fixed_message(env, caplog):
    class Down(FakeEmbedder):
        def warmup(self):
            raise OllamaUnavailable("connection refused to http://localhost:11434/api/embed")
    client, _, jobs = env(Down(dim=4))
    with caplog.at_level("INFO"):
        start(client, env.ids[0])
        jobs.wait(env.ids[0])
    s = status(client, env.ids[0])
    assert s["state"] == "stopped" and "Ollama" in s["message"] and "localhost" not in str(s)
    assert "localhost:11434" in caplog.text, "the real reason is in the log"


def test_a_bug_in_the_worker_ends_the_job_failed_and_frees_the_project(env, caplog):
    class Boom(FakeEmbedder):
        def embed_documents(self, texts, ids=None):
            raise RuntimeError("secret detail at /Users/someone/x.py")
    client, _, jobs = env(Boom(dim=4, digest="d1"))
    with caplog.at_level("ERROR"):
        start(client, env.ids[0])
        jobs.wait(env.ids[0])
    s = status(client, env.ids[0])
    assert s["state"] == "failed" and s["error"]["code"] == "internal_error" and "secret" not in str(s) and "/Users/" not in str(s)
    assert "secret detail" in caplog.text and "Traceback" in caplog.text
    assert start(client, env.ids[0]).status_code == 202, "the project is free again"


def test_skipped_files_are_reported_by_path_and_reason(env):
    class Picky(FakeEmbedder):
        def __init__(self):
            super().__init__(dim=4, digest="d1", max_chars=150)
    (env.repos[0] / "big.py").write_text("def big():\n    return '" + "x" * 500 + "'\n")
    client, _, jobs = env(Picky())
    start(client, env.ids[0])
    jobs.wait(env.ids[0])
    s = status(client, env.ids[0])
    assert s["state"] == "done" and [k["path"] for k in s["skipped"]] == ["big.py"] and "too long" in s["skipped"][0]["reason"].lower()


# ---- shutdown

def test_leaving_the_app_cancels_running_jobs_and_waits_for_the_worker(env):
    gate = Gate()
    client, _, jobs = env(gate)
    start(client, env.ids[0])
    assert gate.entered.wait(5)                                    # file 1 is blocked inside the embedder
    timer = threading.Timer(0.3, gate.release.set)                 # released WHILE the app is shutting down
    timer.start()
    env.clients.remove(client)
    client.__exit__(None, None, None)                              # must cancel the job, then wait for the worker
    timer.join()
    assert not jobs.is_running(env.ids[0]), "shutdown waited for the worker"
    s = jobs.status(env.ids[0])
    assert s["state"] == "cancelled" and s["files_done"] == 1, "shutdown asked the job to stop: file 1 finished, files 2 and 3 never started"
