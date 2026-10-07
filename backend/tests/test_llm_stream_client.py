"""Task: streaming answers, part 3: `OpenAICompatibleClient.stream()`, tested over REAL sockets against a local server that streams, goes silent and drops.

What is promised:
- the request is the same as `complete()`'s, with `stream: true` and `stream_options.include_usage` (so the usage arrives); the key is in the Authorization header ONLY;
- the connection is opened in `stream()`: every refusal raises THERE, before any piece, with the same error `complete()` gives (and a redirect is never followed);
- pieces arrive AS THE SERVICE SENDS THEM (not all at the end): thinking pieces (carrying no text), answer pieces, then one `StreamDone` with why it stopped, the
  model and the numbers the service counted; the usage that comes AFTER the finish chunk is read; an empty piece is no piece;
- thinking is recognised in `reasoning`, `reasoning_content` and inline `<think>` tags, and the tags never reach the answer text;
- a stream that ends before the answer is complete, is cut, is garbled, is too big or goes silent is an error (never a quietly shortened answer);
- `close()` from ANOTHER thread ends a read that is waiting on a silent service within a moment, closes the connection (the service sees it), and ends the iteration quietly;
- a service that ignores `stream` and sends one JSON answer still works; a service that rejects `stream_options` is asked again without it.
"""
import threading
import time

import pytest

from fake_llm_server import FakeLLMServer, Stream, data, drop, good_reply, groq_stream, hang, raw
from llm import (LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMModelNotFound, LLMRateLimited, LLMTimeout, LLMUnavailable, OpenAICompatibleClient, StreamDone,
                 TextPiece, ThinkingPiece)
import llm.openai_compat as openai_compat

KEY = "gsk_" + "s3cr3t" * 8
MESSAGES = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "is 17 prime?"}]


def client(server, **kw):
    kw.setdefault("api_key", KEY)
    kw.setdefault("model", "openai/gpt-oss-120b")
    return OpenAICompatibleClient(base_url=server.base_url, **kw)


def serve(stream, status=200, headers=None):
    return FakeLLMServer(lambda request: (status, headers or {}, stream))


def run(server, **kw):
    """Open a stream, read it all, return the events."""
    stream = client(server, **kw).stream(MESSAGES, max_output_tokens=300)
    return list(stream)


def kinds(events):
    return [type(e).__name__ for e in events]


# ---- the request

def test_the_request_asks_for_a_stream_with_usage_and_the_key_is_only_in_the_header():
    with serve(groq_stream()) as server:
        run(server, output_limit_param="max_completion_tokens", extra_body={"reasoning_effort": "low"})
    request = server.requests[0]
    assert request.path == "/v1/chat/completions"
    assert request.json == {"model": "openai/gpt-oss-120b", "messages": MESSAGES, "max_completion_tokens": 300, "stream": True,
                            "stream_options": {"include_usage": True}, "reasoning_effort": "low"}
    assert request.headers["authorization"] == f"Bearer {KEY}" and request.headers["accept"] == "text/event-stream" and request.headers["user-agent"].startswith("Clank/")
    assert KEY not in request.raw.decode()


def test_extra_fields_cannot_override_the_stream_fields():
    with serve(groq_stream()) as server:
        run(server, extra_body={"stream": False, "stream_options": {"include_usage": False}, "model": "other", "messages": []})
    sent = server.requests[0].json
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True} and sent["model"] == "openai/gpt-oss-120b" and sent["messages"] == MESSAGES


def test_a_client_with_no_key_sends_no_authorization():
    with serve(groq_stream()) as server:
        run(server, api_key=None)
    assert "authorization" not in server.requests[0].headers


def test_bad_arguments_are_refused_before_anything_is_sent():
    with serve(groq_stream()) as server:
        c = client(server)
        for bad in ({"messages": [], "max_output_tokens": 5}, {"messages": [{"role": "boss", "content": "x"}], "max_output_tokens": 5},
                    {"messages": MESSAGES, "max_output_tokens": 0}, {"messages": MESSAGES, "max_output_tokens": True}):
            with pytest.raises(ValueError):
                c.stream(**bad)
    assert server.requests == []


