"""One real call to Groq, for when a key is available: `GROQ_API_KEY=... uv run pytest -q -m live tests/test_groq_live.py`.

It skips itself without the key. It sends a tiny request (about 100 tokens in, at most 300 out) so it barely touches the free plan's 8,000 tokens a minute.
"""
import os

import pytest

from llm import Completion
from llm.profiles import GROQ_GPT_OSS_120B, make_llm

pytestmark = pytest.mark.live
KEY = os.environ.get("GROQ_API_KEY", "").strip()


@pytest.mark.skipif(not KEY, reason="GROQ_API_KEY is not set")
def test_the_real_service_answers_a_tiny_question_through_the_real_client():
    client = make_llm(GROQ_GPT_OSS_120B, {"GROQ_API_KEY": KEY})
    out = client.complete([{"role": "system", "content": "Answer in one short sentence."},
                           {"role": "user", "content": "In Python, what keyword defines a function?"}], max_output_tokens=300)
    assert isinstance(out, Completion) and out.finish_reason in ("stop", "length")
    assert "gpt-oss" in out.model
    assert isinstance(out.prompt_tokens, int) and out.prompt_tokens > 0 and isinstance(out.completion_tokens, int)
    if out.finish_reason == "stop":
        assert "def" in out.text.lower()
    assert KEY not in out.text
