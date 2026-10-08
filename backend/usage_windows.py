"""The time windows of a usage limit (pure: no database, no clock of its own).

Minute and hour are ROLLING: the last 60 seconds and the last hour, counted back from now. Day, week and month are CALENDAR windows in the person's own time zone: a day
starts at local midnight, a week on Monday, a month on the 1st; each starts again at a fixed moment. Times are seconds since 1970.
"""
from datetime import datetime, timedelta

WINDOWS = ("minute", "hour", "day", "week", "month")
ROLLING_SECONDS = {"minute": 60, "hour": 3600}


def window_bounds(window: str, now: datetime) -> tuple[float, float | None, bool]:
    """(start, the moment the window starts again, is it rolling). A rolling window has no fixed moment (None): see `frees_at`. `now` must know its time zone."""
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}")
    if now.tzinfo is None:
        raise ValueError("now must carry a time zone")
    if window in ROLLING_SECONDS:
        return now.timestamp() - ROLLING_SECONDS[window], None, True
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "day":
        start, nxt = midnight, midnight + timedelta(days=1)
    elif window == "week":
        start = midnight - timedelta(days=midnight.weekday())              # Monday is 0
        nxt = start + timedelta(days=7)
    else:
        start = midnight.replace(day=1)
        nxt = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.timestamp(), nxt.timestamp(), False


def frees_at(window: str, *, oldest_call_at: float) -> float | None:
    """When a rolling window next has room: the moment its oldest call is more than a window old. None for a calendar window."""
    seconds = ROLLING_SECONDS.get(window)
    return None if seconds is None else oldest_call_at + seconds