# ---- what comes back

def test_a_groq_stream_gives_thinking_then_text_then_done():
    with serve(groq_stream()) as server:
        events = run(server)
    assert kinds(events) == ["ThinkingPiece", "ThinkingPiece", "TextPiece", "TextPiece", "StreamDone"]
    assert "".join(e.text for e in events if isinstance(e, TextPiece)) == "Yes, 17 is prime."
    assert events[-1] == StreamDone(finish_reason="stop", model="openai/gpt-oss-120b", prompt_tokens=90, completion_tokens=38, reasoning_tokens=7)


def test_a_thinking_piece_carries_no_text():
    with serve(groq_stream(thinking=("secret reasoning",))) as server:
        events = run(server)
    assert ThinkingPiece() in events
    assert all("secret" not in repr(e) for e in events)


def test_pieces_arrive_as_the_service_sends_them_not_all_at_the_end():
    with serve(groq_stream(pieces=("a", "b", "c", "d"), thinking=(), delay=0.15)) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=300)
        started, first_at, last_at = time.monotonic(), None, None
        for event in stream:
            now = time.monotonic() - started
            if isinstance(event, TextPiece):
                first_at = first_at if first_at is not None else now
                last_at = now
    assert first_at is not None and last_at - first_at >= 0.3, (first_at, last_at)
    assert first_at < last_at - 0.25, "the first piece was not held back until the end"


def test_an_empty_piece_is_no_piece():
    with serve(groq_stream(pieces=("", "x", ""), thinking=("",))) as server:
        events = run(server)
    assert kinds(events) == ["TextPiece", "StreamDone"] and events[0].text == "x"


def test_the_usage_after_the_finish_chunk_is_read():
    with serve(groq_stream(usage=(11, 22, 5))) as server:
        done = run(server)[-1]
    assert (done.prompt_tokens, done.completion_tokens, done.reasoning_tokens) == (11, 22, 5)


def test_a_stream_without_usage_still_ends_with_done_and_no_numbers():
    with serve(groq_stream(usage=None)) as server:
        done = run(server)[-1]
    assert isinstance(done, StreamDone) and done.finish_reason == "stop" and done.prompt_tokens is None and done.completion_tokens is None and done.reasoning_tokens is None


def test_the_model_name_comes_from_the_stream_else_from_the_client():
    with serve(groq_stream(model="the-real-model")) as server:
        assert run(server)[-1].model == "the-real-model"
    with serve(Stream(data({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}), data("[DONE]"))) as server:
        assert run(server)[-1].model == "openai/gpt-oss-120b"


def test_an_answer_cut_by_the_length_cap_says_so():
    with serve(groq_stream(finish="length")) as server:
        assert run(server)[-1].finish_reason == "length"


def test_a_stream_that_ends_after_the_finish_chunk_without_done_is_complete():
    steps = groq_stream(usage=None).steps[:-1]                          # no [DONE]
    with serve(Stream(*steps)) as server:
        events = run(server)
    assert isinstance(events[-1], StreamDone) and events[-1].finish_reason == "stop"


def test_a_close_delimited_body_works_like_a_chunked_one():
    with serve(Stream(*groq_stream().steps, chunked=False)) as server:
        assert kinds(run(server))[-1] == "StreamDone"


def test_crlf_line_ends_comments_and_other_fields_are_fine():
    chunk = '{"model": "m", "choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}'
    finish = '{"choices": [{"delta": {}, "finish_reason": "stop"}]}'
    body = (f": keep-alive\r\n\r\nevent: message\r\nid: 7\r\ndata:{chunk}\r\n\r\ndata: {finish}\r\n\r\ndata: [DONE]\r\n\r\n").encode()
    with serve(Stream(raw(body))) as server:
        events = run(server)
    assert [e.text for e in events if isinstance(e, TextPiece)] == ["hi"] and events[-1].finish_reason == "stop"


def test_characters_beyond_ascii_survive():
    with serve(groq_stream(pieces=("café ", "日本語 ", "🙂"), thinking=())) as server:
        assert "".join(e.text for e in run(server) if isinstance(e, TextPiece)) == "café 日本語 🙂"


