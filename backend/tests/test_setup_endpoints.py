"""Task: first run, part 2: the backend tells the window WHY the embedder is not ready, and can download the missing model.

What is promised:
- `GET /health` carries `problem`: None while the embedder is ready or still warming, `ollama_unavailable`, `model_not_found` or `embedding_error` when it is
  not (the free-text `detail` stays, for people);
- `POST /setup/pull-model` starts the download of the embedder's OWN model and answers 202 with the status at once; it takes no model name: a body is ignored
  and the download is of the same model whatever the body says; `GET /setup/pull-model` reads the status;
- when the download succeeds the warm-up is tried again by itself and `/health` turns ready, without a restart and without waiting for the 5 second retry gap;
- a setup that has no Ollama behind it (tests, a different embedder) answers 409 `pull_not_available`, not a crash;
- both endpoints need the token like every other address.
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

import db
from app_for_tests import create_app
from embedding import FakeEmbedder
from embedding.errors import BadResponse, EmbeddingError, ModelNotFound, OllamaUnavailable
from model_pull import ModelPuller
from services import Services
from vectorstore import InMemoryVectorStore

MODEL = "fake-model:1b"


class Embedder(FakeEmbedder):
    """Fails its warm-up with `error` until `available` is set (as if Ollama were off, or the model not yet pulled)."""
    model = MODEL

    def __init__(self, error=None):
        super().__init__(dim=4, digest="d1")
        self.error, self.available = error, error is None

    def warmup(self):
        if not self.available:
            raise self.error
        super().warmup()


class Script:
    """urlopen stand-in for the puller: plays Ollama's lines; `on_success` runs just before `success` (the model "arrives")."""

    def __init__(self, items, on_success=None):
        self.items, self.on_success, self.requests = items, on_success, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outer = self

        class Reply:
            def __enter__(self):
                def gen():
                    for item in outer.items:
                        if item.get("status") == "success" and outer.on_success:
                            outer.on_success()
                        yield (json.dumps(item) + "\n").encode()
                return gen()

            def __exit__(self, *exc):
                return False
        return Reply()


GOOD = [{"status": "pulling manifest"}, {"status": "pulling a", "digest": "sha256:a", "total": 100, "completed": 100}, {"status": "success"}]


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "setup.db"))


@pytest.fixture
def clients():
    made = []
    yield made
    for c in made:
        c.__exit__(None, None, None)


def start(clients, embedder, opener=None, with_puller=True):
    services = Services(embedder, store_factory=lambda pid: InMemoryVectorStore(), retry_after=3600.0)          # a long gap: a retry that waits for it would never finish
    if with_puller:
        services.puller = ModelPuller(MODEL, opener=opener or Script(GOOD), on_done=services.retry_warmup)
    client = TestClient(create_app(services, auto_sync=False))
    client.__enter__()
    clients.append(client)
    services.wait_for_warmup()
    client.services = services
    return client


# ---- health says why

@pytest.mark.parametrize("error,problem", [
    (OllamaUnavailable("Ollama is not running"), "ollama_unavailable"),
    (ModelNotFound("no such model"), "model_not_found"),
    (BadResponse("garbage"), "embedding_error"),
    (EmbeddingError("something else"), "embedding_error"),
])
def test_health_names_the_problem(db_file, clients, error, problem):
    body = start(clients, Embedder(error)).get("/health").json()
    assert body["embedder"] == "degraded" and body["problem"] == problem and body["detail"]


def test_health_has_no_problem_when_ready(db_file, clients):
    body = start(clients, Embedder()).get("/health").json()
    assert body["embedder"] == "ready" and body["problem"] is None


def test_health_has_no_problem_while_warming(db_file, clients):
    release = threading.Event()

    class Slow(FakeEmbedder):
        def warmup(self):
            release.wait(5)
            super().warmup()
    services = Services(Slow(dim=4, digest="d1"), store_factory=lambda pid: InMemoryVectorStore())
    client = TestClient(create_app(services, auto_sync=False))
    clients.append(client)
    client.__enter__()
    try:
        body = client.get("/health").json()
        assert body["embedder"] == "warming" and body["problem"] is None
    finally:
        release.set()
        services.wait_for_warmup()


# ---- the pull

