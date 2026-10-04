"""Task I-7.11 gate: should a second embedding model be tried at all? Read from the STORED results, no new embedding (eval/gate.py).

The plan's rule: run 7.11 only if found-in-context at 6000 tokens is still below 17 of 20 (85%) AND at least 3 misses are MEANING misses, after every miss
has been labelled by cause. A meaning miss = the answer is not among the 30 nearest chunks at all (the model did not connect the question to it); a miss
that was retrieved but ranked low, or ranked but left out of the context by the budget, is a ranking or budget problem that another model does not fix.
"""
import math

import pytest

from eval.gate import MEANING_MISS, TARGET, gate_7_11, label_miss


def hit(path="x.py", symbol="other", names=()):
    return {"id": f"{path}:{symbol}", "score": 0.5, "path": path, "symbol": symbol, "parent": None, "names": list(names), "kind": "function", "part": None,
            "is_test": False, "is_changelog": False}


ANSWER = ("a.py", "f")


def question(id, rank, found, pool_rank, split="tune", expect=True):
    """`pool_rank`: where the answer sits in the raw pool of 30 (None: not in it)."""
    pool = [hit("x.py", f"s{i}") for i in range(30)]
    if pool_rank:
        pool[pool_rank - 1] = hit(*ANSWER)
    return {"id": id, "kind": "name", "split": split, "expect": [{"path": ANSWER[0], "symbol": ANSWER[1]}] if expect else [], "rank": rank,
            "found_in_context": found, "pool": pool}


# ---- the label of one miss

def test_an_answer_that_is_not_among_the_30_nearest_is_a_meaning_miss():
    assert label_miss(question("q01", None, False, None))[0] == MEANING_MISS == "not retrieved"


def test_an_answer_ranked_11_to_30_is_a_ranking_miss_not_a_meaning_miss():
    assert label_miss(question("q01", None, False, 25))[0] == "ranked 11-30"


def test_an_answer_in_the_top_10_but_outside_the_top_3_and_not_in_the_context_is_labelled_so():
    assert label_miss(question("q01", 4, False, 8))[0] == "ranked 4-10, not in context"
    assert label_miss(question("q01", 10, False, 12))[0] == "ranked 4-10, not in context"


def test_an_answer_in_the_top_3_that_the_context_still_left_out_is_labelled_so():
    assert label_miss(question("q01", 2, False, 2))[0] == "ranked top 3, not in context"


def test_the_detail_says_where_the_answer_was():
    assert "rank 4" in label_miss(question("q01", 4, False, 8))[1] and "pool position 8" in label_miss(question("q01", 4, False, 8))[1]
    assert "not among" in label_miss(question("q01", None, False, None))[1]


def test_only_a_question_that_missed_the_context_can_be_labelled():
    with pytest.raises(ValueError, match="found"):
        label_miss(question("q01", 1, True, 1))
    with pytest.raises(ValueError, match="answer"):
        label_miss(question("q22", None, False, None, expect=False))


# ---- the gate

def test_the_target_is_17_of_20():
    assert TARGET == 17 / 20


def run_gate(*questions, **kw):
    return gate_7_11(list(questions), **kw)


def test_the_gate_scales_the_target_to_the_number_of_questions_and_rounds_up():
    g = run_gate(*[question(f"q{i:02d}", 1, True, 1) for i in range(14)])
    assert g.n == 14 and g.found == 14 and g.target_count == math.ceil(0.85 * 14) == 12 and not g.below_target
    twenty = run_gate(*[question(f"q{i:02d}", 1, i < 17, 1) for i in range(20)])
    assert twenty.target_count == 17 and not twenty.below_target
    assert run_gate(*[question(f"q{i:02d}", 1, i < 16, 1 if i < 16 else None) for i in range(20)]).below_target


def test_at_or_above_the_target_the_model_stays_whatever_the_misses_are():
    qs = [question(f"q{i:02d}", 1, True, 1) for i in range(12)] + [question("q90", None, False, None), question("q91", None, False, None)]
    g = run_gate(*qs)
    assert g.found == 12 and not g.below_target and g.run is False and "target met" in g.reason


