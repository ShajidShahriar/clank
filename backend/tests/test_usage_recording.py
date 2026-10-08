"""Task: stage 2, step 2.1b: every model call is recorded (usage.py), and the provider's rate-limit numbers are kept.

What is promised:
- an answer that the model gave (`/answer` and `/answer/stream`) adds ONE row: the profile's name, the model, kind `answer`, outcome `done`, and the tokens: the
  service's own numbers when it reports them, else Clank's estimate (`estimated` says so);
- a stream that the person stops is recorded as `stopped`, one that fails in the middle as `failed`, both with estimates for what the service did not say, because tokens
  were spent; a call refused before any word (a bad key, a 429, a too long request), a refusal by Clank (no consent, no index) and "no matching code" record NO call;
- the provider's `x-ratelimit-*` numbers are kept from a good reply AND from a 429 (the numbers that matter most);
- the settings dialog's "Test connection" is recorded as kind `test`;
- recording is best effort: if it fails, the answer is still given; and the question, the answer text and the project are never stored in the usage tables.
"""
import json
import threading
import time

import pytest

import routes_answer
import usage
import usage_recording
from chunker.core import estimate_tokens
from llm import Completion, FakeLLM, LLMAuthError, LLMRateLimited, LLMUnavailable, StreamDone, TextPiece, ThinkingPiece
from llm.fake import FakeStream
from test_answer_endpoint import LOCAL, REMOTE, ask, clients, indexed, make_client, world  # noqa: F401  (fixtures)
from test_answer_stream_endpoint import events, post, types  # noqa: F401
from test_answer_stream_live import Live, read_event, wait_for

NOW = 1_700_000_000.0
HEADERS = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-tokens": "7000", "set-cookie": "never kept"}


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(usage_recording, "_wall", lambda: NOW)


def rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM usage_calls ORDER BY id")]


def prompt_estimate(client):
    return sum(estimate_tokens(m["content"]) for m in client.llm.calls[0]["messages"])


# ---- /answer

def test_a_good_answer_records_one_call_with_the_services_own_numbers(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, FakeLLM([Completion("hi", "stop", "served-model", prompt_tokens=120, completion_tokens=30, reasoning_tokens=10)]))
    assert ask(client).status_code == 200
    assert rows(conn) == [{"id": 1, "at": NOW, "provider": "remote", "model": "served-model", "kind": "answer", "outcome": "done",
                           "prompt_tokens": 120, "thinking_tokens": 10, "answer_tokens": 20, "estimated": 0}]


def test_numbers_the_service_did_not_give_are_estimated_and_marked(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, FakeLLM([Completion("x" * 30, "stop", "m")]))
    ask(client)
    (row,) = rows(conn)
    assert (row["prompt_tokens"], row["thinking_tokens"], row["answer_tokens"], row["estimated"]) == (prompt_estimate(client), None, estimate_tokens("x" * 30), 1)


def test_one_number_missing_is_enough_to_mark_the_row_as_estimated(world, clients, conn):
    indexed(world)
    ask(make_client(world, clients, FakeLLM([Completion("hi", "stop", "m", prompt_tokens=None, completion_tokens=30, reasoning_tokens=10)])))     # only the prompt is guessed
    ask(make_client(world, clients, FakeLLM([Completion("hi", "stop", "m", prompt_tokens=100, completion_tokens=None)])))                           # only the answer is guessed
    ask(make_client(world, clients, FakeLLM([Completion("hi", "stop", "m", prompt_tokens=100, completion_tokens=30)])))                              # nothing is guessed
    first, second, third = rows(conn)
    assert (first["estimated"], first["answer_tokens"], first["thinking_tokens"]) == (1, 20, 10)
    assert second["estimated"] == 1 and third["estimated"] == 0


def test_a_local_model_is_recorded_too(world, clients, conn):
    indexed(world)
    ask(make_client(world, clients, profile=LOCAL))
    assert [r["provider"] for r in rows(conn)] == ["local"]


