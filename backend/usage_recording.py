"""Writes what the routes know about a model call into the usage tables (usage.py). Best effort: a failure here is logged and never touches the answer.

Tokens: the service's own numbers when it gave them, otherwise Clank's estimates (the same ones the answer's summary line uses), and `estimated` says whether any of
them is an estimate. A call refused before any word (a bad key, a 429, a too long request) spent nothing and is not a call: only its rate-limit numbers are kept.
"""
import logging
import time

import usage
from answer_stats import token_summary
from chunker.core import estimate_tokens

log = logging.getLogger("clank.usage")
_wall = time.time                       # the tests set the time by hand


def record_headers(services, provider: str, headers) -> None:
    """Keep the provider's latest rate-limit numbers (from a reply, or from a refusal). Headers with none of them change nothing (see usage.record_snapshot)."""
    try:
        conn = services.connect()
        try:
            usage.record_snapshot(conn, provider, headers, at=_wall())
        finally:
            conn.close()
    except Exception as problem:        # noqa: BLE001 - recording must never break an answer
        log.error("could not record the provider's rate-limit numbers", exc_info=problem)


def record_call(services, profile, *, messages, kind, outcome, model, prompt_tokens, completion_tokens, reasoning_tokens, thinking_pieces, text, headers=None) -> None:
    """One model call that spent tokens: an answer (done, stopped or failed in the middle) or the settings dialog's test."""
    try:
        summary = token_summary(reported_thinking=reasoning_tokens, reported_completion=completion_tokens, thinking_pieces=thinking_pieces, text=text)
        prompt_estimated = prompt_tokens is None
        prompt = sum(estimate_tokens(m["content"]) for m in messages) if prompt_estimated else prompt_tokens
        conn = services.connect()
        try:
            usage.record_call(conn, at=_wall(), provider=profile.name, model=model or profile.model, kind=kind, outcome=outcome, prompt_tokens=prompt,
                              thinking_tokens=summary["thinking"], answer_tokens=summary["answer"], estimated=summary["estimated"] or prompt_estimated)
            usage.record_snapshot(conn, profile.name, headers or {}, at=_wall())
        finally:
            conn.close()
    except Exception as problem:        # noqa: BLE001 - recording must never break an answer
        log.error("could not record the model call", exc_info=problem)
