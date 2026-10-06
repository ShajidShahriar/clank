"""Answer-model profiles: everything that differs between models, in one place. The pipeline never knows which profile is active.

A profile holds where the model lives, its name, the NAME of the environment variable that holds its key (never the key), how much retrieved code to send
(`context_tokens`, in Clank's own estimate units: `len(text) // 3`, which over-counts real tokens by about a quarter) and how long an answer may be.

Groq's free plan for `openai/gpt-oss-120b` (per its rate-limit page, which can change): 30 requests and 8,000 tokens a minute, 1,000 requests and 200,000 tokens a
day. 2,500 estimated tokens of code are about 1,900 real ones; with the instructions, the question and an answer cap of 1,200 tokens one question uses about
3,500, so two fit in a minute. A reasoning model's thinking counts as output, hence the low reasoning effort and a generous answer cap.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .base import host_is_local
from .errors import LLMNotConfigured
from .openai_compat import LIMIT_PARAMS, OpenAICompatibleClient

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_KEY_CHARS = re.compile(r"^[\x21-\x7e]{1,512}$")        # printable ASCII, no space: a key that cannot break out of its header


def _int_in(name: str, value, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be a whole number from {low} to {high}, got {value!r}")


@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    model: str
    api_key_env: str | None
    context_tokens: int
    max_output_tokens: int
    output_limit_param: str = "max_tokens"
    extra_body: dict = field(default_factory=dict)
    timeout: float = 60.0
    key_optional: bool = False                 # a custom service may need no key: a missing key then means "send none", not "not configured"

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("a profile needs a name")
        parts = urlsplit(self.base_url) if isinstance(self.base_url, str) else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url must be an http or https address")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("a profile needs a model name")
        if self.api_key_env is not None:
            if not isinstance(self.api_key_env, str) or not _ENV_NAME.match(self.api_key_env):
                raise ValueError("api_key_env must be the NAME of an environment variable, such as GROQ_API_KEY")
            if parts.scheme == "http" and not host_is_local(self.base_url):
                raise ValueError("a profile with a key must use https unless the model is on this computer")
        _int_in("context_tokens", self.context_tokens, 500, 16000)
        _int_in("max_output_tokens", self.max_output_tokens, 1, 20000)
        if self.output_limit_param not in LIMIT_PARAMS:
            raise ValueError(f"output_limit_param must be one of {LIMIT_PARAMS}")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or self.timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        if not isinstance(self.extra_body, dict):
            raise ValueError("extra_body must be a dict")

    @property
    def is_local(self) -> bool:
        """On this computer: code sent to it does not leave the machine, so no consent is needed."""
        return host_is_local(self.base_url)


GROQ_GPT_OSS_120B = Profile(
    name="groq", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b", api_key_env="GROQ_API_KEY",
    context_tokens=2500, max_output_tokens=1200, output_limit_param="max_completion_tokens", extra_body={"reasoning_effort": "low"}, timeout=60.0,
)

PROFILES = {"groq": GROQ_GPT_OSS_120B}


def profile_from_env(environ) -> Profile:
    """The profile named by CLANK_LLM_PROFILE (default: groq)."""
    name = (environ.get("CLANK_LLM_PROFILE") or "groq").strip()
    if name not in PROFILES:
        raise LLMNotConfigured(f"Unknown answer model profile. Known profiles: {', '.join(sorted(PROFILES))}.", env_var="CLANK_LLM_PROFILE")
    return PROFILES[name]


def check_key(raw) -> str:
    """A key as it may be held: trimmed, visible ASCII only, no spaces, at most 512 characters. Raises ValueError (never repeating the key) otherwise."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not _KEY_CHARS.match(text):
        raise ValueError("A key has only visible ASCII characters, no spaces or line breaks, and at most 512 of them.")
    return text


def make_llm(profile: Profile, environ, key: str | None = None) -> OpenAICompatibleClient:
    """The client for a profile. A profile that takes a key uses `key` if one is given (held in memory), else the environment variable it names. A missing key is
    "not configured" (the message names the variable only), unless the profile says the key is optional: then none is sent. A malformed key is always refused.
    A profile that takes no key (a local model) never gets one."""
    chosen = None
    if profile.api_key_env:
        raw = (key if key else (environ.get(profile.api_key_env) or "")).strip()
        if raw:
            try:
                chosen = check_key(raw)
            except ValueError:
                raise LLMNotConfigured(f"The key for the answer model (from the settings or {profile.api_key_env}) has characters a key cannot have (spaces, line breaks or non-ASCII letters).",
                                       env_var=profile.api_key_env) from None
        elif not profile.key_optional:
            raise LLMNotConfigured(f"The answer model needs a key: add it in the settings, or set the environment variable {profile.api_key_env}.", env_var=profile.api_key_env)
    return OpenAICompatibleClient(profile.base_url, profile.model, chosen, timeout=profile.timeout, output_limit_param=profile.output_limit_param,
                                  extra_body=profile.extra_body)


def llm_from_env(environ) -> tuple[Profile, OpenAICompatibleClient]:
    profile = profile_from_env(environ)
    return profile, make_llm(profile, environ)
