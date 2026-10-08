"""The usage report for one provider: each limit with what was used inside its window, the provider's own numbers, the limit the person is closest to, and the counts.

A limit counts the calls of THIS provider made inside its window: tokens are prompt + thinking + answer (thinking is part of the completion, counted once), requests are
calls (a connection test is a call too). Percent is not capped (125 means over the limit); `reached` is used >= limit."""
from datetime import datetime
from zoneinfo import ZoneInfo

import usage
import usage_limits
import usage_report

DHAKA = ZoneInfo("Asia/Dhaka")
NOW = datetime(2026, 10, 8, 13, 0, 0, tzinfo=DHAKA)
T = NOW.timestamp()


def call(conn, ago, provider="groq", prompt=100, thinking=20, answer=30, estimated=False, kind="answer"):
    usage.record_call(conn, at=T - ago, provider=provider, model="m", kind=kind, outcome="done", prompt_tokens=prompt, thinking_tokens=thinking, answer_tokens=answer, estimated=estimated)


def report(conn, provider="groq"):
    return usage_report.build_report(conn, provider=provider, model="m", now=NOW)


def by(rows, window, kind):
    (found,) = [r for r in rows if r["window"] == window and r["kind"] == kind]
    return found


def test_nothing_set_and_nothing_known_is_an_empty_report(conn):
    r = report(conn, provider="ollama")
    assert r["limits"] == [] and r["provider_reported"] == [] and r["closest"] is None and r["limits_source"] == "none" and r["published_note"] is None
    assert r["provider"] == "ollama" and r["model"] == "m" and r["now"] == T
    assert r["counts"] == {"requests": 0, "prompt_tokens": 0, "thinking_tokens": 0, "answer_tokens": 0, "estimated_calls": 0, "thinking_share": None}


def test_a_rolling_limit_counts_the_last_minute_and_says_when_its_oldest_call_leaves(conn):
    usage_limits.save_limits(conn, "groq", {"minute": {"tokens": 8000, "requests": 4}})
    call(conn, ago=30, prompt=3000, thinking=1000, answer=2000)
    call(conn, ago=90)                                                       # a minute and a half ago: outside
    r = report(conn)
    tokens = by(r["limits"], "minute", "tokens")
    assert tokens == {"window": "minute", "kind": "tokens", "limit": 8000, "used": 6000, "percent": 75.0, "reached": False, "rolling": True,
                      "resets_at": T + 30, "resets_in_seconds": 30.0, "source": "yours"}
    requests = by(r["limits"], "minute", "requests")
    assert (requests["used"], requests["percent"]) == (1, 25.0)


def test_a_rolling_limit_with_no_calls_has_no_reset_to_wait_for(conn):
    usage_limits.save_limits(conn, "groq", {"minute": {"tokens": 100}})
    row = by(report(conn)["limits"], "minute", "tokens")
    assert (row["used"], row["resets_at"], row["resets_in_seconds"]) == (0, None, None)


def test_a_calendar_limit_counts_since_local_midnight_and_resets_at_the_next(conn):
    usage_limits.save_limits(conn, "groq", {"day": {"requests": 10}})
    call(conn, ago=3600)                                                     # 12:00 today
    call(conn, ago=13 * 3600 + 60)                                           # 23:59 yesterday: outside
    row = by(report(conn)["limits"], "day", "requests")
    midnight = datetime(2026, 10, 9, tzinfo=DHAKA).timestamp()
    assert (row["used"], row["rolling"], row["resets_at"], row["resets_in_seconds"]) == (1, False, midnight, midnight - T)


def test_requests_count_every_call_including_a_connection_test(conn):
    usage_limits.save_limits(conn, "groq", {"hour": {"requests": 2}})
    call(conn, ago=10)
    call(conn, ago=20, kind="test")
    row = by(report(conn)["limits"], "hour", "requests")
    assert (row["used"], row["percent"], row["reached"]) == (2, 100.0, True)


def test_the_percent_is_not_capped_and_unknown_token_counts_add_nothing(conn):
    usage_limits.save_limits(conn, "groq", {"hour": {"tokens": 100}})
    call(conn, ago=10, prompt=100, thinking=None, answer=25)
    row = by(report(conn)["limits"], "hour", "tokens")
    assert (row["used"], row["percent"], row["reached"]) == (125, 125.0, True)


