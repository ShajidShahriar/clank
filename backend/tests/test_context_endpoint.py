"""Task 8.5: `POST /projects/{id}/context`.

The user's defaults (decided 2026-10-04): k = 10, max_tokens = 4000 (devlog 77: the same as 6000 on every measured question), the demotion ON (the
default of `search`), NO relevance floor (decision 13: show the best score, never cut silently). The response carries the text for the LLM, metadata for
every passage (including the STALE flag), the files that could not be indexed, and BOTH `ranking_note` and `calibration_note`, which are for the person
and are never in the text.
Out-of-range values are refused (422), not clamped: a cap that silently changed k would hide what was asked. Hand-made 2-d hits give exact scores.
"""
import inspect

import pytest
from fastapi.testclient import TestClient

from embedding.errors import OllamaUnavailable
from hand_made import MODEL, Question2D, make_world
from jobs import IndexJobs
from main import create_app
from services import Services
import routes_context

CALIBRATED_TAG = "qwen3-embedding:0.6b"
CALIBRATED = f"{CALIBRATED_TAG}@ac6da0dfba84"


class Named(Question2D):
    """The 2-d question embedder under another model name (a real model name, so the shipped calibration applies or not)."""

    def __init__(self, name):
        super().__init__()
        self._name = name

    @property
    def model_name(self):
        return self._name


@pytest.fixture
def world(conn, tmp_path):
    build = make_world(conn, tmp_path)
    conn.execute("UPDATE projects SET repo_path = ? WHERE id = 1", (str(build.repo),))
    conn.commit()
    return build


def client_for(world, embedder=None, signature=None, store=None):
    embedder = embedder or Question2D()
    store = store if store is not None else world.store
    if signature is not None:
        store.set_signature(*signature)
    services = Services(embedder, store_factory=lambda pid: store)
    client = TestClient(create_app(services, IndexJobs(services), auto_sync=False))
    client.__enter__()
    services.wait_for_warmup()
    return client


@pytest.fixture
def clients():
    made = []
    yield made
    for c in made:
        c.__exit__(None, None, None)


def ask(client, **body):
    return client.post("/projects/1/context", json={"question": "how does it work", **body})


# ---- the response

def test_the_answer_has_the_text_the_passages_and_both_notes(world, clients):
    world({"src/a.py": dict(scores=[0.80]), "src/b.py": dict(scores=[0.60])})
    clients.append(client := client_for(world))
    r = ask(client)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"text", "passages", "dropped", "hidden_files", "stale_files", "deleted_files", "tokens_used", "over_budget", "best_score",
                         "k", "max_tokens", "ranking_note", "calibration_note"}
    assert "src/a.py" in body["text"] and "src/b.py" in body["text"]
    first = body["passages"][0]
    assert first == {"path": "src/a.py", "symbol": "s0", "parent": None, "kind": first["kind"], "start_line": 1, "end_line": 1, "score": 0.8,
                     "stale": False, "complete": True, "narrowed": False}
    assert [p["path"] for p in body["passages"]] == ["src/a.py", "src/b.py"] and body["best_score"] == 0.8
    assert body["tokens_used"] > 0 and body["over_budget"] is False and body["dropped"] == [] and body["hidden_files"] == []


