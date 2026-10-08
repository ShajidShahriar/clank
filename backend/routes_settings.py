"""The answer-model settings: `GET/PUT /settings/llm`, `PUT /settings/llm/key`, `POST /settings/llm/test`. Plain `def`.

The choice (preset, address, model, budgets) is stored. The KEY is only ever held in memory: it comes in through its own endpoint, is never returned, never logged and
never written to the database; the desktop app keeps it encrypted and pushes it again after every start.
"""
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

import usage_recording
from llm import LLMError, host_is_local
from llm.settings import PRESETS, build_selection, selection_to_profile
from services import Services, get_services

router = APIRouter()


class SelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str
    base_url: str | None = Field(default=None, max_length=400)
    model: str | None = Field(default=None, max_length=400)
    context_tokens: int | None = Field(default=None, strict=True)
    max_output_tokens: int | None = Field(default=None, strict=True)


class KeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: Annotated[str, StringConstraints(max_length=512)] | None          # required: null clears the key


def _settings(services: Services) -> dict:
    selection = services.llm_selection()
    profile = selection_to_profile(selection)
    preset = PRESETS[selection.preset]
    key_set, key_source = services.llm_key_state(profile)
    return {
        "active": {"preset": preset.id, "label": preset.label, "base_url": selection.base_url, "model": selection.model, "context_tokens": selection.context_tokens,
                   "max_output_tokens": selection.max_output_tokens, "local": profile.is_local, "takes_key": profile.api_key_env is not None, "key_optional": profile.key_optional,
                   "key_set": key_set, "key_source": key_source},
        "presets": [{"id": p.id, "label": p.label, "base_url": p.base_url, "model": p.model, "takes_key": p.api_key_env is not None, "key_optional": p.key_optional, "local": host_is_local(p.base_url),
                     "base_url_editable": p.base_url_editable, "context_tokens": p.context_tokens, "max_output_tokens": p.max_output_tokens, "note": p.note}
                    for p in PRESETS.values()],
    }


@router.get("/settings/llm")
def get_llm_settings(services: Services = Depends(get_services)):
    return _settings(services)


@router.put("/settings/llm")
def put_llm_settings(body: SelectionBody, services: Services = Depends(get_services)):
    selection = build_selection(body.preset, body.model_dump(exclude={"preset"}))            # raises InvalidSelection: nothing is saved
    services.set_llm_selection(selection)
    return _settings(services)


@router.put("/settings/llm/key")
def put_llm_key(body: KeyBody, services: Services = Depends(get_services)):
    services.set_llm_key(body.api_key)                                                       # raises InvalidKey: the old key stays
    selection = services.llm_selection()
    return {"key_set": services.llm_key_state(selection_to_profile(selection))[0]}


@router.post("/settings/llm/test")
def test_llm(services: Services = Depends(get_services)):
    """One tiny real call to the configured service. No code is sent, so no consent is needed. Failures are the same friendly errors an answer gives."""
    profile, client = services.llm_setup()
    started = time.monotonic()
    messages = [{"role": "user", "content": "Reply with the single word OK."}]
    try:
        completion = client.complete(messages, max_output_tokens=min(profile.max_output_tokens, 100))
    except LLMError as failure:
        usage_recording.record_headers(services, profile.name, getattr(failure, "rate_limit_headers", None))
        raise
    usage_recording.record_call(services, profile, messages=messages, kind="test", outcome="done", model=completion.model, prompt_tokens=completion.prompt_tokens,
                                completion_tokens=completion.completion_tokens, reasoning_tokens=completion.reasoning_tokens, thinking_pieces=0, text=completion.text,
                                headers=completion.rate_limit_headers)
    return {"ok": True, "model": completion.model, "latency_ms": int((time.monotonic() - started) * 1000), "finish_reason": completion.finish_reason,
            "reply": completion.text.strip()[:80]}
