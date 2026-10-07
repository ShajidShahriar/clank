"""The answer-model interface. Everything else in Clank talks to this, never to a specific provider (like `Embedder` for embeddings).

A new provider is one new class with `complete()`. Messages are the usual chat list: `{"role": "system" | "user" | "assistant", "content": "..."}`.
"""
import ipaddress
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

ROLES = ("system", "user", "assistant")


@dataclass(frozen=True)
class Completion:
    text: str                           # may be empty (a reasoning model can spend the whole cap thinking); check finish_reason
    finish_reason: str | None           # "stop" = finished by itself, "length" = cut by the length cap
    model: str                          # the model that answered
    prompt_tokens: int | None = None    # what the service counted, None when it did not say
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ThinkingPiece:
    """The model produced a piece of thinking. The thinking TEXT is deliberately not carried: it is never shown, stored or sent on, only counted."""


@dataclass(frozen=True)
class TextPiece:
    text: str                           # the next piece of the answer (never empty, never thinking, never a tag)


@dataclass(frozen=True)
class StreamDone:
    """The end of a streamed answer: why it stopped, which model answered, and what the service counted (None for what it did not say)."""
    finish_reason: str | None
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None    # ALL the output tokens, the thinking ones included
    reasoning_tokens: int | None = None     # the thinking part of them


StreamEvent = ThinkingPiece | TextPiece | StreamDone


@runtime_checkable
class LLMStream(Protocol):
    """An answer that is still being written. Iterate it for events; the last one is a `StreamDone`. Every failure raises an `LLMError` subclass."""
    rate_limit_headers: dict[str, str]  # the service's `x-ratelimit-*` and `retry-after` headers (lower case names), known BEFORE the first piece

    def __iter__(self) -> Iterator[StreamEvent]: ...

    def close(self) -> None:
        """Stop the answer NOW. Safe to call from another thread, even while a read is waiting for the service: the wait ends at once and the service is told to stop
        (the connection is closed), so it stops spending tokens. Iteration then ends quietly. Safe to call more than once."""
        ...


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, messages: list[dict], *, max_output_tokens: int) -> Completion:
        """One answer to a chat. Raises an `LLMError` subclass for every failure."""
        ...

    def stream(self, messages: list[dict], *, max_output_tokens: int) -> LLMStream:
        """The same question, answered piece by piece. The connection is OPENED here: a refusal (a bad key, a rate limit, a too long request) raises now, before
        any piece, exactly as `complete` would."""
        ...


def check_messages(messages) -> None:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ROLES or not isinstance(message.get("content"), str):
            raise ValueError("every message needs a role (system, user or assistant) and a text content")


def check_cap(max_output_tokens) -> None:
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
        raise ValueError(f"max_output_tokens must be a positive integer, got {max_output_tokens!r}")


def host_is_local(base_url: str) -> bool:
    """Is this address on this computer? (localhost, 127.x.x.x, ::1). Anything else is a remote service: code sent there leaves the machine."""
    host = (urlsplit(base_url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
