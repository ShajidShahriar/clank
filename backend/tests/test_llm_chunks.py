"""Task: streaming answers, part 2: reading ONE chunk of a streamed chat reply (`llm/chunks.py`, pure).

The fixtures below are REAL chunks from Groq's `openai/gpt-oss-120b` (captured 2026-10-07 with scripts/capture_llm_stream.py, request ids removed). What is promised:
- thinking text comes from `delta.reasoning` (Groq, Ollama, OpenRouter) or `delta.reasoning_content` (DeepSeek, LM Studio); answer text from `delta.content`;
- the FIRST chunk has `content: ""`: an empty piece says nothing (it must never look like "the model started writing");
- the finish reason is read from the choice that has one; the usage arrives in a LATER chunk with `choices: []` (or in the finish chunk itself, as some servers do);
- the usage gives the prompt tokens, ALL the output tokens, and the thinking part of them (`completion_tokens_details.reasoning_tokens`) when the service says so;
- an `error` chunk is flagged; fields that must never be passed on (`x_groq`, `service_tier`, `system_fingerprint`) simply do not appear in the result;
- a chunk that is not an object, or whose fields have the wrong type, is an `LLMBadResponse`: never half-used.
"""
import pytest

from llm.chunks import read_chunk
from llm.errors import LLMBadResponse

FIRST = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1791388922, "model": "openai/gpt-oss-120b", "system_fingerprint": "fp_dee443f41b",
         "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "logprobs": None, "finish_reason": None}], "x_groq": {"id": "req_x", "seed": 1414499071}}
THINKING = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1791388922, "model": "openai/gpt-oss-120b", "system_fingerprint": "fp_dee443f41b",
            "choices": [{"index": 0, "delta": {"reasoning": " brief", "channel": "analysis"}, "logprobs": None, "finish_reason": None}]}
ANSWER = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "model": "openai/gpt-oss-120b", "choices": [{"index": 0, "delta": {"content": "Yes, 17 is prime."}, "finish_reason": None}]}
FINISH = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "model": "openai/gpt-oss-120b", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
USAGE = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1791388922, "model": "openai/gpt-oss-120b", "system_fingerprint": "fp_dee443f41b", "choices": [],
         "usage": {"queue_time": 0.36142086, "prompt_tokens": 90, "prompt_time": 0.028013544, "completion_tokens": 38, "completion_time": 0.079409871, "total_tokens": 128,
                   "total_time": 0.107423415, "completion_tokens_details": {"reasoning_tokens": 7}}, "service_tier": "on_demand"}


def test_the_first_chunk_with_an_empty_content_says_nothing():
    chunk = read_chunk(FIRST)
    assert (chunk.thinking, chunk.text, chunk.finish_reason, chunk.usage, chunk.error) == ("", "", None, None, False)
    assert chunk.model == "openai/gpt-oss-120b"


def test_a_thinking_piece_is_read_from_reasoning():
    chunk = read_chunk(THINKING)
    assert (chunk.thinking, chunk.text) == (" brief", "")


def test_a_thinking_piece_is_read_from_reasoning_content():
    chunk = read_chunk({"choices": [{"delta": {"reasoning_content": "hmm"}}]})
    assert (chunk.thinking, chunk.text) == ("hmm", "")


def test_an_answer_piece_is_read_from_content():
    chunk = read_chunk(ANSWER)
    assert (chunk.thinking, chunk.text, chunk.finish_reason) == ("", "Yes, 17 is prime.", None)


def test_the_finish_reason_is_read_from_the_finishing_chunk():
    assert read_chunk(FINISH).finish_reason == "stop"
    assert read_chunk({"choices": [{"delta": {}, "finish_reason": "length"}]}).finish_reason == "length"


def test_the_usage_chunk_has_no_choices_and_gives_the_numbers():
    chunk = read_chunk(USAGE)
    assert (chunk.thinking, chunk.text, chunk.finish_reason) == ("", "", None)
    assert chunk.usage.prompt_tokens == 90 and chunk.usage.completion_tokens == 38 and chunk.usage.reasoning_tokens == 7


def test_usage_in_the_finishing_chunk_is_read_too():
    chunk = read_chunk({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}})
    assert (chunk.text, chunk.finish_reason, chunk.usage.prompt_tokens, chunk.usage.completion_tokens, chunk.usage.reasoning_tokens) == ("x", "stop", 5, 3, None)


def test_usage_without_a_thinking_count_has_none():
    assert read_chunk({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}).usage.reasoning_tokens is None
    assert read_chunk({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "completion_tokens_details": {}}}).usage.reasoning_tokens is None
    assert read_chunk({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "completion_tokens_details": None}}).usage.reasoning_tokens is None


def test_a_null_usage_is_no_usage():
    assert read_chunk({"choices": [{"delta": {"content": "x"}}], "usage": None}).usage is None


def test_numbers_that_are_not_counts_are_not_counts():
    chunk = read_chunk({"choices": [], "usage": {"prompt_tokens": True, "completion_tokens": -4, "completion_tokens_details": {"reasoning_tokens": "7"}}})
    assert (chunk.usage.prompt_tokens, chunk.usage.completion_tokens, chunk.usage.reasoning_tokens) == (None, None, None)


def test_a_null_content_or_delta_is_empty():
    assert read_chunk({"choices": [{"delta": {"content": None}}]}).text == ""
    assert read_chunk({"choices": [{"delta": None}]}).text == ""
    assert read_chunk({"choices": [{}]}).text == ""


def test_only_the_first_choice_is_read():
    chunk = read_chunk({"choices": [{"index": 0, "delta": {"content": "a"}}, {"index": 1, "delta": {"content": "b"}}]})
    assert chunk.text == "a"


def test_both_kinds_of_thinking_field_are_joined():
    assert read_chunk({"choices": [{"delta": {"reasoning": "a", "reasoning_content": "b"}}]}).thinking == "ab"


def test_an_error_chunk_is_flagged():
    chunk = read_chunk({"error": {"message": "the provider's own words", "type": "server_error"}})
    assert chunk.error is True and chunk.text == "" and chunk.thinking == ""


def test_what_must_never_be_passed_on_is_not_in_the_result():
    fields = set(vars(read_chunk(FIRST))) | set(vars(read_chunk(USAGE).usage))
    assert fields.isdisjoint({"x_groq", "service_tier", "system_fingerprint", "id", "queue_time", "created"})


def test_the_model_name_is_read_when_it_is_text():
    assert read_chunk({"model": "m", "choices": []}).model == "m"
    assert read_chunk({"model": 5, "choices": []}).model is None
    assert read_chunk({"choices": []}).model is None


@pytest.mark.parametrize("bad", [None, [], "text", 5, [{"choices": []}]])
def test_a_chunk_that_is_not_an_object_is_refused(bad):
    with pytest.raises(LLMBadResponse):
        read_chunk(bad)


@pytest.mark.parametrize("bad", [
    {"choices": "x"}, {"choices": [5]}, {"choices": [{"delta": "x"}]}, {"choices": [{"delta": {"content": 5}}]}, {"choices": [{"delta": {"content": ["a"]}}]},
    {"choices": [{"delta": {"reasoning": 5}}]}, {"choices": [{"delta": {"reasoning_content": {}}}]}, {"choices": [{"delta": {}, "finish_reason": 5}]},
    {"choices": [], "usage": "x"}, {"choices": [], "usage": []},
])
def test_a_chunk_with_fields_of_the_wrong_type_is_refused(bad):
    with pytest.raises(LLMBadResponse):
        read_chunk(bad)