def test_the_providers_rate_limit_numbers_are_kept_from_a_good_reply(world, clients, conn):
    indexed(world)
    ask(make_client(world, clients, FakeLLM([Completion("hi", "stop", "m", prompt_tokens=1, completion_tokens=1, rate_limit_headers=HEADERS)])))
    assert usage.latest_snapshot(conn, "remote") == {"headers": {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-tokens": "7000"}, "at": NOW}


def test_a_429_keeps_the_numbers_and_records_no_call(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, FakeLLM([LLMRateLimited(retry_after=3, rate_limit_headers=HEADERS)]))
    assert ask(client).status_code == 429
    assert rows(conn) == []
    assert usage.latest_snapshot(conn, "remote")["headers"]["x-ratelimit-remaining-tokens"] == "7000"


def test_a_call_refused_before_any_word_records_nothing(world, clients, conn):
    indexed(world)
    assert ask(make_client(world, clients, FakeLLM([LLMAuthError("no")]))).status_code == 502
    assert rows(conn) == [] and usage.latest_snapshot(conn, "remote") is None


def test_a_refusal_by_clank_records_nothing(world, clients, conn):
    indexed(world)
    assert ask(make_client(world, clients), allow_remote=False).status_code == 403
    assert rows(conn) == []


def test_no_matching_code_means_no_call_and_no_record(world, clients, conn):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    client = make_client(world, clients)
    assert ask(client).json()["llm_called"] is False
    assert rows(conn) == []


def test_if_keeping_the_rate_limit_numbers_fails_the_429_is_still_reported(world, clients, conn, monkeypatch):
    indexed(world)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(usage, "record_snapshot", broken)
    client = make_client(world, clients, FakeLLM([LLMRateLimited(retry_after=3, rate_limit_headers=HEADERS)]))
    assert ask(client).status_code == 429


def test_if_recording_fails_the_answer_is_still_given(world, clients, conn, monkeypatch):
    indexed(world)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(usage, "record_call", broken)
    r = ask(make_client(world, clients, FakeLLM(["fine"])))
    assert r.status_code == 200 and r.json()["answer"] == "fine"


def test_the_question_the_answer_and_the_project_are_never_stored(world, clients, conn):
    indexed(world)
    ask(make_client(world, clients, FakeLLM(["an-answer-zebra"])), question="a-question-giraffe")
    dump = json.dumps([dict(r) for r in conn.execute("SELECT * FROM usage_calls")] + [dict(r) for r in conn.execute("SELECT * FROM usage_snapshots")])
    assert "giraffe" not in dump and "zebra" not in dump and str(world.repo) not in dump


# ---- /answer/stream

def stream_of(*steps, **kw):
    return FakeLLM([FakeStream(list(steps), **kw)])


def test_a_streamed_answer_records_one_call_with_the_services_numbers_and_the_headers(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, stream_of(ThinkingPiece(), ThinkingPiece(), TextPiece("hello"), TextPiece(" there"),
                                                   StreamDone("stop", "served", prompt_tokens=100, completion_tokens=84, reasoning_tokens=54), rate_limit_headers=HEADERS))
    events(post(client))
    assert rows(conn) == [{"id": 1, "at": NOW, "provider": "remote", "model": "served", "kind": "answer", "outcome": "done",
                           "prompt_tokens": 100, "thinking_tokens": 54, "answer_tokens": 30, "estimated": 0}]
    assert usage.latest_snapshot(conn, "remote")["headers"]["x-ratelimit-limit-requests"] == "1000"


def test_a_streamed_answer_without_numbers_is_estimated_from_the_pieces_and_the_text(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, stream_of(ThinkingPiece(), ThinkingPiece(), ThinkingPiece(), TextPiece("x" * 30), StreamDone("stop", "served")))
    events(post(client))
    (row,) = rows(conn)
    assert (row["prompt_tokens"], row["thinking_tokens"], row["answer_tokens"], row["estimated"], row["outcome"]) == (prompt_estimate(client), 3, estimate_tokens("x" * 30), 1, "done")


def test_a_stream_that_fails_in_the_middle_is_recorded_as_failed_with_estimates(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, stream_of(TextPiece("x" * 30), fail_with=LLMUnavailable("gone"), rate_limit_headers=HEADERS))
    evts = events(post(client))
    assert types(evts)[-1] == "error"
    (row,) = rows(conn)
    assert (row["outcome"], row["estimated"], row["answer_tokens"], row["prompt_tokens"]) == ("failed", 1, estimate_tokens("x" * 30), prompt_estimate(client))
    assert usage.latest_snapshot(conn, "remote") is not None


def test_a_stream_refused_when_it_opens_records_no_call_but_keeps_the_numbers(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, FakeLLM([LLMRateLimited(retry_after=2, rate_limit_headers=HEADERS)]))
    assert post(client).status_code == 429
    assert rows(conn) == [] and usage.latest_snapshot(conn, "remote") is not None


def test_a_streamed_no_match_records_nothing(world, clients, conn):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    events(post(make_client(world, clients)))
    assert rows(conn) == []


def test_a_streamed_answer_is_recorded_once_even_with_a_conversation_to_save(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["saved text"]))
    cid = client.post("/projects/1/conversations").json()["id"]
    events(post(client, conversation_id=cid))
    assert len(rows(conn)) == 1


# ---- a Stop (a real server and a client that walks away)

def test_a_stopped_stream_is_recorded_once_as_stopped_with_estimates(world, conn):
    indexed(world)
    talking = FakeStream([ThinkingPiece(), TextPiece("a" * 30)] + [TextPiece("b" * 3)] * 200, delay=0.05)
    with Live(world, FakeLLM([talking])) as live:
        http_conn, response = live.post_stream()
        while True:
            event = read_event(response)
            if event is None or event["type"] == "delta":
                break
        http_conn.close()                                                  # the person leaves
        assert wait_for(lambda: talking.closed)
        assert wait_for(lambda: len(rows(conn)) == 1)
        time.sleep(0.3)                                                    # and nothing is added later
    (row,) = rows(conn)
    assert (row["outcome"], row["estimated"], row["provider"], row["kind"]) == ("stopped", 1, "remote", "answer")
    assert row["answer_tokens"] >= estimate_tokens("a" * 30) and row["thinking_tokens"] == 1 and row["prompt_tokens"] > 0
    assert row["model"] == REMOTE.model                                    # the service never said which model answered: the profile's is the best name there is


def test_a_stop_before_the_model_said_anything_records_the_prompt_only_as_an_estimate(world, conn):
    indexed(world)
    silent = FakeStream([], hang_after=0)
    with Live(world, FakeLLM([silent])) as live:
        http_conn, response = live.post_stream()
        assert read_event(response)["type"] == "start"
        http_conn.close()
        assert wait_for(lambda: len(rows(conn)) == 1)
    (row,) = rows(conn)
    assert (row["outcome"], row["estimated"], row["thinking_tokens"], row["answer_tokens"]) == ("stopped", 1, None, 0)
    assert row["prompt_tokens"] > 0


# ---- the settings dialog's test call

def test_the_connection_test_is_recorded_as_a_test(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, FakeLLM([Completion("OK", "stop", "served", prompt_tokens=12, completion_tokens=2, rate_limit_headers=HEADERS)]))
    assert client.post("/settings/llm/test").status_code == 200
    (row,) = rows(conn)
    assert (row["kind"], row["outcome"], row["prompt_tokens"], row["answer_tokens"], row["estimated"]) == ("test", "done", 12, 2, 0)
    assert usage.latest_snapshot(conn, "remote") is not None


def test_a_failed_connection_test_records_no_call_but_keeps_a_429s_numbers(world, clients, conn):
    indexed(world)
    client = make_client(world, clients, FakeLLM([LLMRateLimited(retry_after=1, rate_limit_headers=HEADERS)]))
    assert client.post("/settings/llm/test").status_code == 429
    assert rows(conn) == [] and usage.latest_snapshot(conn, "remote") is not None
