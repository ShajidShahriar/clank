"""Task 8.1: the app's services (embedder, vector stores, database connections) are built by a factory and handed out through FastAPI dependencies.

What is promised:
- importing `main` touches nothing: no data folder, no database, no Ollama (no import-time globals);
- startup warms the model in the BACKGROUND (a failed warm-up takes about 7 s with the real client) and is never fatal: with Ollama off the app starts
  "degraded" and `/health` still answers;
- a request that needs the embedder retries the warm-up, but not more often than every `retry_after` seconds (otherwise every request would wait 7 s);
- every request gets its OWN database connection, closed afterwards; a vector store is made once per project;
- tests swap everything through the `Services` object or `app.dependency_overrides`, with the fakes;
- when the schema version changes and chunk tables are dropped, the vector stores of all projects are cleared (the caller's duty, see `db.init_db`).
"""
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

import db
from embedding import FakeEmbedder
from embedding.errors import OllamaUnavailable
from app_for_tests import create_app
from services import Services, get_conn, get_ready_embedder, get_services
from vectorstore import InMemoryVectorStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FlakyEmbedder(FakeEmbedder):
    """warmup() raises OllamaUnavailable while `up` is False."""

    def __init__(self, up=False):
        super().__init__(dim=4, digest="d1")
        self.up = up

    def warmup(self):
        self.warmup_count += 1
        if not self.up:
            raise OllamaUnavailable("Ollama is not running")


@pytest.fixture
def clock():
    return Clock()


def make_services(embedder=None, clock=None, **kw):
    stores = []

    def factory(project_id):
        stores.append(project_id)
        return InMemoryVectorStore()
    s = Services(embedder or FakeEmbedder(dim=4, digest="d1"), store_factory=factory, clock=clock or Clock(), **kw)
    s.stores_made = stores
    return s


def started(services):
    """A TestClient inside its lifespan, after the background warm-up has finished."""
    client = TestClient(create_app(services))
    client.__enter__()
    services.wait_for_warmup()
    return client


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "svc.db"))


# ---- no import-time globals

def test_importing_main_touches_no_data_folder_database_or_ollama(tmp_path):
    folder = tmp_path / "never-created"
    env = {**os.environ, "CLANK_DATA_DIR": str(folder), "PYTHONDONTWRITEBYTECODE": "1"}
    code = "import main"
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert not folder.exists(), "importing main created the data folder"


# ---- startup

def test_startup_warms_the_model_and_health_says_ready(db_file):
    embedder = FakeEmbedder(dim=4, digest="d1")
    with started(make_services(embedder)) as client:
        body = client.get("/health").json()
    assert embedder.warmup_count == 1
    assert body == {"status": "ok", "embedder": "ready", "model": "fake-hash-4@d1", "detail": None, "problem": None}


def test_the_app_starts_degraded_when_ollama_is_off_and_health_still_answers(db_file):
    embedder = FlakyEmbedder(up=False)
    with started(make_services(embedder)) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["embedder"] == "degraded" and "Ollama is not running" in r.json()["detail"] and r.json()["model"] is None


def test_startup_does_not_wait_for_the_warm_up(db_file):
    release = threading.Event()

    class Slow(FakeEmbedder):
        def warmup(self):
            release.wait(5)
            super().warmup()
    services = make_services(Slow(dim=4, digest="d1"))
    client = TestClient(create_app(services))
    client.__enter__()                                             # returns while the warm-up is still blocked
    try:
        assert client.get("/health").json()["embedder"] == "warming"
        release.set()
        services.wait_for_warmup()
        assert client.get("/health").json()["embedder"] == "ready"
    finally:
        release.set()
        client.__exit__(None, None, None)


# ---- retrying on a request

def needs_embedder_app(services):
    app = create_app(services)

    @app.get("/needs-embedder")
    def needs(embedder=Depends(get_ready_embedder)):
        return {"model": embedder.model_name}
    return app


def test_a_request_retries_the_warm_up_and_recovers_when_ollama_comes_back(db_file, clock):
    embedder = FlakyEmbedder(up=False)
    services = make_services(embedder, clock, retry_after=5.0)
    with TestClient(needs_embedder_app(services)) as client:
        services.wait_for_warmup()
        embedder.up = True
        clock.now += 6
        assert client.get("/needs-embedder").json() == {"model": "fake-hash-4@d1"}
        assert client.get("/health").json()["embedder"] == "ready"
    assert embedder.warmup_count == 2


def test_a_request_while_ollama_is_still_off_gets_a_503(db_file, clock):
    services = make_services(FlakyEmbedder(up=False), clock, retry_after=5.0)
    with TestClient(needs_embedder_app(services)) as client:
        services.wait_for_warmup()
        clock.now += 6
        r = client.get("/needs-embedder")
        assert r.status_code == 503 and r.json()["error"]["code"] == "ollama_unavailable"


