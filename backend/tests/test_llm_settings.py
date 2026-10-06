"""Task: which answer model is used is a SETTING, not code. A preset (Groq, Ollama, LM Studio, a custom OpenAI-compatible service) plus a few overrides.

- the choice is stored in the database WITHOUT any key: a key is never written to disk by the backend (it is held in memory and pushed by the desktop app);
- every field is validated when it is built, with messages that never repeat what was typed;
- a stored choice that is damaged or no longer valid falls back to the default (Groq) instead of breaking the app;
- whether a model is local is decided by its address, so an Ollama preset pointed at another machine counts as remote and needs consent.
"""
import json

import pytest

from llm.profiles import GROQ_GPT_OSS_120B
from llm.settings import PRESETS, InvalidSelection, build_selection, default_selection, load_selection, save_selection, selection_to_profile


# ---- the presets

def test_the_presets_are_groq_ollama_lmstudio_and_custom():
    assert list(PRESETS) == ["groq", "ollama", "lmstudio", "custom"]
    for preset in PRESETS.values():
        assert preset.label and preset.note and preset.id in PRESETS


def test_the_groq_preset_is_the_groq_profile_already_in_use():
    p = selection_to_profile(build_selection("groq", {}))
    assert (p.base_url, p.model, p.api_key_env, p.context_tokens, p.max_output_tokens, p.output_limit_param, p.extra_body, p.timeout) == (
        GROQ_GPT_OSS_120B.base_url, GROQ_GPT_OSS_120B.model, GROQ_GPT_OSS_120B.api_key_env, GROQ_GPT_OSS_120B.context_tokens, GROQ_GPT_OSS_120B.max_output_tokens,
        GROQ_GPT_OSS_120B.output_limit_param, GROQ_GPT_OSS_120B.extra_body, GROQ_GPT_OSS_120B.timeout)
    assert p.name == "groq" and p.is_local is False


def test_the_local_presets_run_on_this_computer_and_take_no_key():
    for preset_id, port in (("ollama", 11434), ("lmstudio", 1234)):
        p = selection_to_profile(build_selection(preset_id, {}))
        assert p.is_local and p.api_key_env is None and f":{port}/v1" in p.base_url, preset_id


def test_a_custom_service_may_take_a_key_but_does_not_need_one():
    p = selection_to_profile(build_selection("custom", {"base_url": "https://api.example.com/v1", "model": "big"}))
    assert p.api_key_env == "CLANK_LLM_API_KEY" and p.is_local is False and p.model == "big" and p.key_optional is True
    assert selection_to_profile(build_selection("groq", {})).key_optional is False


def test_every_field_of_every_preset_has_the_right_kind_of_value():
    """Presets are written with keywords because a positional slip once put Groq's description into `key_optional`."""
    expected = {"groq": (False, False), "ollama": (True, False), "lmstudio": (True, False), "custom": (True, True)}      # base_url_editable, key_optional
    for preset in PRESETS.values():
        assert isinstance(preset.note, str) and len(preset.note) > 20 and isinstance(preset.label, str)
        assert (preset.base_url_editable, preset.key_optional) == expected[preset.id], preset.id
        assert isinstance(preset.extra_body, dict) and preset.output_limit_param in ("max_tokens", "max_completion_tokens") and preset.timeout > 0


# ---- building a selection

def test_a_preset_with_no_overrides_gives_its_defaults():
    s = build_selection("ollama", {})
    assert (s.preset, s.base_url, s.model) == ("ollama", PRESETS["ollama"].base_url, PRESETS["ollama"].model)
    assert (s.context_tokens, s.max_output_tokens) == (PRESETS["ollama"].context_tokens, PRESETS["ollama"].max_output_tokens)


def test_overrides_replace_the_defaults_and_none_means_not_given():
    s = build_selection("ollama", {"base_url": "http://localhost:9999/v1", "model": "qwen2.5-coder:7b", "context_tokens": 4000, "max_output_tokens": 700})
    assert (s.base_url, s.model, s.context_tokens, s.max_output_tokens) == ("http://localhost:9999/v1", "qwen2.5-coder:7b", 4000, 700)
    s = build_selection("ollama", {"base_url": None, "model": None, "context_tokens": None, "max_output_tokens": None})
    assert s.model == PRESETS["ollama"].model


def test_surrounding_spaces_are_trimmed_and_the_trailing_slash_of_an_address_is_kept_out():
    s = build_selection("custom", {"base_url": "  https://api.example.com/v1/  ", "model": "  m  "})
    assert (s.base_url, s.model) == ("https://api.example.com/v1", "m")