# ---- thinking in its three forms

def test_thinking_in_reasoning_content():
    steps = (data({"choices": [{"delta": {"reasoning_content": "hmm"}}]}), data({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}), data("[DONE]"))
    with serve(Stream(*steps)) as server:
        assert kinds(run(server)) == ["ThinkingPiece", "TextPiece", "StreamDone"]


def test_inline_think_tags_become_thinking_and_never_reach_the_text():
    pieces = ("<thi", "nk>I should", " check</th", "ink>\n\nIt is ", "prime.")
    steps = [data({"choices": [{"delta": {"content": p}}]}) for p in pieces] + [data({"choices": [{"delta": {}, "finish_reason": "stop"}]}), data("[DONE]")]
    with serve(Stream(*steps)) as server:
        events = run(server)
    assert "".join(e.text for e in events if isinstance(e, TextPiece)) == "It is prime."
    assert sum(isinstance(e, ThinkingPiece) for e in events) >= 2


def test_a_think_tag_in_the_text_of_a_model_that_has_a_reasoning_field_is_just_text():
    steps = (data({"choices": [{"delta": {"reasoning": "hmm"}}]}), data({"choices": [{"delta": {"content": "<think>not a tag</think>"}, "finish_reason": "stop"}]}), data("[DONE]"))
    with serve(Stream(*steps)) as server:
        assert "".join(e.text for e in run(server) if isinstance(e, TextPiece)) == "<think>not a tag</think>"


def test_an_unfinished_tag_at_the_end_is_not_lost():
    steps = (data({"choices": [{"delta": {"content": "<thi"}, "finish_reason": "stop"}]}), data("[DONE]"))
    with serve(Stream(*steps)) as server:
        assert "".join(e.text for e in run(server) if isinstance(e, TextPiece)) == "<thi"


# ---- what goes wrong before the first piece

@pytest.mark.parametrize("status,body,headers,error", [
    (401, {"error": {"message": "bad key gsk_leak", "code": "invalid_api_key"}}, {}, LLMAuthError),
    (403, {"error": {"message": "blocked"}}, {}, LLMAuthError),
    (404, {"error": {"message": "no such model org-123"}}, {}, LLMModelNotFound),
    (413, {"error": {"message": "too large"}}, {}, LLMContextTooLong),
    (400, {"error": {"message": "maximum context length is 8192 tokens"}}, {}, LLMContextTooLong),
    (429, {"error": {"message": "slow down. Please try again in 7.66s"}}, {}, LLMRateLimited),
    (500, {"error": {"message": "boom at 10.0.0.5"}}, {}, LLMUnavailable),
    (400, {"error": {"message": "nope"}}, {}, LLMBadResponse),
])
def test_a_refusal_raises_in_stream_with_the_same_error_as_complete(status, body, headers, error):
    with FakeLLMServer(lambda request: (status, headers, body)) as server:
        with pytest.raises(error) as caught:
            client(server).stream(MESSAGES, max_output_tokens=50)
    text = str(caught.value)
    assert "gsk_" not in text and "org-123" not in text and "10.0.0.5" not in text and "127.0.0.1" not in text and KEY not in text


def test_a_rate_limit_carries_the_wait():
    with FakeLLMServer(lambda request: (429, {"retry-after": "12"}, {"error": {"message": "slow down"}})) as server:
        with pytest.raises(LLMRateLimited) as caught:
            client(server).stream(MESSAGES, max_output_tokens=50)
    assert caught.value.retry_after == 12.0


def test_a_redirect_is_never_followed():
    with FakeLLMServer(lambda request: (302, {"location": "http://127.0.0.1:1/steal"}, b"")) as server:
        with pytest.raises(LLMUnavailable):
            client(server).stream(MESSAGES, max_output_tokens=50)
    assert len(server.requests) == 1


def test_a_service_that_cannot_be_reached_is_unavailable():
    c = OpenAICompatibleClient(base_url="http://127.0.0.1:1/v1", model="m", api_key=None, timeout=2)
    with pytest.raises(LLMUnavailable) as caught:
        c.stream(MESSAGES, max_output_tokens=50)
    assert "127.0.0.1" not in str(caught.value)


