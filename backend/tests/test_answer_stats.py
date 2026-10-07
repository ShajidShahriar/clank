"""Task: streaming answers, part 5: the numbers behind the summary line "Searched 0.4 s · Thought 3.2 s, 412 tokens · Answer 268 tokens · 5.1 s total"
(`answer_stats.py`, pure: it only does arithmetic on moments of time and counts).

What is promised:
- the moments are monotonic clock readings in SECONDS; the results are whole MILLISECONDS, never negative, and None for a stage that did not happen
  (a model that does not think has no thinking time: it is None, never 0 and never invented);
- waiting = from sending the request to the first piece of any kind; thinking = from the first thinking piece to the first answer piece (or to the end when no answer
  came); writing = from the first answer piece to the end; search and total are what they say; when the model was not called only search and total exist;
- tokens: when the service reports its thinking tokens and its output tokens, the answer's tokens are the difference and nothing is marked as an estimate; when it
  does not report the thinking tokens, the thinking pieces counted stand in for them (marked estimated); when it reports no output tokens, the answer's tokens are
  estimated from its text (len // 3, like everything else here); numbers that cannot be right (thinking above the total) never give a negative answer.
"""
import pytest

from answer_stats import build_timings, token_summary


def timings(**kw):
    base = dict(begin=0.0, searched=0.4, requested=0.4, first_piece=0.9, first_thinking=0.9, first_text=2.15, finished=2.5)
    return build_timings(**{**base, **kw})


# ---- timings

def test_a_model_that_thinks_has_all_the_stages():
    assert timings() == {"search_ms": 400, "wait_ms": 500, "thinking_ms": 1250, "writing_ms": 350, "total_ms": 2500}


def test_a_model_that_does_not_think_has_no_thinking_time():
    t = timings(first_thinking=None, first_piece=0.9, first_text=0.9)
    assert t["thinking_ms"] is None and t["wait_ms"] == 500 and t["writing_ms"] == 1600


def test_a_stream_that_never_wrote_has_no_writing_time_and_thinking_runs_to_the_end():
    t = timings(first_text=None)
    assert t["writing_ms"] is None and t["thinking_ms"] == 1600


def test_a_stream_with_no_piece_at_all_waited_until_the_end():
    t = timings(first_piece=None, first_thinking=None, first_text=None)
    assert t["wait_ms"] == 2100 and t["thinking_ms"] is None and t["writing_ms"] is None and t["total_ms"] == 2500


def test_when_the_model_was_not_called_only_search_and_total_exist():
    t = build_timings(begin=0.0, searched=0.3, requested=None, first_piece=None, first_thinking=None, first_text=None, finished=0.31)
    assert t == {"search_ms": 300, "wait_ms": None, "thinking_ms": None, "writing_ms": None, "total_ms": 310}


def test_milliseconds_are_rounded_not_cut():
    assert timings(searched=0.0004, requested=0.0004, begin=0.0)["search_ms"] == 0
    assert build_timings(begin=0.0, searched=0.0006, requested=None, first_piece=None, first_thinking=None, first_text=None, finished=1.0)["search_ms"] == 1


def test_a_clock_that_seems_to_run_backwards_never_gives_a_negative_number():
    t = timings(searched=-1.0, first_piece=0.1, first_thinking=0.05, first_text=0.0, finished=-5.0)
    assert all(v is None or v >= 0 for v in t.values())


def test_every_value_is_a_whole_number_or_none():
    for value in timings(begin=0.00012, finished=2.50049).values():
        assert value is None or (isinstance(value, int) and not isinstance(value, bool))


# ---- tokens

def summary(**kw):
    base = dict(reported_thinking=None, reported_completion=None, thinking_pieces=0, text="")
    return token_summary(**{**base, **kw})


def test_everything_reported_gives_exact_numbers():
    assert summary(reported_thinking=54, reported_completion=84, thinking_pieces=53, text="x" * 99) == {"thinking": 54, "answer": 30, "estimated": False}


def test_thinking_reported_as_zero_is_exact_zero():
    assert summary(reported_thinking=0, reported_completion=40) == {"thinking": 0, "answer": 40, "estimated": False}


def test_thinking_not_reported_but_pieces_counted_is_an_estimate():
    assert summary(reported_thinking=None, reported_completion=84, thinking_pieces=53, text="hello") == {"thinking": 53, "answer": 31, "estimated": True}


def test_no_thinking_seen_and_none_reported_leaves_thinking_unknown_and_the_rest_exact():
    assert summary(reported_completion=38, text="hello") == {"thinking": None, "answer": 38, "estimated": False}


def test_no_output_count_reported_estimates_the_answer_from_its_text():
    assert summary(text="x" * 90) == {"thinking": None, "answer": 30, "estimated": True}
    assert summary(thinking_pieces=7, text="x" * 90) == {"thinking": 7, "answer": 30, "estimated": True}
    assert summary(reported_thinking=7, text="x" * 90) == {"thinking": 7, "answer": 30, "estimated": True}


def test_an_empty_answer_with_nothing_reported_is_zero_tokens():
    assert summary(text="") == {"thinking": None, "answer": 0, "estimated": True}


def test_thinking_above_the_total_never_gives_a_negative_answer():
    assert summary(reported_thinking=90, reported_completion=40)["answer"] == 0
    assert summary(thinking_pieces=90, reported_completion=40)["answer"] == 0


def test_reported_thinking_wins_over_counted_pieces():
    assert summary(reported_thinking=10, reported_completion=30, thinking_pieces=99)["thinking"] == 10


@pytest.mark.parametrize("bad", [-1, True, 1.5, "7"])
def test_a_reported_number_that_is_not_a_count_is_ignored(bad):
    assert summary(reported_thinking=bad, reported_completion=bad, text="x" * 30, thinking_pieces=4) == {"thinking": 4, "answer": 10, "estimated": True}
