"""Task: `GET/PUT /settings/llm`, `PUT /settings/llm/key`, `POST /settings/llm/test`.

- the choice (preset, address, model, budgets) is stored; the KEY is only ever in memory: it comes in through its own endpoint, is never returned, never logged
  and never written to the database; the desktop app pushes it after every start (it keeps it encrypted);
- a preset that takes no key (Ollama, LM Studio) never sends one, even if a key is held in memory;
- "test" makes one tiny real call to the configured service and reports the model and how long it took, or the same friendly error as an answer would;
- /answer follows the saved choice and switches when it changes (no restart).
"""
import inspect
import json
import logging

import pytest
from fastapi.testclient import TestClient

import db
from app_for_tests import create_app
from embedding import FakeEmbedder
from fake_llm_server import FakeLLMServer, good_reply
from hand_made import Question2D, make_world
from llm.settings import PRESETS
from services import Services
from vectorstore import InMemoryVectorStore

KEY = "gsk_" + "k3y" * 12


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "settings.db"))
    clients = []

    def make(environ=None, embedder=None, store=None):
        services = Services(embedder or FakeEmbedder(dim=4, digest="d1"), store_factory=lambda pid: store if store is not None else InMemoryVectorStore(), environ=environ or {})
        client = TestClient(create_app(services, auto_sync=False))
        client.__enter__()
        clients.append(client)
        services.wait_for_warmup()
        return client, services
    yield make
    for c in clients:
        c.__exit__(None, None, None)


def code(response):
    return response.json()["error"]["code"]


def custom(server, **extra):
    return {"preset": "custom", "base_url": server.base_url, "model": "test-model", **extra}


# ---- reading

def test_the_default_is_groq_with_no_key(env):
    client, _ = env()
    body = client.get("/settings/llm").json()
    assert body["active"] == {"preset": "groq", "label": "Groq (hosted)", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "context_tokens": 2500,
                              "max_output_tokens": 1200, "local": False, "takes_key": True, "key_optional": False, "key_set": False, "key_source": None}
    assert [p["id"] for p in body["presets"]] == ["groq", "ollama", "lmstudio", "custom"]
    ollama = next(p for p in body["presets"] if p["id"] == "ollama")
    assert ollama == {"id": "ollama", "label": PRESETS["ollama"].label, "base_url": "http://localhost:11434/v1", "model": "llama3.2", "takes_key": False, "key_optional": False, "local": True,
                      "base_url_editable": True, "context_tokens": 3000, "max_output_tokens": 800, "note": PRESETS["ollama"].note}


def test_a_key_in_the_environment_is_reported_as_set_but_never_shown(env):
    client, _ = env({"GROQ_API_KEY": KEY})
    r = client.get("/settings/llm")
    assert r.json()["active"]["key_set"] is True and r.json()["active"]["key_source"] == "env" and KEY not in r.text


# ---- choosing

def test_a_saved_choice_is_returned_and_survives_a_restart(env):
    client, _ = env()
    r = client.put("/settings/llm", json={"preset": "ollama", "model": "qwen2.5-coder:7b", "context_tokens": 3500})
    assert r.status_code == 200 and r.json()["active"]["preset"] == "ollama" and r.json()["active"]["model"] == "qwen2.5-coder:7b"
    assert r.json()["active"]["local"] is True and r.json()["active"]["takes_key"] is False and r.json()["active"]["context_tokens"] == 3500
    other, _ = env()                                                   # a second app on the same database: a restart
    assert other.get("/settings/llm").json()["active"]["model"] == "qwen2.5-coder:7b"


@pytest.mark.parametrize("body", [
    {"preset": "nope"}, {"preset": "groq", "model": ""}, {"preset": "groq", "context_tokens": 10}, {"preset": "groq", "base_url": "https://evil.example.com/v1"},
    {"preset": "custom"}, {"preset": "custom", "base_url": "ftp://x", "model": "m"}, {"preset": "custom", "base_url": "http://api.example.com/v1", "model": "m"},
])
def test_a_bad_choice_is_a_422_that_does_not_repeat_what_was_typed_and_saves_nothing(env, body):
    client, _ = env()
    before = client.get("/settings/llm").json()["active"]
    r = client.put("/settings/llm", json=body)
    assert r.status_code == 422 and code(r) == "invalid_settings"
    for typed in ("evil.example.com", "api.example.com", "ftp://x"):
        assert typed not in r.text
    assert client.get("/settings/llm").json()["active"] == before


@pytest.mark.parametrize("body", [{}, {"preset": 5}, {"preset": "groq", "context_tokens": "2500"}, {"preset": "groq", "context_tokens": True}, {"preset": "groq", "api_key": "x"},
                                  {"preset": "groq", "extra": 1}])
