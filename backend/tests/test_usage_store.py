"""The usage store: one row per model call (when, which provider and model, how many tokens, whether they are estimates) and the provider's latest rate-limit
numbers. It never holds a question, an answer, a project or a path."""
import pytest

import db
import usage


def _call(conn, **over):
    fields = dict(at=1000.0, provider="groq", model="m", kind="answer", outcome="done", prompt_tokens=100, thinking_tokens=10, answer_tokens=50, estimated=False)
    fields.update(over)
    return usage.record_call(conn, **fields)


def test_a_call_row_holds_no_question_no_answer_no_project(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(usage_calls)")}
    assert columns == {"id", "at", "provider", "model", "kind", "outcome", "prompt_tokens", "thinking_tokens", "answer_tokens", "estimated"}


def test_totals_add_up_what_was_recorded(conn):
    _call(conn)
    _call(conn, at=1010.0, prompt_tokens=200, thinking_tokens=None, answer_tokens=70, estimated=True)
    totals = usage.totals(conn, since=0)
    assert totals == {"requests": 2, "prompt_tokens": 300, "thinking_tokens": 10, "answer_tokens": 120, "estimated_calls": 1}


def test_totals_count_only_calls_inside_the_window(conn):
    _call(conn, at=100.0)
    _call(conn, at=200.0)
    _call(conn, at=300.0)
    assert usage.totals(conn, since=200.0)["requests"] == 2          # the start of the window counts
    assert usage.totals(conn, since=200.1)["requests"] == 1
    assert usage.totals(conn, since=301.0) == {"requests": 0, "prompt_tokens": 0, "thinking_tokens": 0, "answer_tokens": 0, "estimated_calls": 0}


def test_totals_can_be_asked_for_one_provider_only(conn):
    _call(conn, provider="groq")
    _call(conn, provider="ollama", prompt_tokens=5)
    assert usage.totals(conn, since=0, provider="ollama")["prompt_tokens"] == 5
    assert usage.totals(conn, since=0)["requests"] == 2


@pytest.mark.parametrize("over", [
    dict(kind="chat"), dict(outcome="ok"), dict(provider=""), dict(provider=None),
    dict(prompt_tokens=-1), dict(thinking_tokens=True), dict(answer_tokens=1.5), dict(at="now"), dict(at=True), dict(at=float("nan")), dict(estimated=1),
])
def test_a_bad_call_is_refused_and_nothing_is_stored(conn, over):
    with pytest.raises(ValueError):
        _call(conn, **over)
    assert usage.totals(conn, since=0)["requests"] == 0


def test_the_database_itself_refuses_a_bad_kind_or_outcome(conn):
    with pytest.raises(Exception):
        conn.execute("INSERT INTO usage_calls (at, provider, kind, outcome, estimated) VALUES (1, 'g', 'x', 'done', 0)")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO usage_calls (at, provider, kind, outcome, estimated) VALUES (1, 'g', 'answer', 'x', 0)")


def test_clearing_the_counts_keeps_the_provider_numbers(conn):
    _call(conn)
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "1000"}, at=5.0)
    usage.clear_calls(conn)
    assert usage.totals(conn, since=0)["requests"] == 0
    assert usage.latest_snapshot(conn, "groq") is not None


def test_usage_is_user_data_and_survives_a_schema_change(conn):
    _call(conn)
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "1000"}, at=5.0)
    conn.execute("PRAGMA user_version = 0")                          # makes init_db rebuild the files and chunks tables
    conn.commit()
    db.init_db()
    assert usage.totals(conn, since=0)["requests"] == 1
    assert usage.latest_snapshot(conn, "groq") is not None


def test_a_snapshot_keeps_only_rate_limit_headers_and_the_newest_wins(conn):
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "1000", "set-cookie": "secret", "x-request-id": "abc", "Retry-After": "3"}, at=10.0)
    first = usage.latest_snapshot(conn, "groq")
    assert first == {"headers": {"x-ratelimit-limit-requests": "1000", "retry-after": "3"}, "at": 10.0}
    usage.record_snapshot(conn, "groq", {"x-ratelimit-remaining-requests": "7"}, at=20.0)
    assert usage.latest_snapshot(conn, "groq") == {"headers": {"x-ratelimit-remaining-requests": "7"}, "at": 20.0}


def test_a_snapshot_with_no_rate_limit_headers_changes_nothing(conn):
    usage.record_snapshot(conn, "groq", {"x-ratelimit-remaining-requests": "7"}, at=20.0)
    usage.record_snapshot(conn, "groq", {"content-type": "text/event-stream"}, at=30.0)
    assert usage.latest_snapshot(conn, "groq")["at"] == 20.0
    assert usage.latest_snapshot(conn, "other") is None


def test_snapshots_are_kept_per_provider_and_a_long_value_is_cut(conn):
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "1" * 500}, at=1.0)
    usage.record_snapshot(conn, "ollama", {"x-ratelimit-limit-requests": "9"}, at=2.0)
    assert len(usage.latest_snapshot(conn, "groq")["headers"]["x-ratelimit-limit-requests"]) <= 64
    assert usage.latest_snapshot(conn, "ollama")["headers"] == {"x-ratelimit-limit-requests": "9"}


def test_the_oldest_call_in_a_window_is_found_per_provider(conn):
    _call(conn, at=100.0)
    _call(conn, at=300.0)
    _call(conn, at=50.0, provider="ollama")
    assert usage.oldest_call_at(conn, since=0, provider="groq") == 100.0
    assert usage.oldest_call_at(conn, since=150.0, provider="groq") == 300.0
    assert usage.oldest_call_at(conn, since=400.0, provider="groq") is None
    assert usage.oldest_call_at(conn, since=0, provider="nobody") is None
