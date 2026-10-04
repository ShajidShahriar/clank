"""Task I-7.9: the report on the prefix budget rule (eval/budget_report.py; scripts/budget_report.py only calls it).

The rule (devlog 76, written before any number): cost of the prefix rule = tune questions whose answer is in the top 10 but not in the context.
Switch to "squeeze" only if, at a budget of 4000 or more, it recovers at least 3 more questions than the prefix rule; 2 or fewer is a tie, and a tie keeps
the current rule; a gain only at 2000 is not enough. Holdout hidden. Built on the tested replay; these tests pin what the report adds.
"""
import json

from eval.budget_report import decide, main


def e(id, score, path, symbol, cost=100, test=False):
    return {"id": id, "score": score, "path": path, "symbol": symbol, "parent": None, "names": [], "kind": "function", "part": None,
            "is_test": test, "is_changelog": False, "passage_key": id, "cost": cost}


def blocked(id="q01", split="tune", big=900, answer_cost=100, answer_rank=3, kind="name"):
    """Two big passages, then the answer: with a small budget the prefix rule drops the answer, squeeze keeps it."""
    pool = [e("b0", 0.9, "x.py", "x0", cost=big), e("b1", 0.8, "x.py", "x1", cost=big)]
    pool.insert(answer_rank - 1, e("ans", 0.7, "a.py", "f", cost=answer_cost))
    return {"id": id, "kind": kind, "split": split, "question": "how does f work?", "expect": [{"path": "a.py", "symbol": "f"}], "pool": pool,
            "rank": answer_rank, "top1": False, "top3": True, "top10_hit": True, "found_in_context": False}


def save(tmp_path, name, *questions, repo="r", model="m@1", keep=30):
    path = tmp_path / name
    path.write_text(json.dumps({"meta": {"repo": repo, "model": model, "max_tokens": 6000, "k": 10, "keep": keep}, "questions": list(questions)}))
    return str(path)


def call(*argv):
    lines = []
    code = main(list(argv), out=lines.append)
    return code, "\n".join(lines)


def test_decide_switches_only_when_a_budget_of_4000_or_more_recovers_three_or_more_questions():
    assert decide({2000: 5, 4000: 3, 6000: 0})[0] == "squeeze"
    assert decide({2000: 9, 4000: 2, 6000: 2})[0] == "prefix", "two or fewer is a tie"
    assert decide({2000: 9, 4000: 0, 6000: 0})[0] == "prefix", "a gain only at 2000 is not enough"
    assert decide({2000: 0, 4000: 0, 6000: 3})[0] == "squeeze"
    assert decide({})[0] == "prefix"


def test_decide_explains_itself_in_a_sentence():
    assert "4000" in decide({4000: 4})[1] and "tie" in decide({4000: 1})[1]


def test_it_lists_the_questions_whose_answer_was_ranked_but_dropped_by_the_prefix_rule(tmp_path):
    runs = save(tmp_path, "r.json", blocked("q01", answer_cost=100), blocked("q02", answer_cost=100))
    code, text = call("--runs", runs, "--budgets", "1000", "--policies", "none")
    assert code == 0
    assert "q01" in text and "q02" in text and "ranked but dropped" in text


def test_nothing_is_dropped_when_the_budget_is_big_enough(tmp_path):
    runs = save(tmp_path, "r.json", blocked("q01"))
    code, text = call("--runs", runs, "--budgets", "6000", "--policies", "none")
    assert "none @ 6000: none" in text, "the section for this budget says that nothing was dropped"


def test_the_table_has_a_row_per_policy_and_budget_with_both_rules(tmp_path):
    runs = save(tmp_path, "r.json", blocked("q01"))
    code, text = call("--runs", runs, "--budgets", "1000", "6000", "--policies", "none", "demote:0.15")
    rows = [l for l in text.splitlines() if l.startswith(("none", "demote:0.15"))]
    assert len(rows) == 4 and all("prefix" in text and "squeeze" in text for _ in rows)