def test_the_pull_starts_in_the_background_and_answers_at_once(db_file, clients):
    client = start(clients, Embedder(ModelNotFound("no such model")))
    r = client.post("/setup/pull-model")
    assert r.status_code == 202
    assert r.json()["model"] == MODEL and r.json()["state"] in ("pulling", "done")
    client.services.puller.wait(5)
    status = client.get("/setup/pull-model").json()
    assert status["state"] == "done" and status["percent"] == 100


def test_the_status_before_any_pull_is_idle(db_file, clients):
    status = start(clients, Embedder(ModelNotFound("x"))).get("/setup/pull-model").json()
    assert status["state"] == "idle" and status["model"] == MODEL


def test_the_caller_cannot_choose_the_model(db_file, clients):
    opener = Script(GOOD)
    client = start(clients, Embedder(ModelNotFound("x")), opener)
    for body in ({"model": "evil:latest"}, {"name": "evil"}, {"model": ["a"]}):
        client.post("/setup/pull-model", json=body)
        client.services.puller.wait(5)
    assert opener.requests and all(json.loads(r.data)["model"] == MODEL for r in opener.requests)


def test_a_finished_pull_makes_health_ready_without_a_restart(db_file, clients):
    embedder = Embedder(ModelNotFound("no such model"))
    client = start(clients, embedder, Script(GOOD, on_success=lambda: setattr(embedder, "available", True)))
    assert client.get("/health").json()["problem"] == "model_not_found"
    client.post("/setup/pull-model")
    client.services.puller.wait(5)
    body = client.get("/health").json()
    assert body["embedder"] == "ready" and body["problem"] is None and body["model"]


def test_a_pull_that_fails_leaves_health_as_it_was(db_file, clients):
    client = start(clients, Embedder(ModelNotFound("x")), Script([{"error": "no space left on device"}]))
    client.post("/setup/pull-model")
    client.services.puller.wait(5)
    assert client.get("/setup/pull-model").json()["error"] == "no space left on device"
    assert client.get("/health").json()["problem"] == "model_not_found"


def test_a_pull_that_succeeds_but_leaves_the_model_unusable_does_not_crash(db_file, clients):
    client = start(clients, Embedder(ModelNotFound("x")), Script(GOOD))          # the model never becomes `available`
    client.post("/setup/pull-model")
    client.services.puller.wait(5)
    assert client.get("/setup/pull-model").json()["state"] == "done"
    assert client.get("/health").json()["problem"] == "model_not_found"


def test_retry_warmup_does_not_wait_for_the_retry_gap(db_file, clients):
    embedder = Embedder(OllamaUnavailable("off"))
    client = start(clients, embedder)
    embedder.available = True
    client.services.retry_warmup()                                                # retry_after is an hour: ensure_warm alone would refuse
    assert client.get("/health").json()["embedder"] == "ready"


def test_retry_warmup_swallows_a_problem_that_is_still_there(db_file, clients):
    client = start(clients, Embedder(OllamaUnavailable("still off")))
    client.services.retry_warmup()
    assert client.get("/health").json()["problem"] == "ollama_unavailable"


def test_a_setup_without_ollama_says_so(db_file, clients):
    client = start(clients, Embedder(), with_puller=False)
    for r in (client.post("/setup/pull-model"), client.get("/setup/pull-model")):
        assert r.status_code == 409 and r.json()["error"]["code"] == "pull_not_available"


def test_the_real_default_setup_pulls_the_real_model_name_from_the_embedder():
    import services as services_module
    made = services_module.default_services()
    assert made.puller is not None
    assert made.puller.status()["model"] == made.embedder.model == "qwen3-embedding:0.6b"
    assert made.puller._url == made.embedder.base_url + "/api/pull"
    assert made.puller._on_done == made.retry_warmup


# ---- the token

def test_both_endpoints_need_the_token(db_file):
    from main import create_app as real_create_app
    from security import Security
    token = "k" * 40
    services = Services(Embedder(), store_factory=lambda pid: InMemoryVectorStore())
    services.puller = ModelPuller(MODEL, opener=Script(GOOD))
    with TestClient(real_create_app(services, auto_sync=False, security=Security(token=token, allowed_origins=())), base_url="http://127.0.0.1:8123") as client:
        assert client.post("/setup/pull-model").status_code == 401
        assert client.get("/setup/pull-model").status_code == 401
        assert client.post("/setup/pull-model", headers={"x-clank-token": token}).status_code == 202
