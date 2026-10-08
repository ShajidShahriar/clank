"""The time windows of a usage limit. Minute and hour are ROLLING (the last 60 seconds, the last hour). Day, week and month are CALENDAR windows in the person's own time
zone: a day starts at local midnight, a week on Monday, a month on the 1st; each has a clear moment when it starts again."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import usage_windows as w

DHAKA = ZoneInfo("Asia/Dhaka")
LONDON = ZoneInfo("Europe/London")


def at(year, month, day, hour=0, minute=0, second=0, tz=DHAKA):
    return datetime(year, month, day, hour, minute, second, tzinfo=tz)


def bounds(window, now):
    start, resets, rolling = w.window_bounds(window, now)
    return start, resets, rolling


def test_the_windows_are_these_five_in_this_order():
    assert w.WINDOWS == ("minute", "hour", "day", "week", "month")


def test_minute_and_hour_roll_back_from_now_and_have_no_fixed_reset():
    now = at(2026, 10, 8, 13, 20, 30)
    assert bounds("minute", now) == (now.timestamp() - 60, None, True)
    assert bounds("hour", now) == (now.timestamp() - 3600, None, True)


def test_a_day_starts_at_local_midnight_and_starts_again_at_the_next():
    start, resets, rolling = bounds("day", at(2026, 10, 8, 13, 20))
    assert (start, resets, rolling) == (at(2026, 10, 8).timestamp(), at(2026, 10, 9).timestamp(), False)


def test_the_last_second_of_a_day_still_belongs_to_it_and_midnight_starts_the_next():
    assert bounds("day", at(2026, 10, 8, 23, 59, 59))[0] == at(2026, 10, 8).timestamp()
    assert bounds("day", at(2026, 10, 9))[0] == at(2026, 10, 9).timestamp()


def test_the_time_of_day_down_to_the_microsecond_does_not_move_the_start():
    now = datetime(2026, 10, 8, 13, 20, 30, 123456, tzinfo=DHAKA)
    assert bounds("day", now)[0] == at(2026, 10, 8).timestamp()
    assert bounds("month", now)[0] == at(2026, 10, 1).timestamp()


def test_a_week_starts_on_monday():
    monday = at(2026, 10, 5).timestamp()
    next_monday = at(2026, 10, 12).timestamp()
    assert bounds("week", at(2026, 10, 8, 13, 20)) == (monday, next_monday, False)       # a Thursday
    assert bounds("week", at(2026, 10, 5, 0, 0, 0))[0] == monday                        # Monday, the first second
    assert bounds("week", at(2026, 10, 11, 23, 59, 59)) == (monday, next_monday, False)    # Sunday, the last second
    assert bounds("week", at(2026, 10, 12))[0] == next_monday


def test_a_month_starts_on_the_first_and_december_ends_in_the_next_year():
    assert bounds("month", at(2026, 10, 8)) == (at(2026, 10, 1).timestamp(), at(2026, 11, 1).timestamp(), False)
    assert bounds("month", at(2026, 12, 31, 23, 59)) == (at(2026, 12, 1).timestamp(), at(2027, 1, 1).timestamp(), False)
    assert bounds("month", at(2026, 2, 28))[1] == at(2026, 3, 1).timestamp()


def test_a_day_that_has_23_or_25_hours_still_ends_at_local_midnight():
    spring = bounds("day", at(2026, 3, 29, 12, tz=LONDON))             # the clocks go forward: a 23 hour day
    assert spring[1] - spring[0] == 23 * 3600
    autumn = bounds("day", at(2026, 10, 25, 12, tz=LONDON))            # the clocks go back: a 25 hour day
    assert autumn[1] - autumn[0] == 25 * 3600


def test_a_naive_time_or_an_unknown_window_is_refused():
    with pytest.raises(ValueError):
        w.window_bounds("day", datetime(2026, 10, 8, 12, 0))
    with pytest.raises(ValueError):
        w.window_bounds("year", at(2026, 10, 8))


def test_a_rolling_window_frees_up_when_its_oldest_call_leaves_it():
    assert w.frees_at("minute", oldest_call_at=1000.0) == 1060.0
    assert w.frees_at("hour", oldest_call_at=1000.0) == 4600.0
    assert w.frees_at("day", oldest_call_at=1000.0) is None             # a calendar window starts again at a fixed moment instead
