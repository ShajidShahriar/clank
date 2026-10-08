"""Task: the OpenAI-compatible chat client (Groq, Ollama, LM Studio, OpenRouter all speak it). Tested against a real local HTTP server.

What is promised:
- the request is a POST to `<base_url>/chat/completions` with the model, the messages, a cap on the answer's length (the parameter's NAME is a setting: Groq
  wants `max_completion_tokens`, most others `max_tokens`), `stream: false` and the profile's extra fields; the key goes in `Authorization: Bearer` and ONLY there;
- a client with no key sends no Authorization header (a local model needs none);
- a redirect is never followed (the key must not travel to another address);
- every failure becomes one of OUR errors with a message written for people: the provider's own text, the key and the URL never appear in it;
- a rate limit carries how long to wait (the Retry-After header, or "try again in 7.66s" in the body);
- the answer's text, why it stopped and the token counts come back; an answer cut off by the length cap is reported, not hidden.
"""
import socket
import threading
import time

import pytest

from fake_llm_server import FakeLLMServer, good_reply
from llm import (LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMError, LLMModelNotFound, LLMRateLimited, LLMTimeout, LLMUnavailable,
                 OpenAICompatibleClient)

KEY = "gsk_" + "s3cr3t" * 8
MESSAGES = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "what does f do?"}]


def client(server, **kw):
    kw.setdefault("api_key", KEY)
    kw.setdefault("model", "openai/gpt-oss-120b")
    return OpenAICompatibleClient(base_url=server.base_url, **kw)


def error_reply(status, message="provider words", code=None, headers=None):
    return status, headers or {}, {"error": {"message": message, "type": "t", "code": code}}


# ---- the request

def test_the_request_has_the_model_the_messages_the_cap_and_no_streaming():
    with FakeLLMServer() as server:
        client(server, output_limit_param="max_completion_tokens", extra_body={"reasoning_effort": "low"}).complete(MESSAGES, max_output_tokens=300)
    request = server.requests[0]
    assert request.path == "/v1/chat/completions"
    assert request.json == {"model": "openai/gpt-oss-120b", "messages": MESSAGES, "max_completion_tokens": 300, "stream": False, "reasoning_effort": "low"}


def test_the_name_of_the_length_cap_is_a_setting():
    with FakeLLMServer() as server:
        client(server).complete(MESSAGES, max_output_tokens=50)                       # default: max_tokens, what most servers know
        client(server, output_limit_param="max_completion_tokens").complete(MESSAGES, max_output_tokens=50)
    assert "max_tokens" in server.requests[0].json and "max_completion_tokens" not in server.requests[0].json
    assert "max_completion_tokens" in server.requests[1].json and "max_tokens" not in server.requests[1].json


def test_extra_fields_can_not_override_the_ones_the_client_controls():
    with FakeLLMServer() as server:
        client(server, extra_body={"stream": True, "model": "other", "messages": [], "reasoning_effort": "low"}).complete(MESSAGES, max_output_tokens=10)
    sent = server.requests[0].json
    assert sent["stream"] is False and sent["model"] == "openai/gpt-oss-120b" and sent["messages"] == MESSAGES and sent["reasoning_effort"] == "low"


