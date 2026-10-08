"""Task: stage 2, step 2.2: `GET /usage`, `POST /usage/limits`, `DELETE /usage`.

What is promised:
- `GET /usage` is the usage report of the provider the person has chosen (see usage_report.py); it needs no key and no model: the numbers can be read even when the model
  is not set up;
- `POST /usage/limits` saves the person's limits for that provider and answers with the new report; `{"limits": {}}` clears them, `{"limits": null}` goes back to the
  suggestion; a bad set of limits is refused (422, `invalid_limits`, a sentence that does not repeat what was sent) and nothing is saved;
- `DELETE /usage` clears the counts only (the limits and the provider's own numbers stay) and answers with the new report;
- the guard (token, Host, Origin) covers all of them like every other route.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import routes_usage
import usage
import usage_limits
from llm import LLMNotConfigured
from test_answer_endpoint import clients, make_client, world  # noqa: F401  (fixtures)

REAL_NOW = routes_usage._now                       # the tests below replace it; one test checks the real one
NOW = datetime(2026, 10, 8, 13, 0, 0, tzinfo=ZoneInfo("Asia/Dhaka"))
T = NOW.timestamp()


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(routes_usage, "_now", lambda: NOW)


@pytest.fixture
def client(world, clients):
    return make_client(world, clients)


def call(conn, ago=10, provider="groq", prompt=100, thinking=20, answer=30):
    usage.record_call(conn, at=T - ago, provider=provider, model="m", kind="answer", outcome="done", prompt_tokens=prompt, thinking_tokens=thinking, answer_tokens=answer, estimated=False)


def test_the_report_is_for_the_chosen_provider_with_groqs_published_limits_by_default(client):
    r = client.get("/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "groq" and body["limits_source"] == "published" and body["published_note"] and body["now"] == T
    assert {(x["window"], x["kind"]) for x in body["limits"]} == {("minute", "requests"), ("minute", "tokens"), ("day", "requests"), ("day", "tokens")}
    assert body["closest"]["percent"] == 0.0 and body["counts"]["requests"] == 0


def test_the_report_shows_what_was_recorded(client, conn):
    call(conn)
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "990", "x-ratelimit-reset-requests": "1m26.4s"}, at=T)
    body = client.get("/usage").json()
    assert body["counts"]["requests"] == 1 and body["counts"]["prompt_tokens"] == 100
    minute_tokens = [x for x in body["limits"] if (x["window"], x["kind"]) == ("minute", "tokens")][0]
    assert (minute_tokens["used"], minute_tokens["percent"]) == (150, 1.9)
    assert body["provider_reported"][0]["kind"] == "requests" and body["provider_reported"][0]["used"] == 10


def test_another_chosen_provider_gets_its_own_report(client, conn):
    call(conn)
    client.put("/settings/llm", json={"preset": "ollama"})
    body = client.get("/usage").json()
    assert body["provider"] == "ollama" and body["limits"] == [] and body["limits_source"] == "none" and body["counts"]["requests"] == 0


def test_the_report_needs_no_key_and_no_model(world, clients, conn):
    def not_set_up():
        raise LLMNotConfigured("The answer model needs a key.", env_var="K")
    call(conn)
    r = make_client(world, clients, factory=not_set_up).get("/usage")
    assert r.status_code == 200 and r.json()["counts"]["requests"] == 1


def test_the_real_clock_is_the_persons_own_local_time_with_its_zone():
    now = REAL_NOW()
    assert now.tzinfo is not None and now.utcoffset() == datetime.now().astimezone().utcoffset()


# ---- the person's limits

def test_saving_limits_answers_with_the_new_report(client):
    r = client.post("/usage/limits", json={"limits": {"hour": {"tokens": 50000}}})
    assert r.status_code == 200
    body = r.json()
    assert body["limits_source"] == "yours" and body["published_note"] is None
    assert [(x["window"], x["kind"], x["limit"]) for x in body["limits"]] == [("hour", "tokens", 50000)]
    assert client.get("/usage").json()["limits"] == body["limits"]


def test_limits_are_saved_for_the_chosen_provider_and_not_for_another(client):
    client.put("/settings/llm", json={"preset": "ollama"})
    saved = client.post("/usage/limits", json={"limits": {"day": {"requests": 7}}}).json()
    assert saved["provider"] == "ollama" and saved["limits_source"] == "yours"
    client.put("/settings/llm", json={"preset": "groq"})
    assert client.get("/usage").json()["limits_source"] == "published"                 # groq's are untouched


def test_empty_limits_clear_them_and_null_goes_back_to_the_suggestion(client):
    client.post("/usage/limits", json={"limits": {"hour": {"tokens": 5}}})
    cleared = client.post("/usage/limits", json={"limits": {}}).json()
    assert cleared["limits"] == [] and cleared["limits_source"] == "none" and cleared["closest"] is None
    restored = client.post("/usage/limits", json={"limits": None}).json()
    assert restored["limits_source"] == "published" and len(restored["limits"]) == 4


@pytest.mark.parametrize("limits", [{"year": {"tokens": 1}}, {"day": {"tokens": 0}}, {"day": {"tokens": 1.5}}, {"day": {"tokens": "7"}}, {"day": {"words": 1}}, {"day": 5}, "all of it"])
def test_a_bad_set_of_limits_is_refused_and_nothing_is_saved(client, limits):
    r = client.post("/usage/limits", json={"limits": limits})
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] in ("invalid_limits", "invalid_request") and error["message"]
    assert client.get("/usage").json()["limits_source"] == "published"


def test_the_refusal_sentence_does_not_repeat_what_was_sent(client):
    r = client.post("/usage/limits", json={"limits": {"day": {"tokens": "a-very-odd-value"}}})
    assert r.status_code == 422 and "a-very-odd-value" not in r.text


def test_the_body_must_have_limits_and_nothing_else(client):
    assert client.post("/usage/limits", json={}).status_code == 422
    assert client.post("/usage/limits", json={"limits": {}, "extra": 1}).status_code == 422


# ---- reset the counts

def test_deleting_clears_the_counts_and_nothing_else(client, conn):
    call(conn)
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "990"}, at=T)
    client.post("/usage/limits", json={"limits": {"hour": {"tokens": 5}}})
    r = client.delete("/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["requests"] == 0 and body["limits"][0]["used"] == 0
    assert body["limits_source"] == "yours" and len(body["provider_reported"]) == 1


def test_deleting_clears_the_counts_of_every_provider(client, conn):
    call(conn, provider="groq")
    call(conn, provider="ollama")
    client.delete("/usage")
    assert usage.totals(conn, since=0)["requests"] == 0
