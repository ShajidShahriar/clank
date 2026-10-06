"""Which answer model is used is a SETTING: a preset (Groq, Ollama, LM Studio, a custom OpenAI-compatible service) plus a few overrides.

Stored in the database WITHOUT any key. A key is never written to disk by the backend: it is held in memory (`Services`) and pushed by the desktop app, which keeps
it encrypted with the system's keychain. Every field is validated when the selection is built, and the messages never repeat what was typed. A stored choice that is
damaged or no longer valid falls back to the default (Groq) instead of breaking the app.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .profiles import Profile, check_key

log = logging.getLogger("clank.llm")

ALLOWED_OVERRIDES = ("base_url", "model", "context_tokens", "max_output_tokens")
MAX_ADDRESS_CHARS = 300
MAX_MODEL_CHARS = 200


class InvalidKey(ValueError):
    """A key with characters a key cannot have. The message never repeats the key."""


def clean_key(raw) -> str:
    try:
        return check_key(raw)
    except ValueError as problem:
        raise InvalidKey(str(problem)) from None


class InvalidSelection(ValueError):
    """The chosen model settings cannot be used. The message says what to fix and never repeats what was typed."""


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    base_url: str
    model: str
    api_key_env: str | None            # the variable that can hold this provider's key; None: the provider takes no key
    context_tokens: int
    max_output_tokens: int
    output_limit_param: str = "max_tokens"
    extra_body: dict = field(default_factory=dict)
    timeout: float = 60.0
    base_url_editable: bool = True     # a hosted provider's address is fixed: a key must never be sent somewhere else
    key_optional: bool = False         # a custom service may need no key
    note: str = ""


PRESETS = {p.id: p for p in (
    Preset(id="groq", label="Groq (hosted)", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b", api_key_env="GROQ_API_KEY",
           context_tokens=2500, max_output_tokens=1200, output_limit_param="max_completion_tokens", extra_body={"reasoning_effort": "low"}, timeout=60.0,
           base_url_editable=False, key_optional=False,
           note="A hosted service with a free tier (about 8,000 tokens a minute). Code excerpts are sent to it."),
    Preset(id="ollama", label="Ollama (on this computer)", base_url="http://localhost:11434/v1", model="llama3.2", api_key_env=None,
           context_tokens=3000, max_output_tokens=800, output_limit_param="max_tokens", extra_body={}, timeout=180.0, base_url_editable=True, key_optional=False,
           note="Runs on this computer, nothing leaves it. The model must be pulled first, for example: ollama pull llama3.2"),
    Preset(id="lmstudio", label="LM Studio (on this computer)", base_url="http://localhost:1234/v1", model="local-model", api_key_env=None,
           context_tokens=3000, max_output_tokens=800, output_limit_param="max_tokens", extra_body={}, timeout=180.0, base_url_editable=True, key_optional=False,
           note="Runs on this computer. Start the local server in LM Studio and load a model first."),
    Preset(id="custom", label="Custom (OpenAI-compatible)", base_url="", model="", api_key_env="CLANK_LLM_API_KEY",
           context_tokens=2500, max_output_tokens=1000, output_limit_param="max_tokens", extra_body={}, timeout=60.0, base_url_editable=True, key_optional=True,
           note="Any service that speaks the OpenAI chat protocol: OpenRouter, OpenAI, vLLM, your own server."),
)}


@dataclass(frozen=True)
class LLMSelection:
    preset: str
    base_url: str
    model: str
    context_tokens: int
    max_output_tokens: int


def _address(value) -> str:
    if not isinstance(value, str):
        raise InvalidSelection("The address must be text.")
    text = value.strip().rstrip("/")
    if len(text) > MAX_ADDRESS_CHARS:
        raise InvalidSelection(f"The address is longer than {MAX_ADDRESS_CHARS} characters.")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidSelection("The address must start with http:// or https://, for example https://your-server/v1.")
    return text


def _whole(name: str, value, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise InvalidSelection(f"{name} must be a whole number from {low} to {high}.")
    return value


def build_selection(preset_id, overrides: dict) -> LLMSelection:
    """A validated selection: the preset's defaults with the overrides applied (None means not given)."""
    if not isinstance(preset_id, str) or preset_id not in PRESETS:
        raise InvalidSelection(f"Unknown model preset. Known presets: {', '.join(PRESETS)}.")
    if not isinstance(overrides, dict) or any(key not in ALLOWED_OVERRIDES for key in overrides):
        raise InvalidSelection("Unknown setting.")
    preset = PRESETS[preset_id]
    given = {key: value for key, value in overrides.items() if value is not None}

    if "base_url" in given:
        base_url = _address(given["base_url"])
        if not preset.base_url_editable and base_url != preset.base_url:
            raise InvalidSelection("The address of this provider cannot be changed.")
    else:
        base_url = preset.base_url
    if not base_url:
        raise InvalidSelection("This service needs an address, for example https://your-server/v1.")

    model = given.get("model", preset.model)
    if not isinstance(model, str) or not model.strip() or len(model.strip()) > MAX_MODEL_CHARS:
        raise InvalidSelection(f"A model name is needed (up to {MAX_MODEL_CHARS} characters).")

    selection = LLMSelection(preset_id, base_url, model.strip(),
                             _whole("The code budget", given.get("context_tokens", preset.context_tokens), 500, 16000),
                             _whole("The answer length", given.get("max_output_tokens", preset.max_output_tokens), 1, 20000))
    try:
        selection_to_profile(selection)
    except ValueError as problem:
        raise InvalidSelection(str(problem)) from None             # for example: a profile with a key must use https
    return selection


def selection_to_profile(selection: LLMSelection) -> Profile:
    preset = PRESETS[selection.preset]
    return Profile(name=preset.id, base_url=selection.base_url, model=selection.model, api_key_env=preset.api_key_env, context_tokens=selection.context_tokens,
                   max_output_tokens=selection.max_output_tokens, output_limit_param=preset.output_limit_param, extra_body=dict(preset.extra_body), timeout=preset.timeout,
                   key_optional=preset.key_optional)


def default_selection() -> LLMSelection:
    return build_selection("groq", {})


def load_selection(conn) -> LLMSelection:
    """The stored choice, or the default when nothing is stored or what is stored is damaged or no longer valid."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'llm'").fetchone()
    if row is None:
        return default_selection()
    try:
        data = json.loads(row[0])
        if not isinstance(data, dict):
            raise InvalidSelection("not an object")
        return build_selection(data.get("preset"), {key: data.get(key) for key in ALLOWED_OVERRIDES})
    except (ValueError, TypeError):
        log.warning("the stored answer-model settings were not usable; the default is used")
        return default_selection()


def save_selection(conn, selection: LLMSelection) -> None:
    value = json.dumps({"preset": selection.preset, "base_url": selection.base_url, "model": selection.model,
                        "context_tokens": selection.context_tokens, "max_output_tokens": selection.max_output_tokens})
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('llm', ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (value, now))
    conn.commit()
