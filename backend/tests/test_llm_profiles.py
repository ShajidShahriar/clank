"""Task: answer-model profiles. A profile is everything that differs between models: where it lives, its name, which environment variable holds its key, how much
code to send (its context budget) and how long an answer may be. The pipeline never knows which profile is active.

- Groq's free tier is the first profile (`openai/gpt-oss-120b`); its budget follows the plan's limits (8,000 tokens a minute): about 2,500 tokens of code;
- the key is read from the environment when the client is made, never stored in the profile; a missing, empty or malformed key is "not configured", and the
  message names the VARIABLE, never the value;
- a profile for a remote address must be https when it uses a key; whether a profile is local decides whether sending code needs the user's consent.
"""
import dataclasses

import pytest

from llm import LLMNotConfigured, OpenAICompatibleClient
from llm.profiles import GROQ_GPT_OSS_120B, PROFILES, Profile, llm_from_env, make_llm, profile_from_env

KEY = "gsk_" + "k3y" * 12


def profile(**kw):
    base = dict(name="p", base_url="https://api.example.com/v1", model="m", api_key_env="EXAMPLE_KEY", context_tokens=2500, max_output_tokens=800)
    return Profile(**{**base, **kw})


# ---- the Groq profile

def test_the_groq_profile_matches_the_free_plan():
    p = GROQ_GPT_OSS_120B
    assert (p.name, p.model, p.base_url, p.api_key_env) == ("groq", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", "GROQ_API_KEY")
    assert p.output_limit_param == "max_completion_tokens" and p.extra_body == {"reasoning_effort": "low"}
    assert p.context_tokens == 2500 and p.max_output_tokens == 1200
    # 2,500 estimated tokens of code are about 1,900 real ones; with the prompt and the answer cap one question stays well under half of 8,000 tokens a minute
    assert p.context_tokens * 3 // 4 + 300 + p.max_output_tokens < 8000 // 2
    assert p.is_local is False and PROFILES["groq"] is p


# ---- is it local?

@pytest.mark.parametrize("url, local", [
    ("http://localhost:11434/v1", True), ("http://127.0.0.1:1234/v1", True), ("http://127.5.5.5/v1", True), ("http://[::1]:8080/v1", True),
    ("https://api.groq.com/openai/v1", False), ("http://localhost.evil.com/v1", False), ("http://127.0.0.1.evil.com/v1", False), ("http://192.168.0.5:11434/v1", False),
    ("http://0.0.0.0:1234/v1", False), ("https://openrouter.ai/api/v1", False),
])
def test_a_profile_is_local_only_when_its_address_is_this_computer(url, local):
    assert profile(base_url=url, api_key_env=None).is_local is local


# ---- validation

def test_a_profile_cannot_be_changed_after_it_is_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile().model = "other"


@pytest.mark.parametrize("kw", [
    {"name": ""}, {"model": ""}, {"base_url": "ftp://x/v1"}, {"base_url": ""}, {"base_url": "http://api.example.com/v1"},          # a key over http to a remote address
    {"api_key_env": "lower_case"}, {"api_key_env": "HAS SPACE"}, {"api_key_env": ""}, {"api_key_env": "1STARTS_WITH_DIGIT"},
    {"context_tokens": 499}, {"context_tokens": 16001}, {"context_tokens": 1.5}, {"context_tokens": True},
    {"max_output_tokens": 0}, {"max_output_tokens": -1}, {"max_output_tokens": 20001}, {"max_output_tokens": True},
    {"output_limit_param": "stream"}, {"timeout": 0}, {"extra_body": "x"},
])
def test_a_bad_profile_is_refused_when_it_is_made(kw):
    with pytest.raises(ValueError):
        profile(**kw)


def test_a_profile_without_a_key_may_use_plain_http_to_this_computer_only():
    assert profile(base_url="http://localhost:11434/v1", api_key_env=None, model="llama").is_local
    profile(base_url="http://api.example.com/v1", api_key_env=None)          # no key, nothing secret to protect: the client allows it


# ---- the key

def test_the_client_is_made_from_the_profile_and_the_key_in_the_environment():
    client = make_llm(GROQ_GPT_OSS_120B, {"GROQ_API_KEY": KEY})
    assert isinstance(client, OpenAICompatibleClient)
    assert (client.model, client.base_url, client.output_limit_param, client.extra_body) == (
        "openai/gpt-oss-120b", "https://api.groq.com/openai/v1", "max_completion_tokens", {"reasoning_effort": "low"})
    assert client._api_key == KEY and KEY not in repr(client)


def test_the_key_is_trimmed():
    assert make_llm(GROQ_GPT_OSS_120B, {"GROQ_API_KEY": f"  {KEY}\n"})._api_key == KEY


@pytest.mark.parametrize("environ", [{}, {"GROQ_API_KEY": ""}, {"GROQ_API_KEY": "   "}, {"OTHER": "x"}])
def test_no_key_is_not_configured_and_the_message_names_the_variable(environ):
    with pytest.raises(LLMNotConfigured, match="GROQ_API_KEY") as caught:
        make_llm(GROQ_GPT_OSS_120B, environ)
    assert caught.value.env_var == "GROQ_API_KEY"


@pytest.mark.parametrize("bad", ["key with space", "line1\nline2", "tab\tkey", "kéy", "key\x00x", "a" * 5000])
def test_a_malformed_key_is_refused_without_repeating_it(bad):
    """A newline in a header value would let a key break out of its header; the standard library's own error would print the value."""
    with pytest.raises(LLMNotConfigured) as caught:
        make_llm(GROQ_GPT_OSS_120B, {"GROQ_API_KEY": bad})
    assert bad.strip() not in str(caught.value) and "GROQ_API_KEY" in str(caught.value)


def test_a_profile_with_no_key_variable_needs_no_key():
    client = make_llm(profile(base_url="http://localhost:11434/v1", api_key_env=None, model="llama3"), {})
    assert client._api_key is None


# ---- choosing the profile

def test_groq_is_the_default_profile_and_the_environment_can_name_another_known_one():
    assert profile_from_env({}) is GROQ_GPT_OSS_120B
    assert profile_from_env({"CLANK_LLM_PROFILE": "groq"}) is GROQ_GPT_OSS_120B
    assert profile_from_env({"CLANK_LLM_PROFILE": "  groq "}) is GROQ_GPT_OSS_120B


@pytest.mark.parametrize("name", ["nope", "GROQ", "groq;rm", "../x"])
def test_an_unknown_profile_name_is_not_configured_and_lists_the_known_ones(name):
    with pytest.raises(LLMNotConfigured, match="groq"):
        profile_from_env({"CLANK_LLM_PROFILE": name})


def test_llm_from_env_returns_the_profile_and_its_client():
    chosen, client = llm_from_env({"GROQ_API_KEY": KEY})
    assert chosen is GROQ_GPT_OSS_120B and client.model == "openai/gpt-oss-120b"
    with pytest.raises(LLMNotConfigured):
        llm_from_env({})
