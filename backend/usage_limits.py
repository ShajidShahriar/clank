"""The person's own usage limits, per provider: a number of tokens and/or requests per window (see usage_windows.py).

Stored in the `settings` table (user data) as `usage_limits:<provider>`. Three states: NEVER SET (Groq shows its published free tier as a suggestion, any other provider
shows none), SET (exactly what the person saved), CLEARED (saved as empty: no limits at all, the suggestion does not come back by itself). Limits only WARN: nothing here
stops a question.
"""
import json
import logging
from datetime import datetime, timezone

from usage_windows import WINDOWS

log = logging.getLogger("clank.usage")

KINDS = ("requests", "tokens")
MAX_LIMIT = 10 ** 12
# What Groq says its free tier allows (its rate-limit page, 2026-10-07). It can change, so it is only ever a suggestion, and shown as published.
PUBLISHED = {"groq": {"minute": {"requests": 30, "tokens": 8000}, "day": {"requests": 1000, "tokens": 200000}}}
PUBLISHED_NOTE = "These are the free-tier limits Groq publishes. They can change: check Groq's rate-limit page and edit them if they do."


class InvalidLimits(ValueError):
    """The limits that were sent cannot be saved. The message is written for people and never repeats what was typed."""


def validate_limits(raw) -> dict:
    if not isinstance(raw, dict):
        raise InvalidLimits("The limits must be a set of windows, each with a number of tokens or requests.")
    clean = {}
    for window, value in raw.items():
        if window not in WINDOWS:
            raise InvalidLimits(f"That is not a window Clank knows. Use one of: {', '.join(WINDOWS)}.")
        if not isinstance(value, dict):
            raise InvalidLimits(f"The {window} limit must hold tokens or requests.")
        unknown = set(value) - set(KINDS)
        if unknown:
            raise InvalidLimits(f"The {window} limit can only be in tokens or requests.")
        kept = {}
        for kind in KINDS:
            number = value.get(kind)
            if number is None:
                continue
            if isinstance(number, bool) or not isinstance(number, int):
                raise InvalidLimits(f"The {window} {kind} limit must be a whole number.")
            if number < 1:
                raise InvalidLimits(f"The {window} {kind} limit must be at least 1.")
            if number > MAX_LIMIT:
                raise InvalidLimits(f"The {window} {kind} limit is too large.")
            kept[kind] = number
        if kept:
            clean[window] = kept
    return clean


def _key(provider: str) -> str:
    return f"usage_limits:{provider}"


def load_limits(conn, provider: str) -> dict:
    """`{"limits": {window: {kind: number}}, "source": "yours" | "published" | "none"}`."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (_key(provider),)).fetchone()
    if row is not None:
        try:
            return {"limits": validate_limits(json.loads(row[0])), "source": "yours" if json.loads(row[0]) else "none"}
        except (ValueError, TypeError):                                    # damaged, or no longer valid: the suggestion is used
            log.warning("the stored usage limits were not usable; the default is used")
    published = PUBLISHED.get(provider)
    return {"limits": {w: dict(k) for w, k in published.items()}, "source": "published"} if published else {"limits": {}, "source": "none"}


def save_limits(conn, provider: str, limits) -> None:
    """Saves the person's limits. `{}` clears them (no limits). `None` forgets them (back to the suggestion)."""
    if limits is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (_key(provider),))
        conn.commit()
        return
    clean = validate_limits(limits)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (_key(provider), json.dumps(clean, separators=(",", ":")), now))
    conn.commit()