def test_the_key_goes_in_the_authorization_header_and_nowhere_else():
    with FakeLLMServer() as server:
        client(server).complete(MESSAGES, max_output_tokens=10)
    request = server.requests[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in request.path and KEY.encode() not in request.raw


def test_no_key_means_no_authorization_header():
    with FakeLLMServer() as server:
        client(server, api_key=None).complete(MESSAGES, max_output_tokens=10)
    assert "authorization" not in server.requests[0].headers


def test_it_says_who_it_is_not_python_urllib():
    """Groq's edge refuses the default Python user agent, so the client names itself."""
    with FakeLLMServer() as server:
        client(server).complete(MESSAGES, max_output_tokens=10)
    agent = server.requests[0].headers["user-agent"]
    assert agent.startswith("Clank/") and "urllib" not in agent.lower()


def test_a_trailing_slash_on_the_base_url_does_not_double_the_slash():
    with FakeLLMServer() as server:
        OpenAICompatibleClient(base_url=server.base_url + "/", model="m").complete(MESSAGES, max_output_tokens=10)
    assert server.requests[0].path == "/v1/chat/completions"


def test_messages_are_checked_before_anything_is_sent():
    with FakeLLMServer() as server:
        c = client(server)
        for bad in ([], "hello", [{"role": "user"}], [{"role": "robot", "content": "x"}], [{"role": "user", "content": 5}], [{"content": "x"}], [5]):
            with pytest.raises(ValueError):
                c.complete(bad, max_output_tokens=10)
        for bad_cap in (0, -1, 1.5, True, "10"):
            with pytest.raises(ValueError):
                c.complete(MESSAGES, max_output_tokens=bad_cap)
    assert server.requests == []


# ---- the answer

def test_the_answer_comes_back_with_its_finish_reason_and_token_counts():
    with FakeLLMServer(lambda r: good_reply("hello there", "stop", 120, 33, "served-model")) as server:
        out = client(server).complete(MESSAGES, max_output_tokens=100)
    assert (out.text, out.finish_reason, out.prompt_tokens, out.completion_tokens, out.model) == ("hello there", "stop", 120, 33, "served-model")


def test_an_answer_cut_by_the_length_cap_is_reported_and_an_empty_one_is_not_an_error():
    with FakeLLMServer(lambda r: good_reply("", "length")) as server:
        out = client(server).complete(MESSAGES, max_output_tokens=5)
    assert out.text == "" and out.finish_reason == "length"
    with FakeLLMServer(lambda r: (200, {}, {"choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "length"}]})) as server:
        out = client(server).complete(MESSAGES, max_output_tokens=5)
    assert out.text == "" and out.finish_reason == "length", "a reasoning model can spend the whole cap thinking: no text, and that is said"


def test_missing_usage_is_none_not_a_crash():
    with FakeLLMServer(lambda r: (200, {}, {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})) as server:
        out = client(server).complete(MESSAGES, max_output_tokens=5)
    assert out.prompt_tokens is None and out.completion_tokens is None and out.model == "openai/gpt-oss-120b"


@pytest.mark.parametrize("body", [b"not json at all", b"[]", b'{"choices": []}', b'{"choices": [{}]}', b'{"choices": [{"message": {"content": 5}}]}',
                                  b'{"choices": "x"}', b'{}', b'{"choices": [{"message": {"content": "x"}, "finish_reason": 7}]}'])
def test_an_answer_that_is_not_a_chat_answer_is_a_bad_response(body):
    with FakeLLMServer(lambda r: (200, {}, body)) as server:
        with pytest.raises(LLMBadResponse):
            client(server).complete(MESSAGES, max_output_tokens=10)


# ---- the errors

@pytest.mark.parametrize("reply, expected", [
    (error_reply(401, "Invalid API Key"), LLMAuthError),
    (error_reply(403, "Forbidden"), LLMAuthError),
    (error_reply(404, "The model `x` does not exist"), LLMModelNotFound),
    (error_reply(413, "Request too large for model in organization org_123 on tokens per minute (TPM): Limit 8000, Requested 9000"), LLMContextTooLong),
    (error_reply(400, "maximum context length is 131072 tokens", code="context_length_exceeded"), LLMContextTooLong),
    (error_reply(400, "This model's maximum context length is 8192 tokens. Please reduce the length of the messages."), LLMContextTooLong),
    (error_reply(400, "something else was wrong with the request"), LLMBadResponse),
    (error_reply(422, "unprocessable"), LLMBadResponse),
    (error_reply(500, "boom"), LLMUnavailable),
    (error_reply(502, "bad gateway"), LLMUnavailable),
    (error_reply(503, "over capacity"), LLMUnavailable),
    (error_reply(408, "request timeout"), LLMTimeout),
    (error_reply(429, "Rate limit reached"), LLMRateLimited),
])
def test_each_failure_becomes_one_of_our_errors(reply, expected):
    with FakeLLMServer(lambda r: reply) as server:
        with pytest.raises(expected) as caught:
            client(server).complete(MESSAGES, max_output_tokens=10)
    assert isinstance(caught.value, LLMError)


def test_a_failure_with_a_body_that_is_not_json_is_still_classified_by_its_status():
    with FakeLLMServer(lambda r: (401, {"content-type": "text/html"}, b"<html>Access denied. error code: 1010</html>")) as server:
        with pytest.raises(LLMAuthError):
            client(server).complete(MESSAGES, max_output_tokens=10)


def test_no_error_message_holds_the_key_the_url_or_the_providers_own_words():
    words = "Invalid API Key sk-live-abc123 for organization org_secret at http://internal.example/v1"
    for status in (400, 401, 403, 404, 413, 422, 429, 500, 503):
        with FakeLLMServer(lambda r, status=status: error_reply(status, words, headers={"retry-after": "3"})) as server:
            with pytest.raises(LLMError) as caught:
                client(server).complete(MESSAGES, max_output_tokens=10)
            text = f"{caught.value} {caught.value!r} {caught.value.args}"
            for secret in (KEY, "sk-live-abc123", "org_secret", "internal.example", server.base_url, "127.0.0.1"):
                assert secret not in text, (status, secret, text)


def test_the_client_never_shows_its_key():
    with FakeLLMServer() as server:
        c = client(server)
        assert KEY not in repr(c) and KEY not in str(c) and KEY not in str(vars(c).get("extra_body", ""))


# ---- rate limits

def test_a_rate_limit_carries_the_retry_after_header():
    with FakeLLMServer(lambda r: error_reply(429, "x", headers={"retry-after": "12"})) as server:
        with pytest.raises(LLMRateLimited) as caught:
            client(server).complete(MESSAGES, max_output_tokens=10)
    assert caught.value.retry_after == 12.0


RATE_HEADERS = {"x-ratelimit-limit-requests": "1000", "X-RateLimit-Remaining-Tokens": "7000", "retry-after": "3", "set-cookie": "never", "x-request-id": "abc"}
KEPT = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-tokens": "7000", "retry-after": "3"}


def test_a_good_reply_carries_only_the_rate_limit_headers():
    status, _, body = good_reply()
    with FakeLLMServer(lambda r: (status, RATE_HEADERS, body)) as server:
        completion = client(server).complete(MESSAGES, max_output_tokens=10)
    assert completion.rate_limit_headers == KEPT


def test_a_reply_with_no_rate_limit_headers_has_none():
    with FakeLLMServer(lambda r: good_reply()) as server:
        assert client(server).complete(MESSAGES, max_output_tokens=10).rate_limit_headers == {}


def test_a_429_carries_the_rate_limit_headers_the_service_sent_with_it():
    with FakeLLMServer(lambda r: error_reply(429, "x", headers=RATE_HEADERS)) as server:
        with pytest.raises(LLMRateLimited) as caught:
            client(server).complete(MESSAGES, max_output_tokens=10)
    assert caught.value.rate_limit_headers == KEPT


def test_a_long_header_value_is_cut():
    status, _, body = good_reply()
    with FakeLLMServer(lambda r: (status, {"x-ratelimit-limit-requests": "9" * 500}, body)) as server:
        value = client(server).complete(MESSAGES, max_output_tokens=10).rate_limit_headers["x-ratelimit-limit-requests"]
    assert value == "9" * 100


def test_a_rate_limit_without_the_header_reads_the_wait_from_the_message():
    cases = [("Rate limit reached. Please try again in 7.66s.", 7.66), ("Please try again in 2m30s", 150.0), ("try again in 450ms", 0.45), ("try again in 1m", 60.0),
             ("Rate limit reached.", None)]
    for message, expected in cases:
        with FakeLLMServer(lambda r, m=message: error_reply(429, m)) as server:
            with pytest.raises(LLMRateLimited) as caught:
                client(server).complete(MESSAGES, max_output_tokens=10)
        assert caught.value.retry_after == pytest.approx(expected) if expected is not None else caught.value.retry_after is None, message


def test_a_silly_retry_after_is_ignored_or_capped():
    for header, expected in (("abc", None), ("-5", None), ("", None), ("999999", 3600.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None)):
        with FakeLLMServer(lambda r, h=header: error_reply(429, "x", headers={"retry-after": h})) as server:
            with pytest.raises(LLMRateLimited) as caught:
                client(server).complete(MESSAGES, max_output_tokens=10)
        assert caught.value.retry_after == expected, header


# ---- the network

def test_a_closed_port_is_unavailable():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(LLMUnavailable):
        OpenAICompatibleClient(base_url=f"http://127.0.0.1:{port}/v1", model="m", api_key=KEY).complete(MESSAGES, max_output_tokens=10)


def test_a_server_that_never_answers_is_a_timeout():
    release = threading.Event()

    def slow(request):
        release.wait(3)
        return good_reply()
    with FakeLLMServer(slow) as server:
        started = time.monotonic()
        with pytest.raises(LLMTimeout):
            client(server, timeout=0.3).complete(MESSAGES, max_output_tokens=10)
        assert time.monotonic() - started < 2.5
        release.set()


def test_a_redirect_is_not_followed_so_the_key_never_travels_on():
    """302 is the kind urllib DOES follow for a POST (as a GET, keeping the Authorization header): without the guard the other address would get the key."""
    for status in (301, 302, 303, 307, 308):
        with FakeLLMServer() as elsewhere:
            with FakeLLMServer(lambda r, s=status: (s, {"location": elsewhere.base_url + "/chat/completions"}, b"")) as server:
                with pytest.raises(LLMUnavailable):
                    client(server).complete(MESSAGES, max_output_tokens=10)
        assert elsewhere.requests == [], f"{status}: the other address received a request, and the key with it"


def test_a_timeout_while_connecting_is_a_timeout_too(monkeypatch):
    """The read-timeout path is tested above with a slow server; a connect timeout arrives wrapped in URLError."""
    import urllib.error
    c = OpenAICompatibleClient(base_url="http://127.0.0.1:1/v1", model="m")

    def refuse(*args, **kwargs):
        raise urllib.error.URLError(TimeoutError("timed out"))
    monkeypatch.setattr(c._opener, "open", refuse)
    with pytest.raises(LLMTimeout):
        c.complete(MESSAGES, max_output_tokens=10)


@pytest.mark.parametrize("usage, expected", [
    ({"prompt_tokens": True, "completion_tokens": False}, (None, None)), ({"prompt_tokens": -1, "completion_tokens": 1.5}, (None, None)),
    ({"prompt_tokens": "5", "completion_tokens": None}, (None, None)), ({"prompt_tokens": 0, "completion_tokens": 12}, (0, 12)), ("not a dict", (None, None)),
])
def test_token_counts_are_whole_numbers_or_none(usage, expected):
    reply = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}], "usage": usage}
    with FakeLLMServer(lambda r: (200, {}, reply)) as server:
        out = client(server).complete(MESSAGES, max_output_tokens=10)
    assert (out.prompt_tokens, out.completion_tokens) == expected


def test_the_options_are_checked():
    for kw in ({"model": ""}, {"timeout": 0}, {"timeout": -1}, {"output_limit_param": ""}, {"output_limit_param": "stream"}, {"extra_body": "x"}, {"api_key": 5}):
        with pytest.raises(ValueError):
            OpenAICompatibleClient(**{"base_url": "http://127.0.0.1:1/v1", "model": "m", **kw})
    for url in ("", "ftp://x/v1", "not a url", "http://", "//x/v1"):
        with pytest.raises(ValueError):
            OpenAICompatibleClient(base_url=url, model="m")


def test_a_key_is_never_sent_in_clear_text_to_a_remote_address():
    with pytest.raises(ValueError, match="https"):
        OpenAICompatibleClient(base_url="http://api.example.com/v1", model="m", api_key=KEY)
    OpenAICompatibleClient(base_url="https://api.example.com/v1", model="m", api_key=KEY)
    OpenAICompatibleClient(base_url="http://localhost:11434/v1", model="m", api_key=KEY)
    OpenAICompatibleClient(base_url="http://127.0.0.1:1234/v1", model="m")
    OpenAICompatibleClient(base_url="http://api.example.com/v1", model="m")                    # no key, nothing secret to protect: allowed


def test_an_extra_stream_options_field_is_never_sent_by_complete():
    with FakeLLMServer() as server:
        client(server, extra_body={"stream_options": {"include_usage": True}, "reasoning_effort": "low"}).complete(MESSAGES, max_output_tokens=10)
    assert "stream_options" not in server.requests[0].json and server.requests[0].json["reasoning_effort"] == "low"


def test_the_thinking_tokens_are_read_when_the_service_reports_them():
    reply = good_reply(text="x")
    reply[2]["usage"]["completion_tokens_details"] = {"reasoning_tokens": 5}
    with FakeLLMServer(lambda request: reply) as server:
        completion = client(server).complete(MESSAGES, max_output_tokens=10)
    assert completion.reasoning_tokens == 5 and completion.completion_tokens == 7


def test_the_thinking_tokens_are_none_when_the_service_does_not_report_them():
    with FakeLLMServer() as server:
        assert client(server).complete(MESSAGES, max_output_tokens=10).reasoning_tokens is None
    reply = good_reply(text="x")
    reply[2]["usage"]["completion_tokens_details"] = {"reasoning_tokens": "5"}
    with FakeLLMServer(lambda request: reply) as server:
        assert client(server).complete(MESSAGES, max_output_tokens=10).reasoning_tokens is None