def test_the_defaults_are_k_10_and_4000_tokens_with_the_default_policies_and_no_cutoff(world, clients, monkeypatch):
    world({"a.py": dict(scores=[0.9 - 0.01 * i for i in range(12)])})
    seen = {}
    real = routes_context.build_context

    def spy(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return real(*args, **kwargs)
    monkeypatch.setattr(routes_context, "build_context", spy)
    clients.append(client := client_for(world))
    body = ask(client).json()
    assert (body["k"], body["max_tokens"]) == (10, 4000) and len(body["passages"]) == 10
    assert seen["args"][-2:] == (10, 4000)
    assert seen["kwargs"].get("cutoff") is None, "no relevance floor"
    assert seen["kwargs"].get("test_policy", routes_context.DEFAULT_DEMOTION) is routes_context.DEFAULT_DEMOTION, "the demotion is on"


def test_a_given_k_and_max_tokens_are_used_and_echoed(world, clients):
    world({"a.py": dict(scores=[0.9 - 0.01 * i for i in range(12)])})
    clients.append(client := client_for(world))
    body = ask(client, k=3, max_tokens=1000).json()
    assert (body["k"], body["max_tokens"], len(body["passages"])) == (3, 1000, 3)


def test_scores_are_rounded_to_four_places(world, clients):
    world({"a.py": dict(scores=[0.7234567891]), "b.py": dict(scores=[0.5123456789])})
    clients.append(client := client_for(world))
    body = ask(client).json()
    assert [p["score"] for p in body["passages"]] == [0.7235, 0.5123] and body["best_score"] == 0.7235


def test_the_question_is_stripped(world, clients):
    world({"a.py": dict(scores=[0.9])})
    clients.append(client := client_for(world))
    assert ask(client, question="  how?  \n").status_code == 200


def test_no_path_outside_the_repo_comes_back(world, clients):
    world({"src/a.py": dict(scores=[0.8])})
    clients.append(client := client_for(world))
    assert str(world.repo) not in ask(client).text


def test_the_endpoint_is_a_plain_def():
    route = next(r for r in routes_context.router.routes if r.path == "/projects/{project_id}/context")
    assert not inspect.iscoroutinefunction(route.endpoint)


# ---- the notes

def test_a_model_other_than_the_calibrated_one_gives_the_ranking_note_and_the_default_policy_was_asked_for(world, clients):
    world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    clients.append(client := client_for(world))
    body = ask(client).json()
    assert [p["path"] for p in body["passages"]] == ["tests/t.py", "src/a.py"], "not demoted for this model"
    assert CALIBRATED_TAG in body["ranking_note"] and MODEL in body["ranking_note"] and body["calibration_note"] is None


def test_the_calibrated_model_demotes_tests_with_no_note(world, clients):
    world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    clients.append(client := client_for(world, Named(CALIBRATED), (CALIBRATED, 2)))
    body = ask(client).json()
    assert [p["path"] for p in body["passages"]] == ["src/a.py", "tests/t.py"]
    assert body["ranking_note"] is None and body["calibration_note"] is None


def test_the_same_model_with_new_weights_still_demotes_and_returns_the_calibration_note(world, clients):
    world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    other = f"{CALIBRATED_TAG}@123456789abc"
    clients.append(client := client_for(world, Named(other), (other, 2)))
    body = ask(client).json()
    assert [p["path"] for p in body["passages"]] == ["src/a.py", "tests/t.py"] and body["ranking_note"] is None
    assert "measured on ac6da0dfba84" in body["calibration_note"] and "123456789abc" in body["calibration_note"]
    assert "ac6da0dfba84" not in body["text"] and "calibration" not in body["text"].lower(), "the note is for the person, never for the LLM"


# ---- freshness and files

def test_a_changed_file_is_marked_stale_and_a_deleted_one_is_listed_and_left_out(world, clients):
    world({"src/a.py": dict(scores=[0.80]), "src/b.py": dict(scores=[0.70]), "src/c.py": dict(scores=[0.60])})
    (world.repo / "src/a.py").write_text("changed since it was indexed")
    (world.repo / "src/b.py").unlink()
    clients.append(client := client_for(world))
    body = ask(client).json()
    by_path = {p["path"]: p for p in body["passages"]}
    assert by_path["src/a.py"]["stale"] is True and by_path["src/c.py"]["stale"] is False
    assert body["stale_files"] == ["src/a.py"] and body["deleted_files"] == ["src/b.py"] and "src/b.py" not in by_path


def test_files_that_could_not_be_indexed_are_listed_with_the_reason_and_their_chunks_are_left_out(world, clients):
    world({"src/a.py": dict(scores=[0.80]), "src/bad.py": dict(scores=[0.90], hidden=True)})
    clients.append(client := client_for(world))
    body = ask(client).json()
    assert [p["path"] for p in body["passages"]] == ["src/a.py"]
    assert body["hidden_files"] == [{"path": "src/bad.py", "reason": "EmbeddingTooLong: x"}]


# ---- validation: refused, not clamped

@pytest.mark.parametrize("body", [
    {"question": ""}, {"question": "   \n "}, {"question": "x" * 2001}, {"question": 5}, {},
    {"question": "ok", "k": 0}, {"question": "ok", "k": 31}, {"question": "ok", "k": -1}, {"question": "ok", "k": "ten"}, {"question": "ok", "k": 2.5},
    {"question": "ok", "k": "5"}, {"question": "ok", "k": True}, {"question": "ok", "max_tokens": "1000"},
    {"question": "ok", "max_tokens": 499}, {"question": "ok", "max_tokens": 16001}, {"question": "ok", "max_tokens": 0},
    {"question": "ok", "max_token": 1000}, {"question": "ok", "cutoff": 0.5},
])
def test_a_bad_request_is_a_422_and_nothing_is_searched(world, clients, body):
    world({"a.py": dict(scores=[0.9])})
    embedder = Question2D()
    clients.append(client := client_for(world, embedder))
    r = client.post("/projects/1/context", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert embedder.query_count == 0, "validation happens before anything is embedded"


@pytest.mark.parametrize("body", [{"question": "x" * 2000}, {"question": "ok", "k": 30}, {"question": "ok", "k": 1},
                                  {"question": "ok", "max_tokens": 500}, {"question": "ok", "max_tokens": 16000}])
def test_the_edges_of_the_ranges_are_accepted(world, clients, body):
    world({"a.py": dict(scores=[0.9])})
    clients.append(client := client_for(world))
    assert client.post("/projects/1/context", json=body).status_code == 200


# ---- errors, and their order

def test_an_unknown_project_is_404_even_when_ollama_is_down(world, clients):
    class Down(Question2D):
        def warmup(self):
            raise OllamaUnavailable("down")
    world({"a.py": dict(scores=[0.9])})
    clients.append(client := client_for(world, Down()))
    r = client.post("/projects/999/context", json={"question": "q"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "project_not_found"


def test_a_project_that_was_never_indexed_is_409_not_indexed(world, clients):
    from vectorstore import InMemoryVectorStore
    clients.append(client := client_for(world, store=InMemoryVectorStore()))     # a fresh store: no signature, nothing indexed
    r = ask(client)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_indexed"


def test_an_index_built_for_other_weights_is_409_index_out_of_date(world, clients):
    world({"a.py": dict(scores=[0.9])})
    clients.append(client := client_for(world, Named("fake-hash-2@new"), (MODEL, 2)))
    r = ask(client)
    assert r.status_code == 409 and r.json()["error"]["code"] == "index_out_of_date" and "re-index" in r.json()["error"]["message"]


def test_ollama_down_is_503(world, clients):
    class Down(Question2D):
        def warmup(self):
            raise OllamaUnavailable("down")
    world({"a.py": dict(scores=[0.9])})
    clients.append(client := client_for(world, Down()))
    services = client.app.state.services
    services._retry_after = 0
    r = ask(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "ollama_unavailable"
