"""The person's own usage limits: per provider, per window (minute, hour, day, week, month), in tokens and/or requests.

Groq's published free tier is a SUGGESTION until the person saves limits of their own: shown as published (it can change), editable, and clearable. What is stored is
exactly what the person saved; "nothing" (a cleared set) and "never set" (the suggestion) are different things."""
import pytest

import usage_limits as ul

GROQ_FREE = {"minute": {"requests": 30, "tokens": 8000}, "day": {"requests": 1000, "tokens": 200000}}


def test_groqs_published_free_tier_is_what_the_suggestion_says(conn):
    assert ul.PUBLISHED["groq"] == GROQ_FREE
    assert "groq" in ul.PUBLISHED_NOTE.lower() and "change" in ul.PUBLISHED_NOTE.lower()


def test_with_nothing_saved_groq_gets_the_published_numbers_and_others_get_none(conn):
    assert ul.load_limits(conn, "groq") == {"limits": GROQ_FREE, "source": "published"}
    assert ul.load_limits(conn, "ollama") == {"limits": {}, "source": "none"}


def test_the_suggestion_handed_out_is_a_copy_that_cannot_change_the_published_numbers(conn):
    ul.load_limits(conn, "groq")["limits"]["minute"]["tokens"] = 1
    assert ul.PUBLISHED["groq"]["minute"]["tokens"] == 8000 and ul.load_limits(conn, "groq")["limits"]["minute"]["tokens"] == 8000


def test_saved_limits_replace_the_suggestion_and_come_back_as_saved(conn):
    ul.save_limits(conn, "groq", {"hour": {"tokens": 50000}, "week": {"requests": 5000}})
    assert ul.load_limits(conn, "groq") == {"limits": {"hour": {"tokens": 50000}, "week": {"requests": 5000}}, "source": "yours"}


def test_clearing_means_no_limits_and_going_back_means_the_suggestion_again(conn):
    ul.save_limits(conn, "groq", {})
    assert ul.load_limits(conn, "groq") == {"limits": {}, "source": "none"}
    ul.save_limits(conn, "groq", None)
    assert ul.load_limits(conn, "groq")["source"] == "published"


def test_limits_are_kept_per_provider(conn):
    ul.save_limits(conn, "groq", {"day": {"tokens": 1000}})
    ul.save_limits(conn, "ollama", {"day": {"tokens": 9}})
    assert ul.load_limits(conn, "groq")["limits"] == {"day": {"tokens": 1000}}
    assert ul.load_limits(conn, "ollama")["limits"] == {"day": {"tokens": 9}}


def test_saving_twice_keeps_the_latest(conn):
    ul.save_limits(conn, "groq", {"day": {"tokens": 1}})
    ul.save_limits(conn, "groq", {"day": {"tokens": 2}})
    assert ul.load_limits(conn, "groq")["limits"] == {"day": {"tokens": 2}}
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'usage_limits:%'").fetchone()[0] == 1


def test_limits_survive_a_schema_change(conn):
    ul.save_limits(conn, "groq", {"day": {"tokens": 7}})
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    import db
    db.init_db()
    assert ul.load_limits(conn, "groq")["limits"] == {"day": {"tokens": 7}}


def test_a_damaged_stored_value_falls_back_to_the_suggestion(conn):
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('usage_limits:groq', 'not json', 'now')")
    conn.commit()
    assert ul.load_limits(conn, "groq")["source"] == "published"
    conn.execute("UPDATE settings SET value = '{\"year\": {\"tokens\": 5}}' WHERE key = 'usage_limits:groq'")
    conn.commit()
    assert ul.load_limits(conn, "groq")["source"] == "published"                   # a stored value that is no longer valid is not used either


# ---- what may be saved

def test_validation_keeps_known_windows_and_drops_empty_ones():
    assert ul.validate_limits({"day": {"tokens": 100, "requests": None}, "hour": {}, "week": {"tokens": None}}) == {"day": {"tokens": 100}}
    assert ul.validate_limits({}) == {}


@pytest.mark.parametrize("raw, message", [
    ("nope", "limits"),
    ({"year": {"tokens": 5}}, "window"),
    ({"day": {"words": 5}}, "tokens or requests"),
    ({"day": "five"}, "day"),
    ({"day": {"tokens": 0}}, "at least 1"),
    ({"day": {"tokens": -5}}, "at least 1"),
    ({"day": {"tokens": 2.5}}, "whole number"),
    ({"day": {"tokens": True}}, "whole number"),
    ({"day": {"tokens": "5"}}, "whole number"),
    ({"day": {"tokens": 10 ** 13}}, "too large"),
])
def test_a_bad_set_of_limits_is_refused_with_a_sentence_that_does_not_repeat_it(raw, message):
    with pytest.raises(ul.InvalidLimits) as caught:
        ul.validate_limits(raw)
    assert message in str(caught.value)
    assert "five" not in str(caught.value).replace("day", "") or message == "day"


def test_saving_validates_first_and_stores_nothing_when_refused(conn):
    with pytest.raises(ul.InvalidLimits):
        ul.save_limits(conn, "groq", {"day": {"tokens": -1}})
    assert ul.load_limits(conn, "groq")["source"] == "published"
