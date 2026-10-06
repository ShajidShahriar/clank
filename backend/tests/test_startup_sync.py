"""Task 8.4: an incremental index at app start, only for projects that already have an index.

- "has an index" means the project has at least one row in `files` (a cancelled first run counts: it continues; a project nobody ever indexed is NOT started
  on its own: that is the user's choice);
- projects are synced ONE AT A TIME, in id order, through the same job machinery as the Index button (progress, cancel, one job per project);
- if the model is not available at startup the whole sweep is skipped (it would fail the same way for every project); if it stops on an Ollama problem in the
  middle, the rest are not tried;
- a project whose folder is gone is skipped and the others go on; a user pressing Index on a project the sweep is working on gets the normal 409;
- leaving the app stops the sweep; a bug in the sweep never takes the app down.
"""
import threading

import pytest
from fastapi.testclient import TestClient

import chunk_store
import db
from embedding import FakeEmbedder
from embedding.errors import OllamaUnavailable
from indexing import index_project
from jobs import IndexJobs
from app_for_tests import create_app
from services import Services
from vectorstore import InMemoryVectorStore


class Gate(FakeEmbedder):
    def __init__(self):
        super().__init__(dim=4, digest="d1")
        self.entered = threading.Event()
        self.release = threading.Event()
        self.gated = False

    def embed_documents(self, texts, ids=None):
        if self.gated:
            self.entered.set()
            assert self.release.wait(10), "the test never released the gate"
        return super().embed_documents(texts, ids)


class Breakable(FakeEmbedder):
    """Works until `broken` is set; then embedding raises OllamaUnavailable (warm-up still works)."""

    def __init__(self):
        super().__init__(dim=4, digest="d1")
        self.broken = False

    def embed_documents(self, texts, ids=None):
        if self.broken:
            raise OllamaUnavailable("down in the middle")
        return super().embed_documents(texts, ids)


def write_repo(root, names):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_text(f"def {name[:-3]}_fn():\n    return '{name}'\n")
    return root


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Three projects; stores and the database outlive an app, like the real ones. `index_first(*ids)` indexes them BEFORE the app starts."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "sync.db"))
    db.init_db()
    repos = [write_repo(tmp_path / f"repo{i}", (f"f{i}a.py", f"f{i}b.py")) for i in (1, 2, 3)]
    ids = [db.create_project(f"p{i}", str(r)) for i, r in enumerate(repos, 1)]
    stores, clients = {}, []

    def store_for(pid):
        return stores.setdefault(pid, InMemoryVectorStore())

    def index_first(embedder, *pids):
        conn = db.get_connection()
        for pid in pids:
            index_project(conn, pid, repos[pid - 1], embedder, store_for(pid), lock_dir=tmp_path / "locks")
        conn.close()

    def make(embedder, **kw):
        services = Services(embedder, store_factory=store_for)
        jobs = IndexJobs(services, lock_dir=tmp_path / "locks")
        app = create_app(services, jobs, **kw)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        services.wait_for_warmup()
        return client, services, jobs
    world_ = type("World", (), {})()
    world_.ids, world_.repos, world_.tmp, world_.stores, world_.clients = ids, repos, tmp_path, stores, clients
    world_.index_first, world_.make = index_first, make
    yield world_
    for c in clients:
        c.__exit__(None, None, None)


def sync_status(client):
    return client.get("/startup-sync").json()


def wait_sync(client, timeout=10):
    client.app.state.startup_sync.wait(timeout)


def chunk_paths(pid):
    conn = db.get_connection()
    try:
        return set(chunk_store.file_hashes(conn, pid))
    finally:
        conn.close()


# ---- who is synced

def test_only_projects_that_already_have_an_index_are_synced(world):
    embedder = FakeEmbedder(dim=4, digest="d1")
    world.index_first(embedder, 1, 3)
    (world.repos[0] / "new.py").write_text("def new_fn():\n    return 1\n")
    client, _, jobs = world.make(embedder)
    wait_sync(client)
    assert "new.py" in chunk_paths(1), "the file added since the last run is indexed now"
    assert jobs.status(world.ids[0])["state"] == "done" and jobs.status(world.ids[2])["state"] == "done"
    assert jobs.status(world.ids[1]) == {"state": "idle", "project_id": world.ids[1]}, "never indexed: not started on its own"
    assert chunk_paths(2) == set()


def test_a_sync_embeds_only_what_changed(world):
    embedder = FakeEmbedder(dim=4, digest="d1")
    world.index_first(embedder, 1)
    before = embedder.text_count
    (world.repos[0] / "new.py").write_text("def new_fn():\n    return 1\n")
    client, _, _ = world.make(embedder)
    wait_sync(client)
    assert 0 < embedder.text_count - before <= 2, "only the new file's chunks were embedded, not the whole project"


def test_a_project_with_a_cancelled_first_run_continues(world):
    conn = db.get_connection()
    index_first_calls = []
    index_project(conn, world.ids[0], world.repos[0], FakeEmbedder(dim=4, digest="d1"), world.stores.setdefault(1, InMemoryVectorStore()),
                  lock_dir=world.tmp / "locks", cancel=lambda: bool(index_first_calls.append(1)) or len(index_first_calls) > 1)
    conn.close()
    assert len(chunk_paths(1)) == 1, "the first run was cancelled after one file"
    client, _, _ = world.make(FakeEmbedder(dim=4, digest="d1"))
    wait_sync(client)
    assert len(chunk_paths(1)) == 2