def test_another_providers_calls_do_not_count(conn):
    usage_limits.save_limits(conn, "groq", {"hour": {"requests": 5}})
    call(conn, ago=10, provider="ollama")
    assert by(report(conn)["limits"], "hour", "requests")["used"] == 0


def test_limits_come_in_window_order_tokens_before_requests(conn):
    usage_limits.save_limits(conn, "groq", {"week": {"requests": 1}, "minute": {"requests": 1, "tokens": 1}, "day": {"tokens": 1}})
    assert [(r["window"], r["kind"]) for r in report(conn)["limits"]] == [("minute", "tokens"), ("minute", "requests"), ("day", "tokens"), ("week", "requests")]


def test_groq_with_nothing_saved_shows_the_published_limits_and_says_so(conn):
    r = report(conn)
    assert r["limits_source"] == "published" and r["published_note"] == usage_limits.PUBLISHED_NOTE
    assert {(x["window"], x["kind"]) for x in r["limits"]} == {("minute", "requests"), ("minute", "tokens"), ("day", "requests"), ("day", "tokens")}
    assert all(x["source"] == "published" for x in r["limits"])
    usage_limits.save_limits(conn, "groq", {"day": {"tokens": 5}})
    r = report(conn)
    assert r["limits_source"] == "yours" and r["published_note"] is None


def test_the_report_says_whether_the_provider_has_a_suggested_set_to_go_back_to(conn):
    assert report(conn)["has_suggestion"] is True
    assert report(conn, provider="ollama")["has_suggestion"] is False


def test_the_providers_own_numbers_are_shown_as_they_would_be_now(conn):
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "6000", "x-ratelimit-reset-tokens": "20s"}, at=T - 10)
    (row,) = report(conn)["provider_reported"]
    assert (row["kind"], row["used"], row["percent"], row["full_again_in_seconds"]) == ("tokens", 1000, 12.5, 10.0)


def test_the_closest_limit_is_the_highest_percent_of_all_of_them(conn):
    usage_limits.save_limits(conn, "groq", {"minute": {"tokens": 10000}, "day": {"requests": 100}})
    call(conn, ago=10, prompt=1000, thinking=0, answer=0)                      # 10 % of the minute's tokens, 1 % of the day's requests
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "500", "x-ratelimit-reset-requests": "10m"}, at=T)
    closest = report(conn)["closest"]
    assert closest == {"source": "provider", "window": "day", "kind": "requests", "limit": 1000, "used": 500, "percent": 50.0, "reached": False, "resets_in_seconds": 600.0}


def test_a_limit_of_the_person_wins_a_tie_with_the_provider(conn):
    usage_limits.save_limits(conn, "groq", {"minute": {"requests": 2}})
    call(conn, ago=10)
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-requests": "10", "x-ratelimit-remaining-requests": "5", "x-ratelimit-reset-requests": "1m"}, at=T)
    closest = report(conn)["closest"]
    assert (closest["source"], closest["percent"]) == ("yours", 50.0)


def test_closest_has_the_same_fields_whatever_its_source(conn):
    usage_limits.save_limits(conn, "groq", {"minute": {"requests": 2}})
    call(conn, ago=10)
    assert set(report(conn)["closest"]) == {"source", "window", "kind", "limit", "used", "percent", "reached", "resets_in_seconds"}


def test_the_counts_are_all_calls_of_this_provider_with_the_thinking_share(conn):
    call(conn, ago=100000, prompt=100, thinking=30, answer=70, estimated=True)       # long ago: counts are not a window
    call(conn, ago=10, prompt=50, thinking=10, answer=10)
    call(conn, ago=10, provider="ollama", prompt=7)
    assert report(conn)["counts"] == {"requests": 2, "prompt_tokens": 150, "thinking_tokens": 40, "answer_tokens": 80, "estimated_calls": 1, "thinking_share": 0.3333}


def test_a_provider_bucket_that_is_empty_counts_as_reached(conn):
    usage.record_snapshot(conn, "groq", {"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "40s"}, at=T)
    usage_limits.save_limits(conn, "groq", {})
    closest = report(conn)["closest"]
    assert (closest["source"], closest["percent"], closest["reached"], closest["resets_in_seconds"]) == ("provider", 100.0, True, 40.0)
