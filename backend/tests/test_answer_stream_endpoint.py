"""Task: streaming answers, part 7: `POST /projects/{id}/answer/stream` (NDJSON: one JSON object per line).

What is promised:
- every check of `/answer` runs, and the search, and the model connection is OPENED, BEFORE a single byte is streamed: so every refusal (404, 403 consent, 409 index,
  503 Ollama / model not set up, 422, and each model refusal with its status, code, message and Retry-After) is EXACTLY what `/answer` answers (a parity test compares them);
- then events, in this order: `start` (the search is done: sources, notes, budget), `stage` thinking (only if the model thinks), `thinking` (a running count),
  `stage` writing, `delta` (the next piece of text), and exactly ONE last event: `done` (the whole answer, in the shape of `/answer`'s reply plus timings, thinking and
  tokens) or `error` (a failure in the middle, in the status / code / message `/answer` would use); nothing follows the last event;
- the stage times are measured by the BACKEND (a fake clock makes them exact in these tests); a model that does not think has no thinking stage and no thinking time;
- an answer is saved ONLY when the model finished it (a failure or a Stop saves nothing); the saved extras are the same as `/answer`'s;
- when nothing matches the model is not called and the stream is just `start` then `done`;
- the model's stream is always closed at the end, whatever the end.
"""
import inspect
import json

import pytest
from fastapi.testclient import TestClient

import conversations
import routes_answer
from answer import NO_MATCH_TEXT
from llm import (FakeLLM, LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMModelNotFound, LLMNotConfigured, LLMRateLimited, LLMTimeout, LLMUnavailable,
                 StreamDone, TextPiece, ThinkingPiece)
from llm.fake import FakeStream
from test_answer_endpoint import LOCAL, REMOTE, Question2D, clients, indexed, make_client, world  # noqa: F401  (fixtures)
from embedding.errors import OllamaUnavailable
from vectorstore import InMemoryVectorStore

STREAM = "/projects/1/answer/stream"
ONE_SHOT = "/projects/1/answer"


def post(client, url=STREAM, **body):
    return client.post(url, json={"question": "how does it work", "allow_remote": True, **body})


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.split("\n") if line.strip()]


def types(evts):
    return [e["type"] for e in evts]


def new_conversation(client):
    return client.post("/projects/1/conversations").json()["id"]


def saved(client, cid):
    return client.get(f"/projects/1/conversations/{cid}").json()