def test_the_verdict_follows_the_rule(tmp_path):
    many = [blocked(f"q{i:02d}") for i in range(1, 5)]
    code, text = call("--runs", save(tmp_path, "r.json", *many), "--budgets", "1000", "--policies", "none")
    assert "keep the prefix rule" in text, "a gain at 1000 is below 4000: it does not count"
    code, text = call("--runs", save(tmp_path, "r2.json", *many), "--budgets", "4000", "--policies", "none")
    assert "keep the prefix rule" in text, "at 4000 the answers fit anyway (900 + 900 + 100): nothing to recover"
    big = [blocked(f"q{i:02d}", big=2500) for i in range(1, 5)]
    code, text = call("--runs", save(tmp_path, "r3.json", *big), "--budgets", "4000", "--policies", "none")
    assert "switch to squeeze" in text


def test_the_holdout_is_hidden_unless_asked_for(tmp_path):
    runs = save(tmp_path, "r.json", blocked("q01"), blocked("q03", split="holdout"))
    assert "q03" not in call("--runs", runs, "--budgets", "1000", "--policies", "none")[1]
    assert "q03" in call("--runs", runs, "--budgets", "1000", "--policies", "none", "--include-holdout")[1]


def test_a_run_without_a_pool_or_with_a_bad_policy_or_budget_is_refused(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"meta": {"repo": "r", "model": "m", "max_tokens": 6000}, "questions": [{"id": "q01", "expect": [], "split": "tune"}]}))
    assert call("--runs", str(old))[0] == 2 and "--keep" in call("--runs", str(old))[1]
    good = save(tmp_path, "g.json", blocked())
    assert call("--runs", good, "--policies", "shuffle")[0] == 2
    assert call("--runs", good, "--budgets", "0")[0] == 2
    assert call("--runs", good, "--budgets", "abc")[0] == 2


def test_runs_with_different_models_are_refused(tmp_path):
    a, b = save(tmp_path, "a.json", blocked("q01"), model="m@1"), save(tmp_path, "b.json", blocked("q02"), model="m@2")
    code, text = call("--runs", a, b)
    assert code == 2 and "model" in text


def test_a_table_row_carries_the_right_numbers_in_the_right_columns(tmp_path):
    runs = save(tmp_path, "r.json", blocked("q01"), blocked("q02"), blocked("q03", answer_cost=100, answer_rank=1))
    code, text = call("--runs", runs, "--budgets", "1000", "--policies", "none")
    row = next(l for l in text.splitlines() if l.startswith("none"))
    spec, budget, in_top10, kept_prefix, kept_squeeze, dropped, recovered = row.split()
    # q03 has the answer FIRST (always kept); q01 and q02 have two big passages in front: the prefix rule drops them, squeeze keeps them
    assert (spec, budget, in_top10, kept_prefix, kept_squeeze, dropped, recovered) == ("none", "1000", "3", "1", "3", "2", "2")
    assert "n=3" in text


def test_the_dropped_list_names_repo_question_and_rank(tmp_path):
    runs = save(tmp_path, "r.json", blocked("q01"), repo="flask")
    text = call("--runs", runs, "--budgets", "1000", "--policies", "none")[1]
    assert "none @ 1000: flask:q01 (rank 3)" in text


def test_the_top_10_column_counts_answers_beyond_the_top_3_too(tmp_path):
    pool = [e(f"f{i}", 0.9 - i * 0.01, "x.py", f"s{i}", cost=10) for i in range(4)] + [e("ans", 0.5, "a.py", "f", cost=10)]
    deep = {"id": "q01", "kind": "name", "split": "tune", "question": "how does f work?", "expect": [{"path": "a.py", "symbol": "f"}], "pool": pool,
            "rank": 5, "top1": False, "top3": False, "top10_hit": True, "found_in_context": True}
    row = next(l for l in call("--runs", save(tmp_path, "r.json", deep), "--budgets", "1000", "--policies", "none")[1].splitlines() if l.startswith("none"))
    assert row.split()[2] == "1" and row.split()[3] == "1", "the answer is rank 5: in the top 10 and in the context"