def test_nothing_runs_when_the_sync_is_switched_off(world):
    embedder = FakeEmbedder(dim=4, digest="d1")
    world.index_first(embedder, 1)
    client, _, jobs = world.make(embedder, auto_sync=False)
    assert jobs.status(world.ids[0])["state"] == "idle" and sync_status(client)["state"] == "off"


# ---- one at a time

def test_projects_are_synced_one_at_a_time_in_id_order(world):
    gate = Gate()
    world.index_first(gate, 1, 2)
    (world.repos[0] / "n1.py").write_text("def n1():\n    return 1\n")
    (world.repos[1] / "n2.py").write_text("def n2():\n    return 2\n")
    gate.gated = True
    client, _, jobs = world.make(gate)
    assert gate.entered.wait(5)
    assert jobs.status(world.ids[0])["state"] == "running"
    assert jobs.status(world.ids[1]) == {"state": "idle", "project_id": world.ids[1]}, "project 2 waits for project 1"
    assert sync_status(client) == {"state": "running", "projects": [world.ids[0], world.ids[1]], "reason": None}
    gate.release.set()
    wait_sync(client)
    assert jobs.status(world.ids[1])["state"] == "done" and sync_status(client)["state"] == "done"


def test_the_user_pressing_index_on_the_project_being_synced_gets_the_normal_409(world):
    gate = Gate()
    world.index_first(gate, 1)
    (world.repos[0] / "n1.py").write_text("def n1():\n    return 1\n")
    gate.gated = True
    client, _, jobs = world.make(gate)
    assert gate.entered.wait(5)
    r = client.post(f"/projects/{world.ids[0]}/index")
    assert r.status_code == 409 and r.json()["error"]["code"] == "index_already_running"
    gate.release.set()
    wait_sync(client)
    assert jobs.status(world.ids[0])["state"] == "done"


def test_when_the_user_started_a_project_first_the_sweep_waits_for_it_and_goes_on(world):
    gate = Gate()
    world.index_first(gate, 1, 2)
    (world.repos[0] / "n1.py").write_text("def n1():\n    return 1\n")
    gate.gated = True
    client, services, jobs = world.make(gate, auto_sync=False)
    assert client.post(f"/projects/{world.ids[0]}/index").status_code == 202
    assert gate.entered.wait(5)
    from startup_sync import StartupSync
    sync = StartupSync(services, jobs)
    sync.start()                                                   # the project is already running: not an error
    gate.release.set()
    sync.wait(10)
    assert sync.status()["state"] == "done" and jobs.status(world.ids[1])["state"] == "done"


# ---- when the model is not there

def test_the_sweep_is_skipped_when_the_model_is_not_available_at_startup(world):
    world.index_first(FakeEmbedder(dim=4, digest="d1"), 1)

    class Down(FakeEmbedder):
        def warmup(self):
            raise OllamaUnavailable("down")
    client, _, jobs = world.make(Down(dim=4, digest="d1"))
    wait_sync(client)
    s = sync_status(client)
    assert s["state"] == "skipped" and "Ollama" in s["reason"] and jobs.status(world.ids[0])["state"] == "idle"


def test_an_ollama_problem_in_the_middle_stops_the_rest(world):
    embedder = Breakable()
    world.index_first(embedder, 1, 2)
    (world.repos[0] / "n1.py").write_text("def n1():\n    return 1\n")
    (world.repos[1] / "n2.py").write_text("def n2():\n    return 2\n")
    embedder.broken = True
    client, _, jobs = world.make(embedder)
    wait_sync(client)
    assert jobs.status(world.ids[0])["state"] == "stopped"
    assert jobs.status(world.ids[1]) == {"state": "idle", "project_id": world.ids[1]}, "the same problem would stop project 2 too: not tried"
    assert sync_status(client)["state"] == "stopped" and "Ollama" in sync_status(client)["reason"]


# ---- other trouble

def test_a_project_whose_folder_is_gone_is_skipped_and_the_others_go_on(world):
    import shutil
    embedder = FakeEmbedder(dim=4, digest="d1")
    world.index_first(embedder, 1, 2)
    shutil.rmtree(world.repos[0])
    (world.repos[1] / "n2.py").write_text("def n2():\n    return 2\n")
    client, _, jobs = world.make(embedder)
    wait_sync(client)
    assert jobs.status(world.ids[0])["state"] == "idle" and jobs.status(world.ids[1])["state"] == "done"
    assert "n2.py" in chunk_paths(2) and sync_status(client)["state"] == "done"


