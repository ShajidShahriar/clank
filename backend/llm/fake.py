"""A fake answer model for tests: scripted answers and errors, and a record of every call. It cannot test answer quality.

`complete()` returns a whole answer; `stream()` returns the same answer piece by piece (`FakeStream`): optionally slowly, with thinking pieces first, failing
midway, or going silent, and with a `close()` that ends a wait at once (so a test can check that Stop really reaches the model)."""
import re
import threading

from .base import Completion, StreamDone, TextPiece, ThinkingPiece, check_cap, check_messages


class FakeLLM:
    def __init__(self, script=None, model: str = "fake-llm", *, thinking: int = 0, delay: float = 0.0):
        self.model = model
        self.script = list(script) if script is not None else []     # items: a Completion, a string, or an exception to raise; the last one repeats
        self.calls: list[dict] = []                                  # [{"messages": ..., "max_output_tokens": ...}] (a stream call also has "stream": True)
        self.streams: list["FakeStream"] = []                        # every stream handed out, to ask whether it was closed
        self.thinking, self.delay = thinking, delay                  # for a string or Completion streamed: thinking pieces first; seconds between pieces

    def _next(self):
        return self.script[min(len(self.calls), len(self.script)) - 1] if self.script else "fake answer"

    def complete(self, messages: list[dict], *, max_output_tokens: int) -> Completion:
        check_messages(messages)
        check_cap(max_output_tokens)
        self.calls.append({"messages": [dict(m) for m in messages], "max_output_tokens": max_output_tokens})
        item = self._next()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return Completion(text=item, finish_reason="stop", model=self.model, prompt_tokens=100, completion_tokens=20)
        return item

    def stream(self, messages: list[dict], *, max_output_tokens: int) -> "FakeStream":
        check_messages(messages)
        check_cap(max_output_tokens)
        self.calls.append({"messages": [dict(m) for m in messages], "max_output_tokens": max_output_tokens, "stream": True})
        item = self._next()
        if isinstance(item, BaseException):
            raise item                                               # a refusal comes when the stream is OPENED, like the real client's
        if isinstance(item, FakeStream):
            stream = item
        elif isinstance(item, list):
            stream = FakeStream(item, delay=self.delay)
        else:
            completion = item if isinstance(item, Completion) else Completion(text=item, finish_reason="stop", model=self.model, prompt_tokens=100, completion_tokens=20)
            events = [ThinkingPiece() for _ in range(self.thinking)] + [TextPiece(piece) for piece in _pieces(completion.text)]
            events.append(StreamDone(finish_reason=completion.finish_reason, model=completion.model, prompt_tokens=completion.prompt_tokens,
                                     completion_tokens=completion.completion_tokens, reasoning_tokens=self.thinking or None))
            stream = FakeStream(events, delay=self.delay)
        self.streams.append(stream)
        return stream


def _pieces(text: str) -> list[str]:
    """The text cut into word-sized pieces that put together are exactly the text (the space goes with the word it comes before)."""
    return re.findall(r"\s*\S+|\s+", text)


class FakeStream:
    """The events of an answer, played one at a time. `delay`: seconds before each event; `fail_with`: raised after the last event; `hang_after`: after that many events
    say nothing until closed (a silent service). `close()` ends any wait at once; nothing more is played after it."""

    def __init__(self, events, *, delay: float = 0.0, fail_with: BaseException | None = None, hang_after: int | None = None, rate_limit_headers: dict | None = None):
        self._events, self._delay, self._fail_with, self._hang_after = list(events), delay, fail_with, hang_after
        self.rate_limit_headers = dict(rate_limit_headers or {})
        self._closed = threading.Event()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        self._closed.set()

    def __iter__(self):
        for count, event in enumerate(self._events):
            if self._hang_after is not None and count >= self._hang_after:
                self._closed.wait()
                return
            if self._delay and self._closed.wait(self._delay):
                return
            if self.closed:
                return
            yield event
        if self._hang_after is not None and self._hang_after >= len(self._events):
            self._closed.wait()
            return
        if self._fail_with is not None and not self.closed:
            raise self._fail_with