@pytest.mark.parametrize("preset, overrides", [
    ("nope", {}), ("", {}), ("GROQ", {}), (5, {}),
    ("groq", {"model": ""}), ("groq", {"model": "   "}), ("groq", {"model": "x" * 201}), ("groq", {"model": 5}),
    ("groq", {"context_tokens": 499}), ("groq", {"context_tokens": 16001}), ("groq", {"context_tokens": 1.5}), ("groq", {"context_tokens": True}), ("groq", {"context_tokens": "2500"}),
    ("groq", {"max_output_tokens": 0}), ("groq", {"max_output_tokens": 20001}), ("groq", {"max_output_tokens": True}),
    ("groq", {"base_url": "https://evil.example.com/v1"}),                                  # Groq's address is fixed: a key must never be sent somewhere else
    ("custom", {}), ("custom", {"base_url": "", "model": "m"}), ("custom", {"base_url": "ftp://x/v1", "model": "m"}), ("custom", {"base_url": "not a url", "model": "m"}),
    ("custom", {"base_url": "https://api.example.com/v1"}),                                  # a custom service needs a model name
    ("custom", {"base_url": "http://api.example.com/v1", "model": "m"}),                     # a key-capable profile over plain http to another machine
    ("ollama", {"base_url": "x" * 400}),
    ("ollama", {"unknown_field": 1}),
])
def test_a_bad_selection_is_refused_without_repeating_what_was_typed(preset, overrides):
    with pytest.raises(InvalidSelection) as caught:
        build_selection(preset, overrides)
    text = str(caught.value)
    for typed in ("evil.example.com", "api.example.com", "not a url", "x" * 50):
        assert typed not in text, text


@pytest.mark.parametrize("preset, overrides, says", [
    ("custom", {"base_url": "ftp://x/v1", "model": "m"}, "http://"),
    ("custom", {"base_url": "not a url", "model": "m"}, "http://"),
    ("custom", {"base_url": "http://localhost/" + "a" * 400, "model": "m"}, "300 characters"),
    ("custom", {"model": "m"}, "needs an address"),
    ("custom", {"base_url": "https://your-server/v1"}, "model name"),
    ("groq", {"base_url": "https://other.invalid/v1"}, "cannot be changed"),
    ("groq", {"context_tokens": 10}, "code budget"),
    ("groq", {"context_tokens": 16001}, "code budget"),
    ("groq", {"context_tokens": 10 ** 9}, "code budget"),
    ("groq", {"context_tokens": True}, "code budget"),
    ("groq", {"max_output_tokens": 0}, "answer length"),
    ("groq", {"max_output_tokens": 20001}, "answer length"),
    ("groq", {"max_output_tokens": True}, "answer length"),
])
def test_each_refusal_says_which_field_to_fix(preset, overrides, says):
    """A second check (the profile's own) would refuse these too, with names from the code: the messages here are the ones written for people."""
    with pytest.raises(InvalidSelection, match=says):
        build_selection(preset, overrides)


def test_a_selection_cannot_be_changed_after_it_is_made():
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        build_selection("groq", {}).model = "x"


def test_the_default_is_groq():
    assert default_selection() == build_selection("groq", {})


# ---- storing it

def test_a_selection_is_saved_and_read_back(conn):
    chosen = build_selection("ollama", {"model": "llama3.2:3b", "context_tokens": 3500})
    save_selection(conn, chosen)
    assert load_selection(conn) == chosen


def test_nothing_stored_means_the_default(conn):
    assert load_selection(conn) == default_selection()


def test_saving_again_replaces_it(conn):
    save_selection(conn, build_selection("ollama", {}))
    save_selection(conn, build_selection("lmstudio", {}))
    assert load_selection(conn).preset == "lmstudio"
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key = 'llm'").fetchone()[0] == 1


def test_only_the_non_secret_fields_are_stored(conn):
    save_selection(conn, build_selection("custom", {"base_url": "https://api.example.com/v1", "model": "m"}))
    stored = json.loads(conn.execute("SELECT value FROM settings WHERE key = 'llm'").fetchone()[0])
    assert sorted(stored) == ["base_url", "context_tokens", "max_output_tokens", "model", "preset"]


@pytest.mark.parametrize("junk", ["not json", "[]", "5", "null", '{"preset": "nope"}', '{"preset": "groq", "model": ""}', '{"preset": "groq", "context_tokens": "x"}', ""])
def test_a_damaged_or_invalid_stored_choice_falls_back_to_the_default(conn, junk):
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('llm', ?, 'now')", (junk,))
    conn.commit()
    assert load_selection(conn) == default_selection()


def test_a_stored_extra_field_is_ignored_not_trusted(conn):
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('llm', ?, 'now')", (json.dumps({"preset": "ollama", "api_key": "gsk_leak", "model": "m1"}),))
    conn.commit()
    loaded = load_selection(conn)
    assert loaded.preset == "ollama" and loaded.model == "m1" and "gsk_leak" not in repr(loaded)


def test_the_user_settings_table_survives_a_schema_change(conn):
    """Like projects and conversations, settings are the user's data: the chunk tables may be rebuilt, this one never."""
    import db
    save_selection(conn, build_selection("ollama", {}))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    db.init_db()
    fresh = db.get_connection()
    assert load_selection(fresh).preset == "ollama"
    fresh.close()
