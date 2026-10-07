"""Read ONE chunk of a streamed chat reply (the JSON after `data:`), pure, no I/O.

Shapes seen in the wild (Groq's gpt-oss, captured 2026-10-07): the first chunk is `delta {role, content: ""}` (an empty piece says nothing); thinking pieces are
`delta.reasoning` (Groq, Ollama, OpenRouter) or `delta.reasoning_content` (DeepSeek, LM Studio); answer pieces are `delta.content`; the finish reason comes in its own
chunk; the usage comes LATER in a chunk with `choices: []` (some servers put it in the finish chunk); `completion_tokens` INCLUDES the thinking tokens, which are
`completion_tokens_details.reasoning_tokens`. Only what the app needs is kept: request ids, seeds, the service tier and the system fingerprint are dropped here and never
passed on. A chunk with the wrong shape is an `LLMBadResponse`.
"""
from dataclasses import dataclass

from .errors import LLMBadResponse

BAD = "the answer service sent something that is not a chat answer"


@dataclass(frozen=True)
class ChunkUsage:
    prompt_tokens: int | None
    completion_tokens: int | None          # ALL the output tokens, thinking included
    reasoning_tokens: int | None           # the thinking part of them, None when the service does not say


@dataclass(frozen=True)
class Chunk:
    thinking: str = ""
    text: str = ""
    finish_reason: str | None = None
    usage: ChunkUsage | None = None
    model: str | None = None
    error: bool = False


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _piece(delta: dict, name: str) -> str:
    value = delta.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LLMBadResponse(BAD)
    return value


def read_chunk(data) -> Chunk:
    if not isinstance(data, dict):
        raise LLMBadResponse(BAD)
    if "error" in data:
        return Chunk(error=True)
    model = data.get("model") if isinstance(data.get("model"), str) else None
    choices = data.get("choices", [])
    if not isinstance(choices, list):
        raise LLMBadResponse(BAD)
    thinking = text = ""
    finish = None
    if choices:
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMBadResponse(BAD)
        delta = choice.get("delta")
        if delta is None:
            delta = {}
        if not isinstance(delta, dict):
            raise LLMBadResponse(BAD)
        thinking = _piece(delta, "reasoning") + _piece(delta, "reasoning_content")
        text = _piece(delta, "content")
        finish = choice.get("finish_reason")
        if finish is not None and not isinstance(finish, str):
            raise LLMBadResponse(BAD)
    usage = None
    raw = data.get("usage")
    if raw is not None:
        if not isinstance(raw, dict):
            raise LLMBadResponse(BAD)
        details = raw.get("completion_tokens_details")
        usage = ChunkUsage(_count(raw.get("prompt_tokens")), _count(raw.get("completion_tokens")),
                           _count(details.get("reasoning_tokens")) if isinstance(details, dict) else None)
    return Chunk(thinking=thinking, text=text, finish_reason=finish, usage=usage, model=model)
