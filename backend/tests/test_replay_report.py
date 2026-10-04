"""Task I-7.6, step 1: the report that lays the policies side by side (eval/replay_report.py; scripts/replay_policies.py only calls it).
It uses the already tested replay and comparison code, so these tests only pin what the REPORT adds: the estimate check, the tests-kind guard,
the hidden holdout and the refusals."""
import json

from eval.replay_report import main


def e(id, score, path, symbol=None, test=False, log=False, cost=100):
    return {"id": id, "score": score, "path": path, "symbol": symbol, "parent": None, "names": [], "kind": "function", "part": None,
            "is_test": test, "is_changelog": log, "passage_key": id, "cost": cost}


def q(id, pool, expect, kind="name", split="tune", text="how does f work?", real_found=True):
    return {"id": id, "kind": kind, "split": split, "question": text, "expect": [{"path": p, "symbol": s} for p, s in expect], "pool": pool,
            "rank": 1, "top1": True, "top3": True, "top10_hit": True, "found_in_context": real_found}


def save(tmp_path, name, *questions, repo="r", model="m@1", keep=30):
    path = tmp_path / name
    path.write_text(json.dumps({"meta": {"repo": repo, "model": model, "max_tokens": 6000, "k": 10, "keep": keep}, "questions": list(questions)}))
    return str(path)


def call(*argv):
    lines = []
    code = main(list(argv), out=lines.append)
    return code, "\n".join(lines)


def crowded(id="q01", split="tune", kind="name", text="how does f work?"):
    """Three tests (0.70, 0.69, 0.68) ahead of the answer in code (0.66), so it is rank 4: a demotion of 0.06 lifts it to rank 1, into the top 3."""
    pool = [e(f"t{i}", 0.70 - i * 0.01, "tests/t.py", f"test_{i}", test=True) for i in range(3)] + [e("ans", 0.66, "a.py", "f")]
    return q(id, pool, [("a.py", "f")], kind=kind, split=split, text=text)


def test_it_prints_a_row_per_policy_with_the_counts_and_the_verdict_against_none(tmp_path):
    f = save(tmp_path, "r.json", crowded("q01"), crowded("q02"), crowded("q04"), crowded("q05"))
    code, text = call("--runs", f, "--policies", "none", "demote:0.06", "demote:0.02")
    assert code == 0
    lines = {l.split()[0]: l for l in text.splitlines() if l.startswith(("none", "demote"))}
    assert set(lines) == {"none", "demote:0.06", "demote:0.02"}
    assert "better" in text and "tie" in text


def test_a_policy_that_helps_on_three_or_more_questions_is_called_better_and_two_is_a_tie(tmp_path):
    three = save(tmp_path, "three.json", crowded("q01"), crowded("q02"), crowded("q04"))
    two = save(tmp_path, "two.json", crowded("q01"), crowded("q02"))
    assert "demote:0.06 is better" in call("--runs", three, "--policies", "demote:0.06")[1]
    assert "better" not in call("--runs", two, "--policies", "demote:0.06")[1].split("verdict")[-1]


def test_the_holdout_questions_are_not_in_the_numbers_unless_asked_for(tmp_path):
    f = save(tmp_path, "r.json", crowded("q01"), crowded("q03", split="holdout"))
    assert "n=1" in call("--runs", f, "--policies", "none")[1]
    assert "q03" not in call("--runs", f, "--policies", "none", "--show", "demote:0.06")[1]
    assert "n=2" in call("--runs", f, "--policies", "none", "--include-holdout")[1]


def test_it_warns_when_a_policy_makes_a_tests_question_worse(tmp_path):
    hurt = q("q16", [e("t", 0.70, "tests/t.py", "test_f", test=True), e("c", 0.66, "a.py", "f")], [("tests/t.py", "test_f")], kind="tests", text="how is f checked?")
    f = save(tmp_path, "r.json", hurt)
    code, text = call("--runs", f, "--policies", "demote:0.06")
    assert "HURTS tests question q16" in text
    safe = q("q16", [e("t", 0.70, "tests/t.py", "test_f", test=True), e("c", 0.66, "a.py", "f")], [("tests/t.py", "test_f")], kind="tests", text="where is f tested?")
    assert "HURTS" not in call("--runs", save(tmp_path, "s.json", safe), "--policies", "demote:0.06")[1]


def test_it_reports_how_well_the_context_estimate_agrees_with_the_real_run(tmp_path):
    ok = crowded("q01")
    bad = crowded("q02")
    bad["found_in_context"] = False        # the real run says no, the estimate (budget is huge) says yes
    f = save(tmp_path, "r.json", ok, bad)
    code, text = call("--runs", f, "--policies", "none")
    assert "estimate agrees with the real run on 1 of 2" in text and "q02" in text


def test_the_ids_of_disagreeing_holdout_questions_are_not_printed(tmp_path):
    bad = crowded("q03", split="holdout")
    bad["found_in_context"] = False
    code, text = call("--runs", save(tmp_path, "r.json", crowded("q01"), bad), "--policies", "none")
    assert "estimate agrees with the real run on 1 of 2" in text and "q03" not in text


def test_show_prints_the_per_question_ranks_for_one_policy(tmp_path):
    f = save(tmp_path, "r.json", crowded("q01"))
    text = call("--runs", f, "--policies", "none", "--show", "demote:0.06")[1]
    assert "q01" in text and "better" in text


def test_a_run_without_a_pool_is_refused(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"meta": {"repo": "r", "model": "m", "max_tokens": 6000}, "questions": [{"id": "q01", "expect": [], "split": "tune"}]}))
    code, text = call("--runs", str(path), "--policies", "none")
    assert code == 2 and "--keep" in text


def test_a_bad_policy_is_refused_before_anything_is_printed(tmp_path):
    code, text = call("--runs", save(tmp_path, "r.json", crowded()), "--policies", "none", "shuffle")
    assert code == 2 and "shuffle" in text
    assert "estimate agrees" not in text and "policies on saved pools" not in text, "nothing is printed before the refusal"


def test_several_repos_are_added_together(tmp_path):
    a = save(tmp_path, "a.json", crowded("q01"), repo="clank")
    b = save(tmp_path, "b.json", crowded("q02"), repo="flask")
    code, text = call("--runs", a, b, "--policies", "none")
    assert "n=2" in text


def test_runs_made_with_different_models_are_refused(tmp_path):
    a = save(tmp_path, "a.json", crowded("q01"), model="m@1")
    b = save(tmp_path, "b.json", crowded("q02"), model="m@2")
    code, text = call("--runs", a, b, "--policies", "none")
    assert code == 2 and "model" in text