class Clock:
    """A clock the tests move by hand, so every stage time is exact."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ClockedStream(FakeStream):
    """A stream that moves the clock before each event: steps are (seconds to move first, event)."""

    def __init__(self, clock, steps, **kw):
        super().__init__([event for _, event in steps], **kw)
        self._clock, self._moves = clock, [move for move, _ in steps]

    def __iter__(self):
        for move, event in zip(self._moves, self._events):
            if self.closed:
                return
            self._clock.now += move
            yield event
        if self._fail_with is not None and not self.closed:
            raise self._fail_with


def done_event(model="m", prompt=100, completion=84, reasoning=54, finish="stop"):
    return StreamDone(finish_reason=finish, model=model, prompt_tokens=prompt, completion_tokens=completion, reasoning_tokens=reasoning)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(routes_answer, "_clock", c)
    real = routes_answer.build_context

    def slow_search(*args, **kwargs):
        c.now += 0.4                                       # the search takes 0.4 s
        return real(*args, **kwargs)
    monkeypatch.setattr(routes_answer, "build_context", slow_search)
    return c


# ---- the refusals are exactly /answer's

def scenario_unknown_project(world, clients):
    indexed(world)
    return make_client(world, clients), {}, 999


def scenario_unknown_conversation(world, clients):
    indexed(world)
    return make_client(world, clients), {"conversation_id": 777}, 1


def scenario_not_configured(world, clients):
    indexed(world)

    def not_set_up():
        raise LLMNotConfigured("The answer model needs a key: add it in the settings.", env_var="K")
    return make_client(world, clients, factory=not_set_up), {}, 1


def scenario_no_consent(world, clients):
    indexed(world)
    return make_client(world, clients), {"allow_remote": False}, 1


def scenario_ollama_down(world, clients):
    indexed(world)

    class Down(Question2D):
        def warmup(self):
            raise OllamaUnavailable("down")
    return make_client(world, clients, embedder=Down()), {}, 1


def scenario_not_indexed(world, clients):
    return make_client(world, clients, store=InMemoryVectorStore()), {}, 1


def failing_model(error):
    def scenario(world, clients):
        indexed(world)
        return make_client(world, clients, FakeLLM([error])), {}, 1
    return scenario


def bad_body(**body):
    def scenario(world, clients):
        indexed(world)
        return make_client(world, clients), body, 1
    return scenario


SCENARIOS = {
    "unknown project": scenario_unknown_project,
    "unknown conversation": scenario_unknown_conversation,
    "model not set up": scenario_not_configured,
    "no consent": scenario_no_consent,
    "ollama down": scenario_ollama_down,
    "not indexed": scenario_not_indexed,
    "auth": failing_model(LLMAuthError("x")),
    "model not found": failing_model(LLMModelNotFound("x")),
    "context too long": failing_model(LLMContextTooLong("x")),
    "rate limited, wait known": failing_model(LLMRateLimited(retry_after=7.2)),
    "rate limited, wait unknown": failing_model(LLMRateLimited(retry_after=None)),
    "unavailable": failing_model(LLMUnavailable("x")),
    "timeout": failing_model(LLMTimeout("x")),
    "bad response": failing_model(LLMBadResponse("x")),
    "empty question": bad_body(question=""),
    "long question": bad_body(question="x" * 2001),
    "k too big": bad_body(k=31),
    "k not a number": bad_body(k="5"),
    "bad conversation id": bad_body(conversation_id="1"),
    "zero conversation id": bad_body(conversation_id=0),
    "extra field": bad_body(max_tokens=99999),
    "consent not a boolean": bad_body(allow_remote="true"),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_every_refusal_is_exactly_what_the_normal_route_says(world, clients, name):
    results = []
    for url_tail in ("", "/stream"):
        client, body, project = SCENARIOS[name](world, clients)
        r = client.post(f"/projects/{project}/answer{url_tail}", json={"question": "how does it work", "allow_remote": True, **body})
        results.append((r.status_code, r.json(), r.headers.get("retry-after")))
    assert results[0] == results[1], name
    assert results[0][0] != 200, "the scenario really is a refusal"


def test_a_refusal_sends_nothing_to_the_model_and_nothing_streams(world, clients):
    indexed(world)
    client = make_client(world, clients)
    r = post(client, allow_remote=False)
    assert r.status_code == 403 and r.headers["content-type"].startswith("application/json")
    assert client.llm.calls == [] and client.embedder.query_count == 0


def test_a_missing_conversation_is_found_before_anything_is_embedded(world, clients):
    indexed(world)
    client = make_client(world, clients)
    assert post(client, conversation_id=777).status_code == 404
    assert client.llm.calls == [] and client.embedder.query_count == 0


# ---- the events

def test_a_plain_answer_is_start_then_writing_then_deltas_then_done(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["It works by calling a."]))
    evts = events(post(client))
    assert types(evts) == ["start", "stage"] + ["delta"] * 5 + ["done"]
    assert evts[1]["stage"] == "writing"
    assert "".join(e["text"] for e in evts if e["type"] == "delta") == "It works by calling a." == evts[-1]["answer"]


def test_the_response_is_ndjson_that_is_not_cached(world, clients):
    indexed(world)
    r = post(make_client(world, clients))
    assert r.headers["content-type"].startswith("application/x-ndjson") and r.headers["cache-control"] == "no-store" and r.headers["x-accel-buffering"] == "no"
    assert r.text.endswith("\n") and "\n\n" not in r.text
    assert all(line.startswith("{") and line.endswith("}") for line in r.text.strip().split("\n"))


def test_start_carries_everything_known_before_the_model_writes(world, clients):
    indexed(world)
    start = events(post(make_client(world, clients, FakeLLM(["x"]))))[0]
    assert start["type"] == "start" and start["stage"] == "waiting" and start["llm_called"] is True
    assert {s["path"] for s in start["sources"]} == {"src/a.py", "src/b.py"}
    for key in ("search_ms", "dropped", "hidden_files", "stale_files", "deleted_files", "context_tokens_used", "context_budget", "over_budget", "best_score", "k",
                "ranking_note", "calibration_note", "profile", "sent_off_machine", "conversation_id"):
        assert key in start, key
    assert start["context_budget"] == REMOTE.context_tokens and start["sent_off_machine"] is True and start["profile"] == "remote"


def test_a_local_model_says_nothing_left_the_machine(world, clients):
    indexed(world)
    start = events(post(make_client(world, clients, FakeLLM(["x"]), profile=LOCAL), allow_remote=False))[0]
    assert start["sent_off_machine"] is False


def test_done_is_the_shape_of_the_normal_answer_plus_timings_thinking_and_tokens(world, clients):
    indexed(world)
    text = "It calls a (src/a.py:1-1)."
    normal = post(make_client(world, clients, FakeLLM([text])), ONE_SHOT).json()
    done = events(post(make_client(world, clients, FakeLLM([text]))))[-1]
    assert done.pop("type") == "done"
    for key in ("timings", "thinking", "tokens"):
        assert key in done and key in normal
        done.pop(key), normal.pop(key)
    done.pop("saved")
    assert done == normal


def test_a_thinking_model_shows_thinking_then_writing(world, clients):
    indexed(world)
    evts = events(post(make_client(world, clients, FakeLLM(["a b c"], thinking=3))))
    assert types(evts) == ["start", "stage", "thinking", "thinking", "thinking", "stage", "delta", "delta", "delta", "done"]
    assert [e["stage"] for e in evts if e["type"] == "stage"] == ["thinking", "writing"]
    assert [e["pieces"] for e in evts if e["type"] == "thinking"] == [1, 2, 3]
    assert evts[-1]["thinking"] == {"seen": True, "pieces": 3}


def test_a_model_that_does_not_think_has_no_thinking_anywhere(world, clients):
    indexed(world)
    evts = events(post(make_client(world, clients, FakeLLM(["a b"]))))
    assert "thinking" not in types(evts) and [e["stage"] for e in evts if e["type"] == "stage"] == ["writing"]
    done = evts[-1]
    assert done["thinking"] == {"seen": False, "pieces": 0} and done["timings"]["thinking_ms"] is None and done["tokens"]["thinking"] is None


def test_the_stage_events_say_when_they_happened_and_never_go_backwards(world, clients, clock):
    indexed(world)
    steps = [(0.5, ThinkingPiece()), (0.25, ThinkingPiece()), (1.0, TextPiece("a")), (0.25, TextPiece("b")), (0.1, done_event())]
    evts = events(post(make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))))
    stages = [(e["stage"], e["at_ms"]) for e in evts if e["type"] == "stage"]
    assert stages == [("thinking", 400 + 500), ("writing", 400 + 500 + 250 + 1000)]


def test_the_timings_are_measured_by_the_backend(world, clients, clock):
    indexed(world)
    steps = [(0.5, ThinkingPiece()), (0.25, ThinkingPiece()), (1.0, TextPiece("a")), (0.25, TextPiece("b")), (0.1, done_event())]
    done = events(post(make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))))[-1]
    assert done["timings"] == {"search_ms": 400, "wait_ms": 500, "thinking_ms": 1250, "writing_ms": 350, "total_ms": 2500}


def test_the_timings_of_a_model_that_does_not_think(world, clients, clock):
    indexed(world)
    steps = [(0.5, TextPiece("a")), (0.25, TextPiece("b")), (0.25, done_event(reasoning=None))]
    done = events(post(make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))))[-1]
    assert done["timings"] == {"search_ms": 400, "wait_ms": 500, "thinking_ms": None, "writing_ms": 500, "total_ms": 1400}


def test_the_search_time_is_in_start_too(world, clients, clock):
    indexed(world)
    steps = [(0.1, TextPiece("a")), (0.1, done_event())]
    assert events(post(make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))))[0]["search_ms"] == 400


def test_tokens_reported_by_the_service_are_exact(world, clients, clock):
    indexed(world)
    steps = [(0.1, ThinkingPiece()), (0.1, TextPiece("a")), (0.1, done_event(prompt=90, completion=84, reasoning=54))]
    done = events(post(make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))))[-1]
    assert done["tokens"] == {"thinking": 54, "answer": 30, "estimated": False}
    assert done["usage"] == {"prompt_tokens": 90, "completion_tokens": 84}


def test_tokens_not_reported_are_estimated_and_say_so(world, clients, clock):
    indexed(world)
    steps = [(0.1, ThinkingPiece()), (0.1, ThinkingPiece()), (0.1, TextPiece("x" * 90)), (0.1, StreamDone(finish_reason="stop", model="m"))]
    done = events(post(make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))))[-1]
    assert done["tokens"] == {"thinking": 2, "answer": 30, "estimated": True}
    assert done["usage"] == {"prompt_tokens": None, "completion_tokens": None}


def test_the_model_name_comes_from_the_stream(world, clients):
    indexed(world)
    steps = [TextPiece("a"), done_event(model="the-real-model")]
    assert events(post(make_client(world, clients, FakeLLM([steps]))))[-1]["model"] == "the-real-model"


def test_an_answer_cut_by_the_length_cap_is_said_so(world, clients):
    indexed(world)
    steps = [TextPiece("The function a calls"), done_event(finish="length")]
    done = events(post(make_client(world, clients, FakeLLM([steps]))))[-1]
    assert done["truncated"] is True and done["finish_reason"] == "length" and "cut off" in done["notice"].lower() and done["answer"] == "The function a calls"


def test_an_answer_that_never_wrote_is_empty_with_a_notice(world, clients):
    indexed(world)
    steps = [ThinkingPiece(), done_event(finish="length")]
    evts = events(post(make_client(world, clients, FakeLLM([steps]))))
    done = evts[-1]
    assert done["answer"] == "" and done["truncated"] is True and "whole answer allowance" in done["notice"] and "delta" not in types(evts)
    assert done["timings"]["writing_ms"] is None


def test_nothing_matching_is_start_then_done_and_the_model_is_not_called(world, clients):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    client = make_client(world, clients)
    evts = events(post(client))
    assert types(evts) == ["start", "done"] and evts[0]["llm_called"] is False and client.llm.calls == []
    assert evts[0]["sent_off_machine"] is False and evts[1]["sent_off_machine"] is False, "a remote profile, but nothing was sent"
    done = evts[-1]
    assert done["answer"] == NO_MATCH_TEXT and done["llm_called"] is False and done["usage"] is None and done["model"] is None and done["sources"] == []
    assert done["timings"]["wait_ms"] is None and done["tokens"] == {"thinking": None, "answer": None, "estimated": False}


def test_the_earlier_turns_of_the_conversation_are_sent_like_in_the_normal_route(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["first answer", "second answer"]))
    cid = new_conversation(client)
    events(post(client, question="first question", conversation_id=cid))
    events(post(client, question="and the second?", conversation_id=cid))
    sent = client.llm.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"] and sent[1]["content"] == "first question" and sent[2]["content"] == "first answer"
    assert client.llm.calls[1]["stream"] is True


def test_the_answer_cap_is_the_profiles_and_the_stream_is_asked_for(world, clients):
    indexed(world)
    client = make_client(world, clients)
    events(post(client))
    assert client.llm.calls[0]["max_output_tokens"] == REMOTE.max_output_tokens and client.llm.calls[0]["stream"] is True


def test_no_event_holds_a_secret_or_the_thinking_text(world, clients):
    indexed(world)
    text = post(make_client(world, clients, FakeLLM(["fine"], thinking=2))).text
    for forbidden in ("gsk_", "api_key", "Authorization", "reasoning"):
        assert forbidden not in text


# ---- saving

def test_a_finished_answer_is_saved_with_its_summary(world, clients, clock):
    indexed(world)
    steps = [(0.5, ThinkingPiece()), (0.5, TextPiece("It calls a.")), (0.1, done_event(reasoning=7, completion=12))]
    client = make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))
    cid = new_conversation(client)
    done = events(post(client, question="what does a do?", conversation_id=cid))[-1]
    assert done["saved"] is True and done["conversation_id"] == cid
    user, assistant = saved(client, cid)["messages"]
    assert (user["content"], assistant["content"]) == ("what does a do?", "It calls a.")
    meta = assistant["meta"]
    assert "answer" not in meta and meta["timings"]["total_ms"] == done["timings"]["total_ms"] and meta["tokens"] == {"thinking": 7, "answer": 5, "estimated": False}
    assert meta["thinking"] == {"seen": True, "pieces": 1} and meta["sources"] == done["sources"] and meta["llm_called"] is True


def test_the_saved_answer_is_the_text_that_was_streamed(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["  spaced   out\n\nanswer  "]))
    cid = new_conversation(client)
    evts = events(post(client, conversation_id=cid))
    streamed = "".join(e["text"] for e in evts if e["type"] == "delta")
    assert saved(client, cid)["messages"][1]["content"] == streamed == evts[-1]["answer"] == "  spaced   out\n\nanswer  "


def test_without_a_conversation_nothing_is_saved_and_done_says_so(world, clients):
    indexed(world)
    client = make_client(world, clients)
    done = events(post(client))[-1]
    assert done["conversation_id"] is None and done["saved"] is False
    assert client.get("/projects/1/conversations").json() == []


def test_an_answer_with_no_matching_code_is_saved_but_not_remembered(world, clients):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    client = make_client(world, clients)
    cid = new_conversation(client)
    done = events(post(client, conversation_id=cid))[-1]
    assert done["saved"] is True
    assistant = saved(client, cid)["messages"][1]
    assert assistant["content"] == NO_MATCH_TEXT and assistant["meta"]["llm_called"] is False
    assert conversations.recent_turns(client.app.state.services.connect(), cid) == []


def test_a_failure_in_the_middle_ends_with_an_error_event_and_saves_nothing(world, clients):
    indexed(world)
    stream = FakeStream([TextPiece("half an"), TextPiece(" answer")], fail_with=LLMTimeout("slow"))
    client = make_client(world, clients, FakeLLM([stream]))
    cid = new_conversation(client)
    evts = events(post(client, conversation_id=cid))
    assert types(evts) == ["start", "stage", "delta", "delta", "error"]
    assert evts[-1]["error"] == {"code": "llm_timeout", "message": "The answer service took too long to answer. Try again.", "status": 504}
    assert saved(client, cid)["messages"] == [] and saved(client, cid)["title"] is None


@pytest.mark.parametrize("error,expected", [(LLMUnavailable("x"), "llm_unavailable"), (LLMBadResponse("x"), "llm_bad_response"), (LLMAuthError("x"), "llm_auth_failed"),
                                            (LLMRateLimited(retry_after=3), "llm_rate_limited")])
def test_a_model_failure_in_the_middle_is_described_like_the_normal_route_would(world, clients, error, expected):
    from api_errors import llm_error_info
    indexed(world)
    evts = events(post(make_client(world, clients, FakeLLM([FakeStream([TextPiece("a")], fail_with=error)]))))
    info = llm_error_info(error)
    assert evts[-1]["type"] == "error" and evts[-1]["error"]["code"] == expected
    assert (evts[-1]["error"]["message"], evts[-1]["error"]["status"]) == (info["message"], info["status"])
    assert evts[-1].get("retry_after") == info.get("retry_after")


def test_an_unexpected_failure_is_a_generic_error_event_and_the_details_go_to_the_log(world, clients, caplog):
    indexed(world)
    stream = FakeStream([TextPiece("a")], fail_with=RuntimeError("secret detail at /Users/someone/x.py"))
    with caplog.at_level("ERROR", logger="clank.api"):
        evts = events(post(make_client(world, clients, FakeLLM([stream]))))
    assert evts[-1]["error"]["code"] == "internal_error" and "secret" not in json.dumps(evts) and "/Users" not in json.dumps(evts)
    assert any("secret detail" in str(r.exc_info[1]) for r in caplog.records if r.exc_info)


def test_when_the_answer_cannot_be_saved_it_is_still_shown_and_done_says_it_was_not_saved(world, clients, monkeypatch):
    indexed(world)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(routes_answer.conversations, "add_exchange", broken)
    client = make_client(world, clients, FakeLLM(["the answer"]))
    cid = new_conversation(client)
    done = events(post(client, conversation_id=cid))[-1]
    assert done["type"] == "done" and done["answer"] == "the answer" and done["saved"] is False and "could not be saved" in done["notice"]
    assert "disk full" not in json.dumps(done) and saved(client, cid)["messages"] == []


# ---- the model's stream is always closed

def test_the_stream_is_closed_after_a_complete_answer(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["a b"]))
    events(post(client))
    assert len(client.llm.streams) == 1 and client.llm.streams[0].closed


def test_the_stream_is_closed_after_a_failure_in_the_middle(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM([FakeStream([TextPiece("a")], fail_with=LLMTimeout("x"))]))
    events(post(client))
    assert client.llm.streams[0].closed


def test_the_stream_is_closed_when_nothing_matches_there_is_none_to_close(world, clients):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    client = make_client(world, clients)
    events(post(client))
    assert client.llm.streams == []


# ---- the endpoint itself

def test_the_endpoint_is_a_plain_def_so_its_blocking_work_runs_on_the_thread_pool():
    route = next(r for r in routes_answer.router.routes if r.path == "/projects/{project_id}/answer/stream")
    assert not inspect.iscoroutinefunction(route.endpoint)


def test_the_stream_endpoint_needs_the_token(world, tmp_path):
    from main import create_app as real_create_app
    from security import Security
    from services import Services
    indexed(world)
    token = "t" * 40
    services = Services(Question2D(), store_factory=lambda pid: world.store, llm_factory=lambda: (REMOTE, FakeLLM(["x"])))
    with TestClient(real_create_app(services, auto_sync=False, security=Security(token=token)), base_url="http://127.0.0.1:8123") as client:
        body = {"question": "q", "allow_remote": True}
        assert client.post(STREAM, json=body).status_code == 401
        r = client.post(STREAM, json=body, headers={"x-clank-token": token})
        assert r.status_code == 200 and json.loads(r.text.strip().split("\n")[-1])["type"] == "done"


def test_a_long_answer_streams_every_piece(world, clients):
    indexed(world)
    text = " ".join(f"word{i}" for i in range(2000))
    evts = events(post(make_client(world, clients, FakeLLM([text]))))
    assert sum(e["type"] == "delta" for e in evts) == 2000 and evts[-1]["answer"] == text


def test_unicode_survives(world, clients):
    indexed(world)
    text = "café 日本語 🙂   end"
    evts = events(post(make_client(world, clients, FakeLLM([text]))))
    assert "".join(e["text"] for e in evts if e["type"] == "delta") == text == evts[-1]["answer"]


def test_an_empty_piece_is_not_a_delta_and_does_not_start_the_writing_stage(world, clients):
    indexed(world)
    script = [TextPiece(""), ThinkingPiece(), TextPiece(""), TextPiece("a"), done_event()]
    evts = events(post(make_client(world, clients, FakeLLM([script]))))
    assert types(evts) == ["start", "stage", "thinking", "stage", "delta", "done"]
    assert [e["stage"] for e in evts if e["type"] == "stage"] == ["thinking", "writing"] and evts[-1]["answer"] == "a"


def test_a_stream_that_ends_without_a_done_event_still_ends_with_done_and_the_profiles_model(world, clients):
    indexed(world)
    evts = events(post(make_client(world, clients, FakeLLM([[TextPiece("a b")]]))))
    done = evts[-1]
    assert done["type"] == "done" and done["answer"] == "a b" and done["model"] == REMOTE.model and done["finish_reason"] is None and done["truncated"] is False
    assert done["usage"] == {"prompt_tokens": None, "completion_tokens": None} and done["tokens"]["estimated"] is True


def test_thinking_that_only_starts_after_the_answer_began_has_no_stage_before_the_answer(world, clients, clock):
    """No model seen so far does this. The rule is the plain one: the thinking stage is the time between the first thinking piece and the first answer piece, so here it is 0."""
    indexed(world)
    steps = [(0.2, TextPiece("a")), (0.3, ThinkingPiece()), (0.5, TextPiece("b")), (0.1, done_event())]
    evts = events(post(make_client(world, clients, FakeLLM([ClockedStream(clock, steps)]))))
    done = evts[-1]
    assert done["timings"]["wait_ms"] == 200 and done["timings"]["thinking_ms"] == 0 and done["thinking"] == {"seen": True, "pieces": 1}
    assert [e["stage"] for e in evts if e["type"] == "stage"] == ["writing", "thinking"], "both stages were announced, in the order they really began"


def test_the_start_event_and_the_done_event_name_the_conversation(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["a"]))
    cid = new_conversation(client)
    evts = events(post(client, conversation_id=cid))
    assert evts[0]["conversation_id"] == cid and evts[-1]["conversation_id"] == cid


def test_a_failed_save_keeps_the_notice_the_answer_already_had(world, clients, monkeypatch):
    indexed(world)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(routes_answer.conversations, "add_exchange", broken)
    client = make_client(world, clients, FakeLLM([[TextPiece("cut"), done_event(finish="length")]]))
    cid = new_conversation(client)
    done = events(post(client, conversation_id=cid))[-1]
    assert "cut off" in done["notice"] and "could not be saved" in done["notice"] and done["saved"] is False


def test_a_lone_surrogate_in_the_models_text_does_not_break_the_stream(world, clients):
    """A model's text can hold half of an emoji (a lone surrogate). Written as raw UTF-8 it would raise in the middle of the stream; as an escape it survives."""
    indexed(world)
    text = "bad \ud800 half"
    evts = events(post(make_client(world, clients, FakeLLM([[TextPiece(text), done_event()]]))))
    assert evts[-1]["type"] == "done" and evts[-1]["answer"] == text
    assert "".join(e["text"] for e in evts if e["type"] == "delta") == text


