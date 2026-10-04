"""Task I-7.6: ranking policy for tests and changelogs, a pure function with hand-made hits. NOT wired into search yet.

Rule under test: tests and changelogs lose a small ADDITIVE margin when hits are ORDERED (the raw cosine score is never changed, because the relevance
floor study needs it). Nothing is hidden: a test that beats the best code by more than the margin stays first. A question that asks for tests
(or for a changelog) is not demoted for it. Intent words match whole words only ("latest version" and "contest" are not about tests).
"""
import pytest

from search.core import Hit
from search.policy import Adjusted, apply_test_policy, intent_of


def hit(id, score, path):
    return Hit({"id": id, "rel_path": path}, score)


TAGS = {"src/app.py": (False, False), "tests/test_app.py": (True, False), "CHANGELOG.md": (False, True), "other.py": (False, False)}


def order(result):
    return [a.hit.chunk["id"] for a in result]


def policy(hits, question="how does login work?", margin=0.06, **kw):
    return apply_test_policy(hits, question, TAGS, margin=margin, **kw)


# ---- the rule

def test_a_test_that_wins_by_less_than_the_margin_falls_behind_the_code():
    result = policy([hit("t", 0.70, "tests/test_app.py"), hit("c", 0.66, "src/app.py")], margin=0.06)
    assert order(result) == ["c", "t"]


def test_a_test_that_wins_by_more_than_the_margin_stays_first_and_nothing_is_hidden():
    result = policy([hit("t", 0.80, "tests/test_app.py"), hit("c", 0.66, "src/app.py")], margin=0.06)
    assert order(result) == ["t", "c"]


def test_the_margin_is_a_strict_inequality_a_tie_in_the_adjusted_score_keeps_the_original_order():
    result = policy([hit("t", 0.72, "tests/test_app.py"), hit("c", 0.66, "src/app.py")], margin=0.06)
    assert order(result) == ["t", "c"], "0.72 - 0.06 equals 0.66: the original order wins a tie"


def test_the_same_number_of_hits_comes_out_and_the_input_is_not_changed():
    hits = [hit("t", 0.70, "tests/test_app.py"), hit("c", 0.66, "src/app.py"), hit("x", 0.50, "other.py")]
    before = list(hits)
    result = policy(hits)
    assert len(result) == 3 and sorted(order(result)) == ["c", "t", "x"] and hits == before


def test_equal_scores_keep_a_stable_order():
    hits = [hit(str(i), 0.5, "src/app.py") for i in range(6)]
    assert order(policy(hits)) == [str(i) for i in range(6)]


def test_a_zero_margin_changes_nothing():
    hits = [hit("t", 0.70, "tests/test_app.py"), hit("c", 0.69, "src/app.py")]
    assert order(policy(hits, margin=0.0)) == ["t", "c"]


def test_unknown_files_count_as_code():
    result = policy([hit("t", 0.70, "tests/test_app.py"), hit("u", 0.66, "never-seen.py")])
    assert order(result) == ["u", "t"]


# ---- raw scores are never touched

def test_the_hits_and_their_raw_scores_come_back_unchanged_with_a_separate_adjusted_value():
    t, c = hit("t", 0.70, "tests/test_app.py"), hit("c", 0.66, "src/app.py")
    result = policy([t, c], margin=0.06)
    assert [a.hit for a in result] == [c, t] and [a.hit.score for a in result] == [0.66, 0.70]
    assert all(isinstance(a, Adjusted) for a in result)
    assert result[0].adjusted == pytest.approx(0.66) and result[1].adjusted == pytest.approx(0.64)


# ---- changelogs

def test_a_changelog_takes_twice_the_margin_by_default():
    code, log = hit("c", 0.66, "src/app.py"), hit("l", 0.75, "CHANGELOG.md")
    assert order(policy([log, code], margin=0.06)) == ["c", "l"]            # 0.75 - 0.12 = 0.63 < 0.66
    assert order(policy([hit("l", 0.75, "CHANGELOG.md"), hit("c", 0.66, "src/app.py")], margin=0.06, changelog_margin=0.0)) == ["l", "c"]


