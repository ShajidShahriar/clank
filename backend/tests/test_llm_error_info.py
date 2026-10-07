"""Task: streaming answers, part 6: ONE description of what a model failure looks like to the caller (`api_errors.llm_error_info`).

The normal answer route gets it from the exception handlers; the streaming route must say the same thing when a failure comes in the MIDDLE of a stream (the HTTP status
was already sent, so the failure becomes an `error` event). Both use this one table, so the window sees the same status, code and sentence either way.
"""
import pytest

from api_errors import llm_error_info
from llm import (LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMError, LLMModelNotFound, LLMNotConfigured, LLMRateLimited, LLMTimeout, LLMUnavailable)

CASES = [
    (LLMAuthError("x"), 502, "llm_auth_failed"),
    (LLMModelNotFound("x"), 502, "llm_model_not_found"),
    (LLMContextTooLong("x"), 413, "llm_context_too_long"),
    (LLMUnavailable("x"), 503, "llm_unavailable"),
    (LLMTimeout("x"), 504, "llm_timeout"),
    (LLMBadResponse("x"), 502, "llm_bad_response"),
    (LLMError("x"), 502, "llm_error"),
    (LLMNotConfigured("The answer model needs a key: add it in the settings.", env_var="K"), 503, "llm_not_configured"),
    (LLMRateLimited(retry_after=7.2), 429, "llm_rate_limited"),
]


@pytest.mark.parametrize("error,status,code", CASES)
def test_each_failure_has_its_status_and_code(error, status, code):
    info = llm_error_info(error)
    assert (info["status"], info["code"]) == (status, code)
    assert isinstance(info["message"], str) and info["message"]


def test_a_message_never_holds_the_providers_words():
    nasty = "Invalid API Key gsk_secret for org_9 at https://api.example.com/v1"
    for error in (LLMAuthError(nasty), LLMModelNotFound(nasty), LLMContextTooLong(nasty), LLMUnavailable(nasty), LLMTimeout(nasty), LLMBadResponse(nasty), LLMError(nasty)):
        assert nasty not in llm_error_info(error)["message"] and "gsk_" not in llm_error_info(error)["message"]


def test_a_rate_limit_says_how_long_and_carries_the_seconds():
    info = llm_error_info(LLMRateLimited(retry_after=7.2))
    assert "8 seconds" in info["message"] and info["retry_after"] == 8
    assert llm_error_info(LLMRateLimited(retry_after=0.2))["retry_after"] == 1
    assert "1 second." in llm_error_info(LLMRateLimited(retry_after=0.2))["message"]
    unknown = llm_error_info(LLMRateLimited(retry_after=None))
    assert "minute" in unknown["message"] and unknown.get("retry_after") is None


def test_the_not_configured_message_is_the_authored_one():
    assert llm_error_info(LLMNotConfigured("add the key in the settings", env_var="K"))["message"] == "add the key in the settings"


def test_a_subclass_is_described_as_the_most_specific_class_it_is():
    class MyAuth(LLMAuthError):
        pass
    assert llm_error_info(MyAuth("x"))["code"] == "llm_auth_failed"


def test_something_that_is_not_a_model_failure_is_not_described():
    assert llm_error_info(RuntimeError("boom")) is None
    assert llm_error_info(ValueError("x")) is None


def test_the_handlers_of_the_api_agree_with_this_table(tmp_path, monkeypatch):
    """The normal route (exception handlers) and the stream (this function) must say the same thing."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api_errors import register_error_handlers
    app = FastAPI()
    register_error_handlers(app)
    errors = {}

    @app.get("/raise/{n}")
    def raise_it(n: int):
        raise errors[n]
    for n, (error, status, code) in enumerate(CASES):
        errors[n] = error
    with TestClient(app, raise_server_exceptions=False) as client:
        for n, (error, status, code) in enumerate(CASES):
            r = client.get(f"/raise/{n}")
            info = llm_error_info(error)
            assert (r.status_code, r.json()["error"]["code"], r.json()["error"]["message"]) == (info["status"], info["code"], info["message"]), type(error).__name__