def test_the_normal_route_measures_the_wait_for_the_model_too(world, clients, clock):
    indexed(world)

    class Slow(FakeLLM):
        def complete(self, messages, *, max_output_tokens):
            clock.now += 1.5                                         # the model takes a second and a half
            return super().complete(messages, max_output_tokens=max_output_tokens)
    body = post(make_client(world, clients, Slow(["a"])), ONE_SHOT).json()
    assert body["timings"] == {"search_ms": 400, "wait_ms": 1500, "thinking_ms": None, "writing_ms": None, "total_ms": 1900}
    assert body["thinking"] == {"seen": False, "pieces": 0}


def test_the_normal_route_reports_the_thinking_tokens_the_service_gave(world, clients):
    from llm import Completion
    indexed(world)
    completion = Completion(text="x", finish_reason="stop", model="m", prompt_tokens=90, completion_tokens=84, reasoning_tokens=54)
    body = post(make_client(world, clients, FakeLLM([completion])), ONE_SHOT).json()
    assert body["tokens"] == {"thinking": 54, "answer": 30, "estimated": False}


def test_the_normal_route_with_no_match_has_no_model_time(world, clients, clock):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    body = post(make_client(world, clients), ONE_SHOT).json()
    assert body["timings"] == {"search_ms": 400, "wait_ms": None, "thinking_ms": None, "writing_ms": None, "total_ms": 400}