def test_below_the_target_with_fewer_than_three_meaning_misses_the_model_stays_and_the_causes_are_listed():
    qs = ([question(f"q{i:02d}", 1, True, 1) for i in range(9)] + [question("q90", None, False, None), question("q91", 4, False, 8), question("q92", None, False, 25)])
    g = run_gate(*qs)
    assert g.below_target and g.meaning_misses == 1 and g.run is False
    assert [(m[0], m[1]) for m in g.misses] == [("q90", "not retrieved"), ("q91", "ranked 4-10, not in context"), ("q92", "ranked 11-30")]
    assert "q90" in g.reason and "1 meaning miss" in g.reason


def test_below_the_target_with_three_meaning_misses_the_gate_opens():
    qs = [question(f"q{i:02d}", 1, True, 1) for i in range(9)] + [question(f"q9{i}", None, False, None) for i in range(3)]
    g = run_gate(*qs)
    assert g.below_target and g.meaning_misses == 3 and g.run is True and "run 7.11" in g.reason


def test_two_meaning_misses_are_not_enough():
    qs = [question(f"q{i:02d}", 1, True, 1) for i in range(9)] + [question("q90", None, False, None), question("q91", None, False, None), question("q92", 4, False, 8)]
    assert run_gate(*qs).run is False and run_gate(*qs).meaning_misses == 2


def test_negatives_and_the_holdout_are_not_counted():
    qs = [question(f"q{i:02d}", 1, True, 1) for i in range(12)] + [question("q22", None, False, None, expect=False),
                                                                   question("q03", None, False, None, split="holdout")]
    g = run_gate(*qs)
    assert g.n == 12 and g.found == 12 and g.misses == []
    assert run_gate(*qs, include_holdout=True).n == 13


def test_a_gate_over_nothing_is_refused():
    with pytest.raises(ValueError, match="questions"):
        run_gate()
    with pytest.raises(ValueError, match="pool"):
        run_gate({"id": "q01", "kind": "name", "split": "tune", "expect": [{"path": "a.py", "symbol": None}], "rank": 1, "found_in_context": True})


# ---- the command line

def test_the_command_line_merges_a_results_file_with_its_pool_and_prints_the_causes(tmp_path):
    import json
    from eval.gate import main
    good = [{k: v for k, v in question(f"q{i:02d}", 1, True, 1).items() if k != "pool"} for i in range(9)]
    missing = {k: v for k, v in question("q90", None, False, None).items() if k != "pool"}
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"meta": {"repo": "r", "model": "m"}, "questions": good + [missing]}))
    pool = tmp_path / "p.json"
    pool.write_text(json.dumps({"meta": {"repo": "r", "model": "m"}, "questions": [{"id": q["id"], "pool": question(q["id"], 1, True, 1)["pool"] if q["id"] != "q90" else question("q90", None, False, None)["pool"]} for q in good + [missing]]}))
    lines = []
    assert main(["--runs", str(results), "--pools", str(pool)], out=lines.append) == 0
    text = "\n".join(lines)
    assert "9 of 10" in text and "q90" in text and "not retrieved" in text and "tune split only" in text


def test_the_command_line_refuses_a_missing_pool_and_mismatched_file_counts(tmp_path):
    import json
    from eval.gate import main
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"meta": {"repo": "r", "model": "m"}, "questions": [{k: v for k, v in question("q01", 1, True, 1).items() if k != "pool"}]}))
    lines = []
    assert main(["--runs", str(results)], out=lines.append) == 2 and "pool" in lines[0]
    lines.clear()
    assert main(["--runs", str(results), "--pools", str(results), str(results)], out=lines.append) == 2 and "same number" in lines[0]


def test_the_command_line_counts_the_holdout_only_when_asked(tmp_path):
    import json
    from eval.gate import main
    path = tmp_path / "r.json"
    questions = [question("q01", 1, True, 1), question("q03", 1, True, 1, split="holdout")]
    path.write_text(json.dumps({"meta": {"repo": "r", "model": "m"}, "questions": questions}))
    lines = []
    main(["--runs", str(path)], out=lines.append)
    assert "1 of 1 counted" in lines[0] and "holdout hidden" in lines[0]
    lines.clear()
    main(["--runs", str(path), "--include-holdout"], out=lines.append)
    assert "2 of 2 counted" in lines[0] and "including the holdout" in lines[0]
