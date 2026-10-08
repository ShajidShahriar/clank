"""The usage report for one provider: each limit with what was used inside its window, the provider's own numbers as they would be now, the limit the person is closest
to, and the counts. Everything the meter and the Usage tab draw comes from here, so the window only draws it.

A limit counts the calls of THIS provider made inside its window. Tokens are prompt + thinking + answer (the thinking tokens are part of the completion, counted once),
requests are calls (a connection test is a call too). Percent is not capped (125 means over); `reached` is used >= limit.
"""
from datetime import datetime

import usage
import usage_limits
import usage_provider
import usage_windows

KIND_ORDER = ("tokens", "requests")


def _percent(used: int, limit: int) -> float:
    return round(100 * used / limit, 1)


def _limit_rows(conn, provider: str, limits: dict, source: str, now: datetime) -> list[dict]:
    rows = []
    for window in usage_windows.WINDOWS:
        if window not in limits:
            continue
        start, resets_at, rolling = usage_windows.window_bounds(window, now)
        totals = usage.totals(conn, since=start, provider=provider)
        if rolling:
            oldest = usage.oldest_call_at(conn, since=start, provider=provider)
            resets_at = None if oldest is None else usage_windows.frees_at(window, oldest_call_at=oldest)
        for kind in KIND_ORDER:
            limit = limits[window].get(kind)
            if limit is None:
                continue
            used = totals["requests"] if kind == "requests" else totals["prompt_tokens"] + totals["thinking_tokens"] + totals["answer_tokens"]
            rows.append({"window": window, "kind": kind, "limit": limit, "used": used, "percent": _percent(used, limit), "reached": used >= limit, "rolling": rolling,
                         "resets_at": resets_at, "resets_in_seconds": None if resets_at is None else resets_at - now.timestamp(), "source": source})        # (never negative: a window's next start is after now)
    return rows


def _closest(limit_rows: list[dict], provider_rows: list[dict]) -> dict | None:
    candidates = [{"source": r["source"], "window": r["window"], "kind": r["kind"], "limit": r["limit"], "used": r["used"], "percent": r["percent"], "reached": r["reached"],
                   "resets_in_seconds": r["resets_in_seconds"]} for r in limit_rows]
    candidates += [{"source": "provider", "window": r["window"], "kind": r["kind"], "limit": r["limit"], "used": r["used"], "percent": r["percent"],
                    "reached": r["used"] >= r["limit"], "resets_in_seconds": r["full_again_in_seconds"]} for r in provider_rows]
    best = None
    for candidate in candidates:                                          # the person's own limits come first, so they win a tie
        if best is None or candidate["percent"] > best["percent"]:
            best = candidate
    return best


def build_report(conn, *, provider: str, model: str | None, now: datetime) -> dict:
    """`now` is a time with its time zone (the calendar windows are the person's own days)."""
    loaded = usage_limits.load_limits(conn, provider)
    limit_rows = _limit_rows(conn, provider, loaded["limits"], loaded["source"], now)
    provider_rows = usage_provider.provider_rows(provider, usage.latest_snapshot(conn, provider), now=now.timestamp())
    totals = usage.totals(conn, since=0, provider=provider)
    spent = totals["thinking_tokens"] + totals["answer_tokens"]
    return {
        "provider": provider, "model": model, "now": now.timestamp(),
        "limits": limit_rows, "limits_source": loaded["source"], "has_suggestion": provider in usage_limits.PUBLISHED, "published_note": usage_limits.PUBLISHED_NOTE if loaded["source"] == "published" else None,
        "provider_reported": provider_rows, "closest": _closest(limit_rows, provider_rows),
        "counts": {**totals, "thinking_share": round(totals["thinking_tokens"] / spent, 4) if spent else None},
    }