def test_a_service_that_does_not_answer_the_request_in_time_is_a_timeout():
    with serve(Stream(hang(5))) as server:
        c = client(server, timeout=0.3)
        started = time.monotonic()
        with pytest.raises(LLMTimeout):
            list(c.stream(MESSAGES, max_output_tokens=50))
    assert time.monotonic() - started < 3


def test_a_service_that_rejects_stream_options_is_asked_again_without_them():
    def handler(request):
        if "stream_options" in request.json:
            return 400, {}, {"error": {"message": "Unrecognized request argument supplied: stream_options"}}
        return 200, {}, groq_stream(usage=None)
    with FakeLLMServer(handler) as server:
        events = run(server)
    assert len(server.requests) == 2 and "stream_options" not in server.requests[1].json and server.requests[1].json["stream"] is True
    assert events[-1].finish_reason == "stop"


def test_a_400_about_something_else_is_not_retried():
    with FakeLLMServer(lambda request: (400, {}, {"error": {"message": "bad temperature"}})) as server:
        with pytest.raises(LLMBadResponse):
            client(server).stream(MESSAGES, max_output_tokens=50)
    assert len(server.requests) == 1


# ---- the rate-limit headers

def test_the_rate_limit_headers_are_kept_and_nothing_else():
    headers = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-tokens": "7510", "x-ratelimit-reset-tokens": "3.675s", "retry-after": "5",
               "set-cookie": "__cf_bm=secret", "x-request-id": "req_abc", "x-groq-region": "fra"}
    with serve(groq_stream(), headers=headers) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=50)
        assert stream.rate_limit_headers == {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-tokens": "7510", "x-ratelimit-reset-tokens": "3.675s", "retry-after": "5"}
        list(stream)


def test_a_service_that_sends_no_such_headers_gives_an_empty_dict():
    with serve(groq_stream()) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=50)
        assert stream.rate_limit_headers == {}
        list(stream)


def test_a_huge_header_value_is_cut():
    with serve(groq_stream(), headers={"x-ratelimit-reset-tokens": "9" * 5000}) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=50)
        assert len(stream.rate_limit_headers["x-ratelimit-reset-tokens"]) <= 100
        list(stream)


# ---- what goes wrong in the middle

def test_a_connection_cut_in_the_middle_is_an_error_not_a_short_answer():
    steps = [data({"choices": [{"delta": {"content": "half an ans"}}]}), drop()]
    with serve(Stream(*steps)) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=50)
        got = []
        with pytest.raises(LLMUnavailable):
            for event in stream:
                got.append(event)
    assert got == [TextPiece("half an ans")]


def test_a_stream_that_ends_with_no_finish_and_no_done_is_an_error():
    with serve(Stream(data({"choices": [{"delta": {"content": "x"}}]}))) as server:
        with pytest.raises(LLMUnavailable):
            run(server)


def test_an_empty_stream_is_an_error():
    with serve(Stream()) as server:
        with pytest.raises(LLMUnavailable):
            run(server)


def test_a_garbled_line_is_a_bad_response():
    with serve(Stream(data({"choices": [{"delta": {"content": "x"}}]}), raw(b"data: {not json\n\n"))) as server:
        with pytest.raises(LLMBadResponse):
            run(server)


@pytest.mark.parametrize("bad", ['[1, 2]', '"text"', '{"choices": 5}', '{"choices": [{"delta": {"content": 5}}]}', '{"choices": [], "usage": "x"}'])
def test_a_chunk_of_the_wrong_shape_is_a_bad_response(bad):
    with serve(Stream(raw(f"data: {bad}\n\n".encode()))) as server:
        with pytest.raises(LLMBadResponse):
            run(server)


def test_bytes_that_are_not_text_are_a_bad_response():
    with serve(Stream(raw(b"data: \xff\xfe\n\n"))) as server:
        with pytest.raises(LLMBadResponse):
            run(server)