def test_retries_are_not_made_more_often_than_retry_after(db_file, clock):
    embedder = FlakyEmbedder(up=False)
    services = make_services(embedder, clock, retry_after=5.0)
    with TestClient(needs_embedder_app(services)) as client:
        services.wait_for_warmup()
        assert embedder.warmup_count == 1
        for _ in range(3):
            clock.now += 1
            assert client.get("/needs-embedder").status_code == 503
        assert embedder.warmup_count == 1, "within the pause no new warm-up is tried: the stored error is raised at once"
        clock.now += 5
        assert client.get("/needs-embedder").status_code == 503
        assert embedder.warmup_count == 2


def test_a_warm_embedder_is_never_warmed_again(db_file, clock):
    embedder = FakeEmbedder(dim=4, digest="d1")
    services = make_services(embedder, clock)
    with TestClient(needs_embedder_app(services)) as client:
        services.wait_for_warmup()
        for _ in range(3):
            client.get("/needs-embedder")
    assert embedder.warmup_count == 1


# ---- connections and stores

class SpyConnection:
    """Wraps a real connection and remembers whether close() was called (a closed sqlite connection used from another thread fails for a different reason)."""

    def __init__(self, real):
        self.real, self.closed = real, False

    def execute(self, *args):
        return self.real.execute(*args)

    def close(self):
        self.closed = True
        self.real.close()


def spying_services(spied):
    return make_services(connect=lambda: spied.append(SpyConnection(db.get_connection(shared_across_threads=True))) or spied[-1])


def test_every_request_gets_its_own_connection_and_it_is_closed_afterwards(db_file):
    spied, seen = [], []
    app = create_app(spying_services(spied), auto_sync=False)           # the sweep at startup opens connections of its own; these tests count a request's

    @app.get("/db")
    def use(conn=Depends(get_conn)):
        seen.append(conn)
        return {"projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]}
    with TestClient(app) as client:
        spied.clear()                                              # the startup work is not the request
        assert client.get("/db").json() == {"projects": 0}
        assert [c.closed for c in spied] == [True]
        client.get("/db")
    assert len(spied) == 2 and spied[0] is not spied[1] and all(c.closed for c in spied) and seen == spied


def test_the_connection_is_closed_even_when_the_request_fails(db_file):
    spied = []
    app = create_app(spying_services(spied), auto_sync=False)           # the sweep at startup opens connections of its own; these tests count a request's

    @app.get("/boom")
    def boom(conn=Depends(get_conn)):
        raise RuntimeError("boom")
    with TestClient(app, raise_server_exceptions=False) as client:
        spied.clear()
        assert client.get("/boom").status_code == 500
    assert len(spied) == 1 and spied[0].closed


def test_the_default_connection_can_be_used_from_a_thread_other_than_the_one_that_made_it(db_file):
    """FastAPI may run a dependency and the endpoint on different worker threads; one request uses its connection one step at a time, so that is safe."""
    services = make_services()
    conn = services.connect()
    errors = []

    def use():
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=use)
    thread.start()
    thread.join()
    conn.close()
    assert errors == []


def test_one_vector_store_per_project_made_once(db_file):
    services = make_services()
    a, b = services.store_for(1), services.store_for(2)
    assert services.store_for(1) is a and a is not b
    assert services.stores_made == [1, 2]


def test_dependency_overrides_replace_the_services(db_file):
    other = make_services(FakeEmbedder(dim=4, digest="other"))
    app = create_app(make_services())

    @app.get("/who")
    def who(services: Services = Depends(get_services)):
        return {"model": services.embedder.model_name}
    app.dependency_overrides[get_services] = lambda: other
    with TestClient(app) as client:
        assert client.get("/who").json() == {"model": "fake-hash-4@other"}


# ---- startup housekeeping

def test_a_schema_reset_clears_the_vector_stores_of_every_project(db_file):
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('a', '/a', 'now'), ('b', '/b', 'now')")
    conn.execute("INSERT INTO files (project_id, rel_path, hash, chunker_version) VALUES (1, 'x.py', 'h', 'v')")
    conn.execute("PRAGMA user_version = 1")                       # an old schema: init_db will drop the chunk tables
    conn.commit()
    conn.close()
    services = make_services()
    for pid in (1, 2):
        services.store_for(pid).upsert([f"c{pid}"], [[1.0, 0.0, 0.0, 0.0]])
    with TestClient(create_app(services)):
        services.wait_for_warmup()
    assert services.store_for(1).count() == 0 and services.store_for(2).count() == 0


def test_no_reset_leaves_the_vector_stores_alone(db_file):
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('a', '/a', 'now')")
    conn.commit()
    conn.close()
    services = make_services()
    services.store_for(1).upsert(["c1"], [[1.0, 0.0, 0.0, 0.0]])
    with TestClient(create_app(services)):
        services.wait_for_warmup()
    assert services.store_for(1).count() == 1
