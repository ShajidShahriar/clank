"""The answer-model interface. Everything else in Clank talks to this, never to a specific provider (like `Embedder` for embeddings).

A new provider is one new class with `complete()`. Messages are the usual chat list: `{"role": "system" | "user" | "assistant", "content": "..."}`.
"""
import ipaddress
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


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, messages: list[dict], *, max_output_tokens: int) -> Completion:
        """One answer to a chat. Raises an `LLMError` subclass for every failure."""
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