def test_an_error_chunk_is_unavailable_and_never_repeats_the_services_words():
    with serve(Stream(data({"error": {"message": "internal failure at 10.0.0.5, org-123", "type": "server_error"}}))) as server:
        with pytest.raises(LLMUnavailable) as caught:
            run(server)
    assert "10.0.0.5" not in str(caught.value) and "org-123" not in str(caught.value)


def test_a_line_that_is_too_long_is_a_bad_response(monkeypatch):
    monkeypatch.setattr(openai_compat, "MAX_STREAM_LINE_BYTES", 100)
    with serve(Stream(raw(b"data: " + b"x" * 500 + b"\n\n"))) as server:
        with pytest.raises(LLMBadResponse):
            run(server)


def test_a_stream_that_is_too_big_is_a_bad_response(monkeypatch):
    monkeypatch.setattr(openai_compat, "MAX_REPLY_BYTES", 2000)
    steps = [data({"choices": [{"delta": {"content": "y" * 100}}]}) for _ in range(100)]
    with serve(Stream(*steps)) as server:
        with pytest.raises(LLMBadResponse):
            run(server)


def test_a_line_just_under_the_limit_is_fine(monkeypatch):
    monkeypatch.setattr(openai_compat, "MAX_STREAM_LINE_BYTES", 300)
    with serve(groq_stream(pieces=("z" * 100,), thinking=())) as server:
        assert isinstance(run(server)[-1], StreamDone)


def test_a_service_that_goes_silent_in_the_middle_is_a_timeout():
    steps = [data({"choices": [{"delta": {"content": "x"}}]}), hang(10)]
    with serve(Stream(*steps)) as server:
        c = client(server, timeout=0.4)
        stream = c.stream(MESSAGES, max_output_tokens=50)
        started = time.monotonic()
        with pytest.raises(LLMTimeout):
            list(stream)
    assert time.monotonic() - started < 4
    assert server.client_closed.wait(2), "the connection was closed after the timeout"


# ---- stopping it

def test_close_from_another_thread_ends_a_read_that_waits_on_a_silent_service():
    steps = [data({"choices": [{"delta": {"content": "x"}}]}), hang(20)]
    with serve(Stream(*steps)) as server:
        stream = client(server, timeout=30).stream(MESSAGES, max_output_tokens=50)
        events, failure = [], []

        def read():
            try:
                for event in stream:
                    events.append(event)
            except Exception as error:               # noqa: BLE001 - the test wants to see any
                failure.append(error)
        reader = threading.Thread(target=read)
        reader.start()
        time.sleep(0.3)
        assert events == [TextPiece("x")] and reader.is_alive(), "the reader is waiting on the silent service"
        started = time.monotonic()
        stream.close()
        reader.join(3)
        assert not reader.is_alive(), "the wait ended"
        assert time.monotonic() - started < 1.5
        assert failure == [], "stopping on purpose is not an error"
        assert server.client_closed.wait(2), "the service saw the connection close: it stops spending tokens"
    assert events == [TextPiece("x")], "no StreamDone: the answer was not finished"


def test_close_while_the_service_is_talking_stops_the_reading_and_the_service():
    with serve(groq_stream(pieces=tuple("abcdefghij"), thinking=(), delay=0.2)) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=300)
        got = []
        for event in stream:
            got.append(event)
            if len(got) == 2:
                stream.close()
    assert len(got) <= 3 and not any(isinstance(e, StreamDone) for e in got)
    assert server.client_closed.wait(2)


def test_close_before_reading_gives_nothing_and_closes_the_connection():
    with serve(groq_stream(delay=0.2)) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=300)
        stream.close()
        assert list(stream) == []
        assert server.client_closed.wait(2)


def test_close_can_be_called_again_and_after_the_end():
    with serve(groq_stream()) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=300)
        events = list(stream)
        stream.close()
        stream.close()
    assert isinstance(events[-1], StreamDone)


def test_a_stream_opened_in_one_thread_can_be_read_in_another():
    with serve(groq_stream()) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=300)
        result = []
        reader = threading.Thread(target=lambda: result.extend(stream))
        reader.start()
        reader.join(5)
    assert isinstance(result[-1], StreamDone)


