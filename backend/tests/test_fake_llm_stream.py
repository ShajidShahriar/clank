"""Task: streaming answers, part 4: `FakeLLM.stream()`, the stand-in the route tests use. It must behave like the real stream where the route can tell:
pieces one at a time (optionally slowly), thinking pieces, a final `StreamDone`, a failure midway, a service that goes silent, a refusal when the stream is OPENED,
and a `close()` that ends a wait at once and is remembered. It records every call (with the messages), like `complete()`."""
import threading
import time

import pytest

from llm import Completion, FakeLLM, LLMRateLimited, LLMTimeout, StreamDone, TextPiece, ThinkingPiece
from llm.fake import FakeStream

MESSAGES = [{"role": "user", "content": "hi"}]


def read(stream):
    return list(stream)


def test_a_string_becomes_word_pieces_then_done():
    events = read(FakeLLM(["one two three"]).stream(MESSAGES, max_output_tokens=50))
    assert [e.text for e in events if isinstance(e, TextPiece)] == ["one", " two", " three"]
    assert events[-1] == StreamDone(finish_reason="stop", model="fake-llm", prompt_tokens=100, completion_tokens=20, reasoning_tokens=None)


def test_the_pieces_put_together_are_the_string():
    text = "Line one.\n\n  indented  code\nlast"
    assert "".join(e.text for e in read(FakeLLM([text]).stream(MESSAGES, max_output_tokens=50)) if isinstance(e, TextPiece)) == text
    assert "".join(e.text for e in read(FakeLLM(["  lead and trail  "]).stream(MESSAGES, max_output_tokens=50)) if isinstance(e, TextPiece)) == "  lead and trail  "


def test_thinking_pieces_come_first_and_are_counted_in_the_done():
    events = read(FakeLLM(["a b"], thinking=3).stream(MESSAGES, max_output_tokens=50))
    assert [type(e).__name__ for e in events] == ["ThinkingPiece"] * 3 + ["TextPiece", "TextPiece", "StreamDone"]
    assert events[-1].reasoning_tokens == 3


def test_an_empty_answer_is_just_done():
    events = read(FakeLLM([""]).stream(MESSAGES, max_output_tokens=50))
    assert [type(e) for e in events] == [StreamDone]


def test_a_completion_is_streamed_with_its_numbers():
    completion = Completion(text="x y", finish_reason="length", model="m", prompt_tokens=7, completion_tokens=2)
    events = read(FakeLLM([completion]).stream(MESSAGES, max_output_tokens=50))
    assert events[-1] == StreamDone(finish_reason="length", model="m", prompt_tokens=7, completion_tokens=2, reasoning_tokens=None)


def test_a_list_of_events_is_played_as_it_is():
    script = [ThinkingPiece(), TextPiece("hi"), StreamDone(finish_reason="stop", model="m")]
    assert read(FakeLLM([script]).stream(MESSAGES, max_output_tokens=50)) == script


def test_an_error_in_the_script_is_raised_when_the_stream_is_opened():
    llm = FakeLLM([LLMRateLimited(retry_after=9)])
    with pytest.raises(LLMRateLimited):
        llm.stream(MESSAGES, max_output_tokens=50)
    assert len(llm.calls) == 1


def test_a_stream_that_fails_midway_gives_its_pieces_then_raises():
    stream = FakeStream([TextPiece("half")], fail_with=LLMTimeout("slow"))
    got = []
    with pytest.raises(LLMTimeout):
        for event in stream:
            got.append(event)
    assert got == [TextPiece("half")]


def test_the_delay_between_pieces_is_real():
    stream = FakeLLM(["a b c"], delay=0.1).stream(MESSAGES, max_output_tokens=50)
    started = time.monotonic()
    read(stream)
    assert time.monotonic() - started >= 0.25


