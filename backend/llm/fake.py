"""A fake answer model for tests: scripted answers and errors, and a record of every call. It cannot test answer quality."""
from .base import Completion, check_cap, check_messages


class FakeLLM:
    def __init__(self, script=None, model: str = "fake-llm"):
        self.model = model
        self.script = list(script) if script is not None else []     # items: a Completion, a string, or an exception to raise; the last one repeats
        self.calls: list[dict] = []                                  # [{"messages": ..., "max_output_tokens": ...}]

    def complete(self, messages: list[dict], *, max_output_tokens: int) -> Completion:
        check_messages(messages)
        check_cap(max_output_tokens)
        self.calls.append({"messages": [dict(m) for m in messages], "max_output_tokens": max_output_tokens})
        item = (self.script[min(len(self.calls), len(self.script)) - 1] if self.script else "fake answer")
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return Completion(text=item, finish_reason="stop", model=self.model, prompt_tokens=100, completion_tokens=20)
        return item