def test_a_malformed_request_is_a_422_in_the_usual_shape(env, body):
    client, _ = env()
    r = client.put("/settings/llm", json=body)
    assert r.status_code == 422 and code(r) in ("invalid_request", "invalid_settings")
    assert "api_key" not in client.get("/settings/llm").text


# ---- the key

def test_a_key_is_held_in_memory_reported_as_set_and_never_returned_or_stored(env, caplog):
    client, services = env()
    with caplog.at_level(logging.DEBUG):
        r = client.put("/settings/llm/key", json={"api_key": KEY})
        assert r.status_code == 200 and r.json() == {"key_set": True}
        got = client.get("/settings/llm")
    assert got.json()["active"]["key_set"] is True and got.json()["active"]["key_source"] == "memory"
    assert KEY not in r.text and KEY not in got.text and KEY not in caplog.text
    conn = db.get_connection()
    dump = json.dumps([list(row) for row in conn.execute("SELECT * FROM settings").fetchall()])
    conn.close()
    assert KEY not in dump, "the key was written to the database"


def test_a_key_does_not_survive_a_restart_the_desktop_app_pushes_it_again(env):
    client, _ = env()
    client.put("/settings/llm/key", json={"api_key": KEY})
    other, _ = env()
    assert other.get("/settings/llm").json()["active"]["key_set"] is False


def test_clearing_the_key(env):
    client, _ = env()
    client.put("/settings/llm/key", json={"api_key": KEY})
    r = client.put("/settings/llm/key", json={"api_key": None})
    assert r.status_code == 200 and r.json() == {"key_set": False}
    assert client.get("/settings/llm").json()["active"]["key_set"] is False


def test_clearing_the_key_falls_back_to_the_environment(env):
    client, _ = env({"GROQ_API_KEY": "env-key-" + "e" * 30})
    client.put("/settings/llm/key", json={"api_key": KEY})
    assert client.get("/settings/llm").json()["active"]["key_source"] == "memory"
    client.put("/settings/llm/key", json={"api_key": None})
    assert client.get("/settings/llm").json()["active"]["key_source"] == "env"


@pytest.mark.parametrize("bad", ["", "   ", "has space", "line1\nline2", "tab\tkey", "kéy", "a" * 513])
def test_a_malformed_key_is_refused_without_repeating_it_and_the_old_key_stays(env, bad):
    client, _ = env()
    client.put("/settings/llm/key", json={"api_key": KEY})
    r = client.put("/settings/llm/key", json={"api_key": bad})
    assert r.status_code == 422 and code(r) in ("invalid_key", "invalid_request")
    assert bad.strip() not in r.text or bad.strip() == ""
    assert client.get("/settings/llm").json()["active"]["key_source"] == "memory"


def test_the_key_field_is_required(env):
    client, _ = env()
    assert client.put("/settings/llm/key", json={}).status_code == 422
    assert client.put("/settings/llm/key", json={"api_key": KEY, "extra": 1}).status_code == 422


def test_a_custom_service_may_need_no_key_and_says_so(env):
    client, _ = env()
    custom_preset = next(p for p in client.get("/settings/llm").json()["presets"] if p["id"] == "custom")
    assert custom_preset["takes_key"] is True and custom_preset["key_optional"] is True and custom_preset["base_url"] == ""


def test_a_provider_that_takes_no_key_reports_none_even_when_one_is_held(env):
    client, _ = env()
    client.put("/settings/llm/key", json={"api_key": KEY})
    r = client.put("/settings/llm", json={"preset": "ollama"})
    assert r.json()["active"]["key_set"] is False and r.json()["active"]["key_source"] is None


# ---- testing the connection

