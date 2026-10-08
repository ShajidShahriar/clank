"""What the provider says about its own limits (its `x-ratelimit-*` headers), as of its last reply, shown as they would be NOW.

Groq's `reset-*` headers are the time until the bucket is FULL AGAIN (a refill, not midnight). So between the reply and that moment the used part is taken to drain
linearly to zero, and a snapshot past its reset shows a full bucket. Pure: no database and no clock of its own.
"""
import math
import re

# Which window each kind of limit is counted over, for the providers where it is known (Groq: requests per day, tokens per minute).
KNOWN_WINDOWS = {"groq": {"requests": "day", "tokens": "minute"}}
_UNIT_SECONDS = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|d|h|m|s)")
MAX_SECONDS = 400 * 86400.0


def parse_duration(text) -> float | None:
    """`3.675s`, `1m26.4s`, `450ms`, `2h`, `1d2h` or a plain number of seconds, as seconds. Anything else (negative, huge, not a number, not text) is None."""
    if not isinstance(text, str) or text == "":
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        value = float(text)
    else:
        parts = _PART.findall(text)
        if not parts or "".join(number + unit for number, unit in parts) != text:
            return None
        value = sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)
    return value if math.isfinite(value) and value <= MAX_SECONDS else None


def _whole(text) -> int | None:
    return int(text) if isinstance(text, str) and text.isascii() and text.isdigit() else None


def provider_rows(provider: str, snapshot: dict | None, *, now: float) -> list[dict]:
    if not snapshot:
        return []
    headers, at = snapshot["headers"], snapshot["at"]
    age = max(0.0, now - at)
    rows = []
    for kind in ("requests", "tokens"):
        limit = _whole(headers.get(f"x-ratelimit-limit-{kind}"))
        remaining = _whole(headers.get(f"x-ratelimit-remaining-{kind}"))
        if not limit or remaining is None:                                # no limit, or a limit of 0 (no percent), or no number
            continue
        remaining = min(remaining, limit)
        reset = parse_duration(headers.get(f"x-ratelimit-reset-{kind}"))
        full_again = None
        if reset is not None:
            if age >= reset:
                remaining, full_again = limit, 0.0
            else:
                remaining = round(remaining + (limit - remaining) * (age / reset)) if reset > 0 else limit
                full_again = round(reset - age, 3)
        used = limit - remaining
        rows.append({"kind": kind, "window": KNOWN_WINDOWS.get(provider, {}).get(kind), "limit": limit, "remaining": remaining, "used": used,
                     "percent": round(100 * used / limit, 1), "full_again_in_seconds": full_again, "age_seconds": age})
    return rows
