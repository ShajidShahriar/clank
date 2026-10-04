"""Task I-7.5: comparing two saved runs question by question, with the user's rule: a difference of 2 questions or fewer is a TIE, and a tie
keeps the current behavior. One question is 5 to 25 points here, so totals alone say nothing. The holdout is never compared unless asked.
"""
import pytest

from eval.compare import compare_runs, render


def q(id, rank, split="tune", found=None, kind="name"):
    return {"id": id, "kind": kind, "split": split, "rank": rank, "top1": rank == 1, "top3": rank is not None and rank <= 3,
            "top10_hit": rank is not None and rank <= 10, "found_in_context": rank is not None and rank <= 3 if found is None else found,
            "expect": [{"path": "a.py", "symbol": "f"}] if kind != "negative" else []}


def run(*questions, repo="r"):
    return {"meta": {"repo": repo}, "questions": list(questions)}


def test_each_question_is_marked_better_worse_or_same():
    a = run(q("q01", None), q("q02", 5), q("q03", 2), q("q04", 3), q("q05", 1))
    b = run(q("q01", 4), q("q02", 2), q("q03", 2), q("q04", None), q("q05", 3))
    c = compare_runs([(a, b)])
    moves = {r.id: r.move for r in c.rows}
    assert moves == {"q01": "better", "q02": "better", "q03": "same", "q04": "worse", "q05": "worse"}
    assert (c.better, c.worse, c.same) == (2, 2, 1)


def test_two_questions_that_both_miss_are_the_same():
    c = compare_runs([(run(q("q01", None)), run(q("q01", None)))])
    assert c.rows[0].move == "same"


def test_the_counts_of_top_1_3_10_and_found_in_context_are_per_run():
    a = run(q("q01", 1), q("q02", 4), q("q03", None))
    b = run(q("q01", 2), q("q02", 2), q("q03", 11))
    c = compare_runs([(a, b)])
    assert (c.counts["top1"]["a"], c.counts["top1"]["b"]) == (1, 0)
    assert (c.counts["top3"]["a"], c.counts["top3"]["b"]) == (1, 2)
    assert (c.counts["top10"]["a"], c.counts["top10"]["b"]) == (2, 2)
    assert c.n == 3


@pytest.mark.parametrize("gain, verdict", [(0, "tie"), (1, "tie"), (2, "tie"), (3, "b is better"), (-2, "tie"), (-3, "a is better")])
def test_a_difference_of_two_questions_or_fewer_is_a_tie(gain, verdict):
    n = 8
    a_ranks = [1] * 4 + [None] * 4                      # a: 4 questions in the top 3
    b_hits = max(4 + gain, 0)
    b_ranks = [1] * b_hits + [None] * (n - b_hits)
    a = run(*[q(f"q{i:02d}", r) for i, r in enumerate(a_ranks)])
    b = run(*[q(f"q{i:02d}", r) for i, r in enumerate(b_ranks)])
    assert compare_runs([(a, b)]).verdict("top3") == verdict


def test_the_holdout_is_left_out_unless_asked_for():
    a = run(q("q01", 1), q("q03", 1, split="holdout"))
    b = run(q("q01", 1), q("q03", None, split="holdout"))
    assert compare_runs([(a, b)]).n == 1
    c = compare_runs([(a, b)], include_holdout=True)
    assert c.n == 2 and c.worse == 1


def test_negatives_are_not_compared():
    a = run(q("q01", 1), q("q22", None, kind="negative"))
    b = run(q("q01", 1), q("q22", None, kind="negative"))
    assert compare_runs([(a, b)]).n == 1


def test_several_repos_are_added_together_and_rows_say_which_repo():
    a1, b1 = run(q("q01", 1), repo="clank"), run(q("q01", 2), repo="clank")
    a2, b2 = run(q("q02", 5), repo="flask"), run(q("q02", 3), repo="flask")
    c = compare_runs([(a1, b1), (a2, b2)])
    assert c.n == 2 and [r.repo for r in c.rows] == ["clank", "flask"]
    assert (c.better, c.worse) == (1, 1)


def test_questions_missing_from_one_run_are_refused_not_skipped():
    with pytest.raises(ValueError, match="q02"):
        compare_runs([(run(q("q01", 1), q("q02", 1)), run(q("q01", 1)))])


def test_runs_made_with_different_models_are_refused():
    a, b = run(q("q01", 1)), run(q("q01", 1))
    a["meta"]["model"], b["meta"]["model"] = "m@1", "m@2"
    with pytest.raises(ValueError, match="model"):
        compare_runs([(a, b)])


def test_found_in_context_is_compared_too():
    a = run(q("q01", 2, found=True), q("q02", 2, found=False))
    b = run(q("q01", 2, found=False), q("q02", 2, found=False))
    c = compare_runs([(a, b)])
    assert (c.counts["context"]["a"], c.counts["context"]["b"]) == (1, 0)


def test_the_printed_table_lists_every_question_and_the_verdict():
    a, b = run(q("q01", 1), q("q02", None)), run(q("q01", 3), q("q02", 2))
    text = render(compare_runs([(a, b)]), "control", "raw")
    for word in ("q01", "q02", "better", "worse", "tie", "control", "raw", "top-3"):
        assert word in text