def test_close_ends_a_delay_at_once_and_is_remembered():
    stream = FakeLLM(["a b c d e f g"], delay=1.0).stream(MESSAGES, max_output_tokens=50)
    got = []
    reader = threading.Thread(target=lambda: got.extend(stream))
    reader.start()
    time.sleep(0.2)
    started = time.monotonic()
    stream.close()
    reader.join(2)
    assert not reader.is_alive() and time.monotonic() - started < 0.5
    assert stream.closed is True and not any(isinstance(e, StreamDone) for e in got)


def test_a_silent_service_waits_until_it_is_closed():
    stream = FakeStream([TextPiece("x")], hang_after=1)
    got = []
    reader = threading.Thread(target=lambda: got.extend(stream))
    reader.start()
    time.sleep(0.2)
    assert got == [TextPiece("x")] and reader.is_alive()
    stream.close()
    reader.join(2)
    assert not reader.is_alive()


def test_a_service_that_goes_silent_after_its_last_event_waits_too():
    stream = FakeStream([TextPiece("x")], hang_after=5)
    got = []
    reader = threading.Thread(target=lambda: got.extend(stream))
    reader.start()
    time.sleep(0.2)
    assert reader.is_alive()
    stream.close()
    reader.join(2)
    assert not reader.is_alive() and got == [TextPiece("x")]


def test_close_before_reading_gives_nothing():
    stream = FakeStream([TextPiece("x"), StreamDone(finish_reason="stop", model="m")])
    stream.close()
    assert read(stream) == []


def test_close_can_be_called_twice_and_after_the_end():
    stream = FakeLLM(["x"]).stream(MESSAGES, max_output_tokens=50)
    read(stream)
    stream.close()
    stream.close()
    assert stream.closed


def test_the_headers_are_exposed():
    stream = FakeLLM([FakeStream([], rate_limit_headers={"x-ratelimit-remaining-tokens": "100"})]).stream(MESSAGES, max_output_tokens=50)
    assert stream.rate_limit_headers == {"x-ratelimit-remaining-tokens": "100"}
    assert FakeLLM(["x"]).stream(MESSAGES, max_output_tokens=50).rate_limit_headers == {}


def test_every_call_is_recorded_with_its_messages_and_cap():
    llm = FakeLLM(["a", "b"])
    llm.stream(MESSAGES, max_output_tokens=50)
    llm.complete(MESSAGES, max_output_tokens=60)
    assert [c["max_output_tokens"] for c in llm.calls] == [50, 60] and llm.calls[0]["messages"] == MESSAGES and llm.calls[0].get("stream") is True
    assert "stream" not in llm.calls[1]


def test_the_script_advances_per_call_and_the_last_item_repeats():
    llm = FakeLLM(["first", "second"])
    texts = ["".join(e.text for e in llm.stream(MESSAGES, max_output_tokens=5) if isinstance(e, TextPiece)) for _ in range(3)]
    assert texts == ["first", "second", "second"]


def test_bad_arguments_are_refused_like_the_real_client():
    for bad in ({"messages": [], "max_output_tokens": 5}, {"messages": MESSAGES, "max_output_tokens": 0}):
        with pytest.raises(ValueError):
            FakeLLM().stream(**bad)


def test_the_streams_made_are_kept_so_a_test_can_ask_whether_they_were_closed():
    llm = FakeLLM(["a b"])
    first = llm.stream(MESSAGES, max_output_tokens=5)
    first.close()
    assert llm.streams == [first] and llm.streams[0].closed


def test_a_service_that_goes_silent_before_its_last_event_never_sends_the_rest():
    stream = FakeStream([TextPiece("a"), TextPiece("b"), TextPiece("c")], hang_after=1)
    got = []
    reader = threading.Thread(target=lambda: got.extend(stream))
    reader.start()
    time.sleep(0.2)
    assert got == [TextPiece("a")] and reader.is_alive()
    stream.close()
    reader.join(2)
    assert got == [TextPiece("a")] and not reader.is_alive()