def test_the_stream_object_never_shows_the_key():
    with serve(groq_stream()) as server:
        stream = client(server).stream(MESSAGES, max_output_tokens=300)
        assert KEY not in repr(stream) and KEY not in str(vars(stream))
        list(stream)


# ---- a service that ignores `stream`

def test_a_service_that_sends_one_json_answer_still_works():
    text = "17 is prime."
    with FakeLLMServer(lambda request: good_reply(text=text, prompt=9, completion=4)) as server:
        events = run(server)
    assert events == [TextPiece(text), StreamDone(finish_reason="stop", model="test-model", prompt_tokens=9, completion_tokens=4, reasoning_tokens=None)]


def test_a_one_json_answer_with_inline_thinking_is_separated_too():
    with FakeLLMServer(lambda request: good_reply(text="<think>hmm</think>\n\n17 is prime.")) as server:
        events = run(server)
    assert kinds(events) == ["ThinkingPiece", "TextPiece", "StreamDone"] and events[1].text == "17 is prime."


def test_a_one_json_answer_that_is_not_a_chat_answer_is_a_bad_response():
    with FakeLLMServer(lambda request: (200, {}, {"nothing": "here"})) as server:
        with pytest.raises(LLMBadResponse):
            run(server)


def test_a_one_json_answer_cut_by_the_length_cap_with_no_text_is_reported():
    with FakeLLMServer(lambda request: (200, {}, {"model": "m", "choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "length"}]})) as server:
        events = run(server)
    assert events == [StreamDone(finish_reason="length", model="m")]


# ---- gaps found by reading my own tests before the mutation run

def test_an_error_chunk_in_the_middle_fails_the_answer_even_if_the_rest_looks_fine():
    steps = (data({"choices": [{"delta": {"content": "a"}}]}), data({"error": {"message": "x"}}), data({"choices": [{"delta": {}, "finish_reason": "stop"}]}), data("[DONE]"))
    with serve(Stream(*steps)) as server:
        with pytest.raises(LLMUnavailable):
            run(server)


def test_the_usage_is_kept_when_a_later_chunk_has_none():
    steps = (data({"choices": [{"delta": {"content": "a"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
             data({"choices": []}), data("[DONE]"))
    with serve(Stream(*steps)) as server:
        done = run(server)[-1]
    assert (done.prompt_tokens, done.completion_tokens) == (5, 3)


def test_the_finish_reason_is_kept_when_a_later_chunk_has_none():
    steps = (data({"choices": [{"delta": {"content": "a"}, "finish_reason": "length"}]}), data({"choices": [{"delta": {}}]}), data("[DONE]"))
    with serve(Stream(*steps)) as server:
        assert run(server)[-1].finish_reason == "length"


def test_a_400_that_mentions_stream_options_is_only_retried_when_it_is_a_400():
    with FakeLLMServer(lambda request: (401, {}, {"error": {"message": "stream_options and a bad key"}})) as server:
        with pytest.raises(LLMAuthError):
            client(server).stream(MESSAGES, max_output_tokens=50)
    assert len(server.requests) == 1


def test_a_service_that_rejects_stream_options_twice_gives_up_after_the_second_try():
    with FakeLLMServer(lambda request: (400, {}, {"error": {"message": "stream_options is not allowed"}})) as server:
        with pytest.raises(LLMBadResponse):
            client(server).stream(MESSAGES, max_output_tokens=50)
    assert len(server.requests) == 2


def test_an_extra_stream_options_field_is_never_sent_even_on_the_retry_without_usage():
    def handler(request):
        if "stream_options" in request.json and request.json["stream_options"] == {"include_usage": True}:
            return 400, {}, {"error": {"message": "stream_options is not allowed"}}
        return 200, {}, groq_stream(usage=None)
    with FakeLLMServer(handler) as server:
        run(server, extra_body={"stream_options": {"include_usage": False, "x": 1}})
    assert len(server.requests) == 2 and "stream_options" not in server.requests[1].json


def test_the_second_refusal_is_classified_like_any_other():
    answers = iter([(400, {}, {"error": {"message": "stream_options is not allowed"}}), (400, {}, {"error": {"message": "stream_options and maximum context length"}})])
    with FakeLLMServer(lambda request: next(answers)) as server:
        with pytest.raises(LLMContextTooLong):
            client(server).stream(MESSAGES, max_output_tokens=50)


def test_a_service_that_is_slow_to_answer_the_request_is_a_timeout_when_opening():
    def handler(request):
        time.sleep(1.5)                                          # before any header: the connection is open but silent
        return 200, {}, groq_stream()
    with FakeLLMServer(handler) as server:
        started = time.monotonic()
        with pytest.raises(LLMTimeout):
            client(server, timeout=0.3).stream(MESSAGES, max_output_tokens=50)
    assert time.monotonic() - started < 1.2


def test_a_one_json_answer_that_ends_in_half_a_tag_keeps_it():
    with FakeLLMServer(lambda request: good_reply(text="<thi")) as server:
        events = run(server)
    assert "".join(e.text for e in events if isinstance(e, TextPiece)) == "<thi"


def test_a_comment_line_that_is_too_long_is_refused_too(monkeypatch):
    monkeypatch.setattr(openai_compat, "MAX_STREAM_LINE_BYTES", 600)             # the normal chunks of the fake stream are under 400 bytes
    steps = [raw(b": " + b"x" * 2000 + b"\n\n")] + list(groq_stream(pieces=("a",), thinking=()).steps)
    with serve(Stream(*steps)) as server:
        with pytest.raises(LLMBadResponse):
            run(server)


def test_done_without_a_finish_chunk_ends_the_answer_with_no_finish_reason():
    steps = (data({"choices": [{"delta": {"content": "hello"}}]}), data("[DONE]"))
    with serve(Stream(*steps)) as server:
        events = run(server)
    assert events == [TextPiece("hello"), StreamDone(finish_reason=None, model="openai/gpt-oss-120b")]


# ---- the rule "an error after we stopped it ourselves is not an error", with a reply that behaves badly on purpose

class BadReply:
    """Stands in for the HTTP reply: serves the given lines, then `after` happens (an exception or EOF). `on_line(n)` runs before line n is handed out."""
    status = 200
    msg = {}

    def __init__(self, lines, after=None, on_line=None):
        self.lines, self.after, self.on_line, self.n = list(lines), after, on_line, 0

    def getheaders(self):
        return []

    def getheader(self, name, default=None):
        return "text/event-stream" if name.lower() == "content-type" else default

    def readline(self, limit=-1):
        if self.on_line:
            self.on_line(self.n)
        if self.n < len(self.lines):
            self.n += 1
            return self.lines[self.n - 1]
        if isinstance(self.after, BaseException):
            raise self.after
        return b""


class BadConn:
    sock = None

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def stream_of(reply):
    from llm.openai_compat import OpenAIStream
    return OpenAIStream(BadConn(), reply, default_model="m", parse_whole=None)


LINE = b'data: {"choices": [{"delta": {"content": "a"}}]}\n'
NEXT = b'data: {"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]}\n'


@pytest.mark.parametrize("error", [OSError("bad file descriptor"), ValueError("I/O operation on closed file"), TimeoutError("timed out")])
def test_any_error_after_our_own_close_is_a_quiet_end(error):
    stream = stream_of(BadReply([LINE], after=error))
    events = iter(stream)
    assert next(events) == TextPiece("a")
    stream.close()
    assert list(events) == []


def test_the_same_errors_before_a_close_are_real_errors():
    for error, expected in ((OSError("reset"), LLMUnavailable), (TimeoutError("timed out"), LLMTimeout)):
        with pytest.raises(expected):
            list(stream_of(BadReply([LINE], after=error)))


def test_nothing_more_is_given_after_a_close_even_if_a_whole_line_is_already_there():
    holder = {}
    reply = BadReply([LINE, NEXT], on_line=lambda n: holder["stream"].close() if n == 1 else None)
    holder["stream"] = stream = stream_of(reply)
    got = list(stream)
    assert got == [TextPiece("a")], got