def test_a_changelog_is_not_treated_as_a_test_and_a_test_not_as_a_changelog():
    both = policy([hit("l", 0.70, "CHANGELOG.md"), hit("c", 0.58, "src/app.py")], margin=0.06)       # 0.70 - 0.12 = 0.58: equal, keeps order
    assert order(both) == ["l", "c"]
    assert policy([hit("l", 0.70, "CHANGELOG.md")], margin=0.06)[0].adjusted == pytest.approx(0.58)
    assert policy([hit("t", 0.70, "tests/test_app.py")], margin=0.06)[0].adjusted == pytest.approx(0.64)


def test_a_file_tagged_as_both_takes_the_larger_margin_once():
    tags = {"both.md": (True, True)}
    result = apply_test_policy([hit("b", 0.70, "both.md")], "how?", tags, margin=0.06)
    assert result[0].adjusted == pytest.approx(0.58)


# ---- intent: asking for tests or a changelog switches that demotion off

@pytest.mark.parametrize("question", [
    "where is login tested?", "where is it TESTED", "show the tests for login", "how is login testing done", "which spec covers login",
    "where is the fixture for the app", "what does the pytest config do", "is there a test for retry", "unit tests of the parser",
])
def test_questions_that_ask_for_tests_switch_the_test_demotion_off(question):
    assert intent_of(question).test
    assert order(policy([hit("t", 0.70, "tests/test_app.py"), hit("c", 0.66, "src/app.py")], question=question)) == ["t", "c"]


@pytest.mark.parametrize("question", ["what changed in 2.0?", "show the changelog", "the release notes for 3.1", "what's new in this release", "What Changed since 1.4"])
def test_questions_that_ask_for_a_changelog_switch_the_changelog_demotion_off(question):
    assert intent_of(question).changelog
    result = policy([hit("l", 0.75, "CHANGELOG.md"), hit("c", 0.66, "src/app.py")], question=question)
    assert order(result) == ["l", "c"]


@pytest.mark.parametrize("question", [
    "what is the latest version", "how does the contest app work", "attestation of the signature", "what is the fastest route", "a specific handler",
    "how does login work", "what version of python is needed", "the greatest common divisor", "protest handling", "detest", "specification of the format",
])
def test_words_that_only_contain_test_or_spec_do_not_trigger_the_intent(question):
    assert not intent_of(question).test and not intent_of(question).changelog


def test_the_word_version_alone_is_not_a_changelog_intent():
    assert not intent_of("what version of flask is this").changelog and not intent_of("latest version").changelog


def test_test_intent_does_not_switch_the_changelog_demotion_off_and_the_other_way_round():
    q = "where is login tested?"
    result = policy([hit("l", 0.75, "CHANGELOG.md"), hit("c", 0.66, "src/app.py")], question=q)
    assert order(result) == ["c", "l"]
    q = "show the changelog"
    result = policy([hit("t", 0.70, "tests/test_app.py"), hit("c", 0.66, "src/app.py")], question=q)
    assert order(result) == ["c", "t"]


# ---- arguments

@pytest.mark.parametrize("bad", [dict(margin=-0.01), dict(margin=float("nan")), dict(margin="0.06"), dict(margin=0.06, changelog_margin=-1)])
def test_a_bad_margin_is_refused(bad):
    with pytest.raises(ValueError, match="margin"):
        apply_test_policy([hit("c", 0.5, "src/app.py")], "q", TAGS, **bad)


def test_a_blank_question_is_refused():
    with pytest.raises(ValueError, match="question"):
        apply_test_policy([hit("c", 0.5, "src/app.py")], "  ", TAGS, margin=0.06)


def test_no_hits_gives_no_hits():
    assert policy([]) == []