def test_the_test_makes_one_tiny_real_call_with_the_key_and_reports_the_model(env):
    with FakeLLMServer(lambda r: good_reply("OK", model="served-model")) as server:
        client, _ = env()
        client.put("/settings/llm", json=custom(server))
        client.put("/settings/llm/key", json={"api_key": KEY})
        r = client.post("/settings/llm/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["model"] == "served-model" and body["reply"] == "OK" and body["finish_reason"] == "stop"
    assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0
    assert len(server.requests) == 1
    sent = server.requests[0]
    assert sent.headers["authorization"] == f"Bearer {KEY}" and sent.json["model"] == "test-model" and sent.json["max_tokens"] <= 100
    assert "OK" in sent.json["messages"][0]["content"] and KEY not in r.text


def test_a_local_provider_never_gets_a_key_even_if_one_is_held(env):
    with FakeLLMServer() as server:
        client, _ = env()
        client.put("/settings/llm", json={"preset": "ollama", "base_url": server.base_url, "model": "llama3.2"})
        client.put("/settings/llm/key", json={"api_key": KEY})
        assert client.post("/settings/llm/test").status_code == 200
    assert "authorization" not in server.requests[0].headers and KEY.encode() not in server.requests[0].raw


def test_the_test_without_a_key_for_a_provider_that_needs_one_says_which_variable_to_set(env):
    client, _ = env()
    r = client.post("/settings/llm/test")
    assert r.status_code == 503 and code(r) == "llm_not_configured" and "GROQ_API_KEY" in r.json()["error"]["message"]


@pytest.mark.parametrize("reply, status, expected", [
    ((401, {}, {"error": {"message": "bad key sk-live-1"}}), 502, "llm_auth_failed"),
    ((404, {}, {"error": {"message": "no such model"}}), 502, "llm_model_not_found"),
    ((429, {"retry-after": "7"}, {"error": {"message": "slow down"}}), 429, "llm_rate_limited"),
    ((500, {}, {"error": {"message": "boom"}}), 503, "llm_unavailable"),
])
def test_the_test_reports_a_failing_service_in_the_usual_words(env, reply, status, expected):
    with FakeLLMServer(lambda r: reply) as server:
        client, _ = env()
        client.put("/settings/llm", json=custom(server))
        r = client.post("/settings/llm/test")
    assert r.status_code == status and code(r) == expected and "sk-live-1" not in r.text
    if status == 429:
        assert r.headers["retry-after"] == "7"


def test_the_test_reports_a_service_that_is_not_running(env):
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    client, _ = env()
    client.put("/settings/llm", json={"preset": "ollama", "base_url": f"http://127.0.0.1:{port}/v1", "model": "llama3.2"})
    r = client.post("/settings/llm/test")
    assert r.status_code == 503 and code(r) == "llm_unavailable" and str(port) not in r.text


# ---- /answer follows the choice

@pytest.fixture
def world(conn, tmp_path):
    build = make_world(conn, tmp_path)
    conn.execute("UPDATE projects SET repo_path = ? WHERE id = 1", (str(build.repo),))
    conn.commit()
    build({"src/a.py": dict(scores=[0.80])})
    return build


def test_an_answer_uses_the_saved_service_and_switches_when_the_choice_changes(env, world):
    with FakeLLMServer(lambda r: good_reply("from server A", model="model-a")) as a, FakeLLMServer(lambda r: good_reply("from server B", model="model-b")) as b:
        client, _ = env(embedder=Question2D(), store=world.store)
        client.put("/settings/llm", json=custom(a))
        first = client.post("/projects/1/answer", json={"question": "how does it work"})
        client.put("/settings/llm", json=custom(b))
        second = client.post("/projects/1/answer", json={"question": "how does it work"})
    assert first.status_code == second.status_code == 200
    assert (first.json()["answer"], first.json()["model"]) == ("from server A", "model-a")
    assert (second.json()["answer"], second.json()["model"]) == ("from server B", "model-b")
    assert len(a.requests) == 1 and len(b.requests) == 1
    assert first.json()["profile"] == "custom" and first.json()["sent_off_machine"] is False, "a service on this computer needs no consent"
    messages = a.requests[0].json["messages"]
    assert [m["role"] for m in messages] == ["system", "user"] and "src/a.py" in messages[1]["content"]


def test_a_key_pushed_later_is_used_by_the_next_answer(env, world):
    with FakeLLMServer() as server:
        client, _ = env(embedder=Question2D(), store=world.store)
        client.put("/settings/llm", json=custom(server))
        client.post("/projects/1/answer", json={"question": "q"})
        client.put("/settings/llm/key", json={"api_key": KEY})
        client.post("/projects/1/answer", json={"question": "q"})
    assert "authorization" not in server.requests[0].headers
    assert server.requests[1].headers["authorization"] == f"Bearer {KEY}"


def test_the_settings_endpoints_need_the_token_and_are_plain_def(env):
    from main import create_app as real_create_app
    from routes_settings import router
    from security import Security
    services = Services(FakeEmbedder(dim=4), store_factory=lambda pid: InMemoryVectorStore(), environ={})
    with TestClient(real_create_app(services, auto_sync=False, security=Security(token="t" * 40)), base_url="http://127.0.0.1:8123") as client:
        for method, path in (("GET", "/settings/llm"), ("PUT", "/settings/llm"), ("PUT", "/settings/llm/key"), ("POST", "/settings/llm/test")):
            assert client.request(method, path).status_code == 401, (method, path)
    assert not any(inspect.iscoroutinefunction(r.endpoint) for r in router.routes)
