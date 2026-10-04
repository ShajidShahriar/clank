"""Task 8.2: every error the API returns has ONE shape, `{"error": {"code": "...", "message": "..."}}`, and no stack trace or internal detail leaks.

- "this project has not been indexed" is its own class (`NotIndexed`, a subclass of `IndexOutOfDate`), so a code is never found by matching message text;
- 409: not indexed, index out of date, index already running; 503: Ollama unavailable, model not found; 502: Ollama answered nonsense;
- 422: a request that fails validation (blank question, bad k: the fields themselves are 8.5's), 404/405: unknown route or method;
- anything unexpected is 500 `internal_error` with a fixed message; the real error goes to the log, never to the caller.
"""
import logging

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

import db
from embedding import FakeEmbedder
from embedding.errors import BadResponse, EmbeddingTooLong, ModelNotFound, OllamaUnavailable
from indexing import IndexAlreadyRunning
from main import create_app
from search import IndexOutOfDate, NotIndexed, search
from services import Services, get_ready_embedder
from vectorstore import InMemoryVectorStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "err.db"))
    services = Services(FakeEmbedder(dim=4, digest="d1"), store_factory=lambda pid: InMemoryVectorStore())
    app = create_app(services)

    @app.get("/raise/{name}")
    def raise_it(name: str):
        raise ERRORS[name]

    class Ask(BaseModel):
        question: str = Field(min_length=1)
        k: int = Field(default=10, ge=1, le=50)

    @app.post("/ask")
    def ask(body: Ask):
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as c:
        services.wait_for_warmup()
        yield c


ERRORS = {
    "not_indexed": NotIndexed("this project has not been indexed yet: run indexing first"),
    "out_of_date": IndexOutOfDate("the index is out of date: it was built for a@1 but the embedder is b@2: re-index this project"),
    "running": IndexAlreadyRunning("locked by pid 123 at /Users/someone/.clank/locks/1.lock"),
    "ollama": OllamaUnavailable("connection refused to http://localhost:11434/api/embed"),
    "model": ModelNotFound("model 'qwen3-embedding:0.6b' not found"),
    "bad": BadResponse("Ollama's answer has no 'embeddings' list: {'secret': 'raw reply'}"),
    "long": EmbeddingTooLong("too long: a#0", ["a#0"]),
    "bug": RuntimeError("secret internal detail at /Users/someone/project/file.py line 12"),
}


@pytest.mark.parametrize("name, status, code", [
    ("not_indexed", 409, "not_indexed"),
    ("out_of_date", 409, "index_out_of_date"),
    ("running", 409, "index_already_running"),
    ("ollama", 503, "ollama_unavailable"),
    ("model", 503, "model_not_found"),
    ("bad", 502, "embedding_error"),
    ("long", 502, "embedding_error"),
    ("bug", 500, "internal_error"),
])
def test_each_error_has_its_status_and_code_and_the_one_json_shape(client, name, status, code):
    r = client.get(f"/raise/{name}")
    assert r.status_code == status
    body = r.json()
    assert list(body) == ["error"] and sorted(body["error"]) == ["code", "message"], "exactly {error: {code, message}}"
    assert body["error"]["code"] == code and body["error"]["message"]


@pytest.mark.parametrize("name", ["running", "ollama", "model", "bad", "long", "bug"])
def test_no_internal_detail_leaks_into_the_message(client, name):
    text = client.get(f"/raise/{name}").text
    for secret in ("/Users/", "localhost:11434", "secret", "raw reply", "Traceback", "line 12", "pid 123", "RuntimeError"):
        assert secret not in text, f"{secret!r} leaked: {text}"


def test_the_messages_tell_the_user_what_to_do(client):
    assert "index" in client.get("/raise/not_indexed").json()["error"]["message"].lower()
    assert "ollama" in client.get("/raise/ollama").json()["error"]["message"].lower()
    assert "ollama pull qwen3-embedding:0.6b" in client.get("/raise/model").json()["error"]["message"]
    assert "re-index" in client.get("/raise/out_of_date").json()["error"]["message"], "the out-of-date message is written for people and is passed on"


def test_an_unexpected_error_is_logged_with_its_traceback_but_not_returned(client, caplog):
    with caplog.at_level(logging.ERROR):
        client.get("/raise/bug")
    assert "secret internal detail" in caplog.text and "Traceback" in caplog.text


def test_a_failed_validation_is_422_in_the_same_shape(client):
    for body in ({"question": ""}, {"question": "ok", "k": 0}, {"question": "ok", "k": 51}, {}, {"question": "ok", "k": "many"}):
        r = client.post("/ask", json=body)
        assert r.status_code == 422, body
        assert list(r.json()) == ["error"] and r.json()["error"]["code"] == "invalid_request"
    assert client.post("/ask", json={"question": "fine", "k": 5}).status_code == 200


def test_the_422_message_names_the_field_and_does_not_echo_the_input(client):
    message = client.post("/ask", json={"question": "ok", "k": "my-private-text"}).json()["error"]["message"]
    assert message.count("k:") == 1 and "my-private-text" not in message, "the bad VALUE is the private text here, so an echo would show it"


def test_an_unknown_route_and_a_wrong_method_use_the_same_shape(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


def test_the_embedder_dependency_turns_a_dead_ollama_into_a_503(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "err.db"))

    class Down(FakeEmbedder):
        def warmup(self):
            raise OllamaUnavailable("down")
    services = Services(Down(dim=4), store_factory=lambda pid: InMemoryVectorStore(), retry_after=0)
    app = create_app(services)

    @app.get("/needs")
    def needs(embedder=Depends(get_ready_embedder)):
        return {}
    with TestClient(app) as c:
        services.wait_for_warmup()
        r = c.get("/needs")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ollama_unavailable"


# ---- NotIndexed is its own class

def test_searching_a_project_that_was_never_indexed_raises_NotIndexed_and_an_old_index_does_not(conn):
    embedder = FakeEmbedder(dim=4, digest="new")
    empty = InMemoryVectorStore()
    with pytest.raises(NotIndexed):
        search(conn, 1, embedder, empty, "q")
    with pytest.raises(IndexOutOfDate):                            # still catchable as the old class
        search(conn, 1, embedder, empty, "q")
    old = InMemoryVectorStore()
    old.set_signature("fake-hash-4@old", 4)
    with pytest.raises(IndexOutOfDate) as caught:
        search(conn, 1, embedder, old, "q")
    assert not isinstance(caught.value, NotIndexed), "a stale index must not look like a never-indexed one"