def test_a_bug_in_the_sweep_never_takes_the_app_down(world, caplog):
    embedder = FakeEmbedder(dim=4, digest="d1")
    world.index_first(embedder, 1)
    services = Services(embedder, store_factory=lambda pid: world.stores.setdefault(pid, InMemoryVectorStore()))
    jobs = IndexJobs(services, lock_dir=world.tmp / "locks")

    def boom(project_id):
        raise RuntimeError("secret internal detail")
    jobs.start = boom
    with caplog.at_level("ERROR"):
        with TestClient(create_app(services, jobs)) as client:
            services.wait_for_warmup()
            client.app.state.startup_sync.wait(10)
            assert client.get("/health").status_code == 200
            s = sync_status(client)
    assert s["state"] == "failed" and "secret" not in str(s)
    assert "secret internal detail" in caplog.text and "Traceback" in caplog.text


# ---- leaving the app

def test_leaving_the_app_stops_the_sweep_before_the_next_project(world):
    gate = Gate()
    world.index_first(gate, 1, 2)
    (world.repos[0] / "n1.py").write_text("def n1():\n    return 1\n")
    (world.repos[1] / "n2.py").write_text("def n2():\n    return 2\n")
    gate.gated = True
    client, _, jobs = world.make(gate)
    assert gate.entered.wait(5)
    timer = threading.Timer(0.3, gate.release.set)
    timer.start()
    world.clients.remove(client)
    sync = client.app.state.startup_sync
    client.__exit__(None, None, None)
    timer.join()
    assert not sync.is_running() and not jobs.is_running(world.ids[0])
    assert jobs.status(world.ids[1]) == {"state": "idle", "project_id": world.ids[1]}, "project 2 was never started"


# ---- gaps the first mutation run found

def test_the_sweep_waits_for_a_slow_warm_up_instead_of_calling_it_unavailable(world):
    release = threading.Event()

    class SlowWarm(FakeEmbedder):
        def warmup(self):
            assert release.wait(10)
            super().warmup()
    embedder = SlowWarm(dim=4, digest="d1")
    world.index_first(FakeEmbedder(dim=4, digest="d1"), 1)
    (world.repos[0] / "n1.py").write_text("def n1():\n    return 1\n")
    services = Services(embedder, store_factory=lambda pid: world.stores.setdefault(pid, InMemoryVectorStore()))
    jobs = IndexJobs(services, lock_dir=world.tmp / "locks")
    with TestClient(create_app(services, jobs)) as client:         # no wait_for_warmup here: the model is still loading
        assert sync_status(client)["state"] == "running"
        release.set()
        wait_sync(client)
        assert sync_status(client)["state"] == "done" and "n1.py" in chunk_paths(1)


def test_a_project_that_stops_for_too_many_skipped_files_does_not_end_the_sweep(world):
    first = FakeEmbedder(dim=4, digest="d1")
    world.index_first(first, 1, 2)
    for i in range(6):                                             # six new files too long for the model: 4 new failures pass the limit of 3 and stop project 1
        (world.repos[0] / f"long{i}.py").write_text(f"def long{i}():\n    return '{'x' * 200}'\n")
    (world.repos[1] / "n2.py").write_text("def n2():\n    return 2\n")
    client, _, jobs = world.make(FakeEmbedder(dim=4, digest="d1", max_chars=100))
    wait_sync(client)
    one = jobs.status(world.ids[0])
    assert one["state"] == "stopped" and one["stopped_kind"] == "too_many_skips"
    assert jobs.status(world.ids[1])["state"] == "done" and "n2.py" in chunk_paths(2), "project 2 is still synced"
    assert sync_status(client)["state"] == "done"


def test_leaving_the_app_waits_for_the_sweep_thread_itself(world):
    embedder = FakeEmbedder(dim=4, digest="d1")
    world.index_first(embedder, 1, 2)
    client, _, jobs = world.make(embedder)
    wait_sync(client)
    sync = client.app.state.startup_sync
    sync._state = "running"                                        # restart the sweep so that it is slow to notice the stop
    original = jobs.status

    def slow_status(project_id):
        threading.Event().wait(0.4)
        return original(project_id)
    jobs.status = slow_status
    sync.start()
    threading.Event().wait(0.1)                                    # the sweep thread is now inside its slow step
    world.clients.remove(client)
    client.__exit__(None, None, None)
    assert not sync.is_running(), "leaving the app waited for the sweep thread to finish"


def test_a_project_deleted_while_the_sweep_runs_is_skipped_and_the_others_go_on(world):
    from jobs import ProjectNotFound
    embedder = FakeEmbedder(dim=4, digest="d1")
    world.index_first(embedder, 1, 2)
    (world.repos[1] / "n2.py").write_text("def n2():\n    return 2\n")
    services = Services(embedder, store_factory=lambda pid: world.stores.setdefault(pid, InMemoryVectorStore()))
    jobs = IndexJobs(services, lock_dir=world.tmp / "locks")
    real_start = jobs.start

    def start(project_id):
        if project_id == world.ids[0]:
            raise ProjectNotFound("deleted a moment ago")
        return real_start(project_id)
    jobs.start = start
    with TestClient(create_app(services, jobs)) as client:
        services.wait_for_warmup()
        client.app.state.startup_sync.wait(10)
        assert sync_status(client)["state"] == "done"
        assert jobs.status(world.ids[1])["state"] == "done" and "n2.py" in chunk_paths(2)
