"""What the answer model has been used for: one row per model call, and the provider's latest rate-limit numbers.

A row holds only WHEN, which provider and model, the token counts, whether any count is an estimate, and how the call ended. Never a question, an answer, a project or a
path: usage must be countable without being a second copy of the conversations. These tables are the user's data (never dropped by a schema change); "Reset counts"
clears the calls and leaves the provider's numbers.
"""
import json
import math

KINDS = ("answer", "test")                   # a real question, or the settings dialog's "Test connection" (it costs a few tokens too)
OUTCOMES = ("done", "stopped", "failed")     # finished, stopped by the person, or failed in the middle (tokens may still have been spent)
MAX_HEADER_VALUE_CHARS = 64
_KEPT_PREFIXES = ("x-ratelimit-",)
_KEPT_NAMES = ("retry-after",)


def _count(name: str, value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a whole number of at least 0 or None, got {value!r}")
    return value


def record_call(conn, *, at, provider, model, kind, outcome, prompt_tokens, thinking_tokens, answer_tokens, estimated) -> int:
    """Stores one model call. `at` is seconds since 1970 (UTC). Counts are None when unknown. `estimated` is True when any count is Clank's own estimate."""
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        raise ValueError(f"at must be a finite number of seconds, got {at!r}")
    if not isinstance(provider, str) or provider.strip() == "":
        raise ValueError("provider must be a non-empty text")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    if not isinstance(estimated, bool):
        raise ValueError("estimated must be True or False")
    if model is not None and not isinstance(model, str):
        raise ValueError("model must be a text or None")
    counts = (_count("prompt_tokens", prompt_tokens), _count("thinking_tokens", thinking_tokens), _count("answer_tokens", answer_tokens))
    cursor = conn.execute(
        "INSERT INTO usage_calls (at, provider, model, kind, outcome, prompt_tokens, thinking_tokens, answer_tokens, estimated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (float(at), provider, model, kind, outcome, *counts, int(estimated)))
    conn.commit()
    return cursor.lastrowid


def totals(conn, *, since, provider: str | None = None) -> dict:
    """What was used by calls made at or after `since`, for one provider or all. An unknown count adds nothing."""
    where, args = "at >= ?", [since]
    if provider is not None:
        where += " AND provider = ?"
        args.append(provider)
    row = conn.execute(
        f"SELECT COUNT(*) AS requests, COALESCE(SUM(prompt_tokens), 0) AS prompt, COALESCE(SUM(thinking_tokens), 0) AS thinking, COALESCE(SUM(answer_tokens), 0) AS answer, "
        f"COALESCE(SUM(estimated), 0) AS estimated FROM usage_calls WHERE {where}", args).fetchone()
    return {"requests": row["requests"], "prompt_tokens": row["prompt"], "thinking_tokens": row["thinking"], "answer_tokens": row["answer"], "estimated_calls": row["estimated"]}


def oldest_call_at(conn, *, since, provider: str) -> float | None:
    """The time of the oldest call of this provider made at or after `since` (None when there is none): when it leaves a rolling window, that window has room again."""
    return conn.execute("SELECT MIN(at) FROM usage_calls WHERE at >= ? AND provider = ?", (since, provider)).fetchone()[0]


def clear_calls(conn) -> None:
    conn.execute("DELETE FROM usage_calls")
    conn.commit()


def record_snapshot(conn, provider: str, headers: dict, *, at: float) -> None:
    """Keeps the provider's latest rate-limit numbers (only `x-ratelimit-*` and `retry-after`, values cut short). Headers with none of those change nothing: a reply
    without them must not wipe the last good numbers."""
    kept = {}
    for name, value in (headers or {}).items():
        lower = str(name).lower()
        if lower in _KEPT_NAMES or lower.startswith(_KEPT_PREFIXES):
            kept[lower] = str(value)[:MAX_HEADER_VALUE_CHARS]
    if not kept:
        return
    conn.execute("INSERT INTO usage_snapshots (provider, headers, at) VALUES (?, ?, ?) ON CONFLICT(provider) DO UPDATE SET headers = excluded.headers, at = excluded.at",
                 (provider, json.dumps(kept, separators=(",", ":")), float(at)))
    conn.commit()


def latest_snapshot(conn, provider: str) -> dict | None:
    row = conn.execute("SELECT headers, at FROM usage_snapshots WHERE provider = ?", (provider,)).fetchone()
    return None if row is None else {"headers": json.loads(row["headers"]), "at": row["at"]}
