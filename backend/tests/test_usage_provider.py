"""What the provider itself says about its limits (its `x-ratelimit-*` headers), as of its last reply.

Groq's `reset-*` headers are the time until the bucket is FULL AGAIN (a refill, not midnight), so the numbers are shown as they would be NOW: the used part drains
linearly to zero at the reset moment, and a snapshot past its reset shows a full bucket. Groq has no header for its daily token limit: that never appears here."""
import pytest

import usage_provider as up

GROQ = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "990", "x-ratelimit-reset-requests": "10m0s",
        "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "6000", "x-ratelimit-reset-tokens": "20s"}


@pytest.mark.parametrize("text, seconds", [
    ("3.675s", 3.675), ("1m26.4s", 86.4), ("2m52.8s", 172.8), ("450ms", 0.45), ("2h", 7200.0), ("1h30m", 5400.0), ("1d2h", 93600.0), ("6m0s", 360.0), ("0s", 0.0),
    ("12", 12.0), ("1.5", 1.5),
])
def test_durations_in_the_forms_providers_use(text, seconds):
    assert up.parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", [None, "", "soon", "-5s", "5x", "1m-2s", "s", "1e999", "nan", "inf", "9999999d", 5, "5 s"])
def test_durations_that_are_not_durations_are_none(text):
    assert up.parse_duration(text) is None


def row(rows, kind):
    (found,) = [r for r in rows if r["kind"] == kind]
    return found


def test_a_fresh_snapshot_shows_what_the_provider_said():
    rows = up.provider_rows("groq", {"headers": GROQ, "at": 1000.0}, now=1000.0)
    requests, tokens = row(rows, "requests"), row(rows, "tokens")
    assert requests == {"kind": "requests", "window": "day", "limit": 1000, "remaining": 990, "used": 10, "percent": 1.0, "full_again_in_seconds": 600.0, "age_seconds": 0.0}
    assert tokens == {"kind": "tokens", "window": "minute", "limit": 8000, "remaining": 6000, "used": 2000, "percent": 25.0, "full_again_in_seconds": 20.0, "age_seconds": 0.0}


def test_the_used_part_drains_linearly_towards_the_reset():
    tokens = row(up.provider_rows("groq", {"headers": GROQ, "at": 1000.0}, now=1010.0), "tokens")        # half of the 20 s have passed
    assert (tokens["remaining"], tokens["used"], tokens["percent"], tokens["full_again_in_seconds"], tokens["age_seconds"]) == (7000, 1000, 12.5, 10.0, 10.0)


def test_a_snapshot_past_its_reset_shows_a_full_bucket():
    tokens = row(up.provider_rows("groq", {"headers": GROQ, "at": 1000.0}, now=1020.0), "tokens")
    assert (tokens["remaining"], tokens["used"], tokens["percent"], tokens["full_again_in_seconds"]) == (8000, 0, 0.0, 0.0)
    assert row(up.provider_rows("groq", {"headers": GROQ, "at": 1000.0}, now=5000.0), "tokens")["remaining"] == 8000


def test_without_a_reset_time_nothing_drains():
    headers = {k: v for k, v in GROQ.items() if "reset" not in k}
    tokens = row(up.provider_rows("groq", {"headers": headers, "at": 1000.0}, now=5000.0), "tokens")
    assert (tokens["remaining"], tokens["full_again_in_seconds"], tokens["age_seconds"]) == (6000, None, 4000.0)


def test_a_clock_that_went_back_does_not_make_a_negative_age():
    assert row(up.provider_rows("groq", {"headers": GROQ, "at": 1000.0}, now=900.0), "tokens")["age_seconds"] == 0.0


def test_a_kind_with_a_missing_or_bad_number_is_left_out():
    headers = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "lots"}
    assert up.provider_rows("groq", {"headers": headers, "at": 1.0}, now=1.0) == []
    headers = {"x-ratelimit-limit-tokens": "0", "x-ratelimit-remaining-tokens": "0"}
    assert up.provider_rows("groq", {"headers": headers, "at": 1.0}, now=1.0) == []        # a limit of 0 can have no percent
    assert up.provider_rows("groq", None, now=1.0) == []


def test_a_remaining_above_the_limit_is_cut_to_the_limit():
    headers = {"x-ratelimit-limit-tokens": "100", "x-ratelimit-remaining-tokens": "150"}
    assert row(up.provider_rows("groq", {"headers": headers, "at": 0.0}, now=0.0), "tokens")["used"] == 0


def test_the_window_is_only_named_for_a_provider_whose_windows_are_known():
    assert row(up.provider_rows("someone-else", {"headers": GROQ, "at": 1000.0}, now=1000.0), "tokens")["window"] is None


def test_a_header_with_digit_like_characters_is_skipped_never_a_crash():
    headers = {"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "\u00b2", "x-ratelimit-limit-requests": "\u0661\u0660", "x-ratelimit-remaining-requests": "5"}
    assert up.provider_rows("groq", {"headers": headers, "at": 1.0}, now=1.0) == []
