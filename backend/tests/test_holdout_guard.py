"""Task I-7.13: the holdout is looked at ONCE, with the final configuration. The runner prints holdout numbers only with `--final` and writes
`meta.final = true` into the file; every other tool refuses `--include-holdout` for a run that was not made that way. Every results file made before
the final run contains holdout answers, so without this guard a tool could show them early.
"""
import json

import pytest

from eval.holdout import require_final

import eval.budget_report as budget_report
import eval.compare as compare
import eval.floor as floor
import eval.gate as gate
import eval.replay_report as replay_report


def hit(score, path="x.py", symbol="other"):
    return {"id": f"{path}:{symbol}", "score": score, "path": path, "symbol": symbol, "parent": None, "names": [], "kind": "function", "part": None,
            "is_test": False, "is_changelog": False, "passage_key": f"{path}:{symbol}", "cost": 50}


def question(id, split, expect=True):
    answer = hit(0.8, "a.py", "f")
    pool = [answer] + [hit(0.7 - i * 0.01, "x.py", f"s{i}") for i in range(29)]
    return {"id": id, "kind": "name" if expect else "negative", "split": split, "question": "how does f work?",
            "expect": [{"path": "a.py", "symbol": "f"}] if expect else [], "rank": 1 if expect else None, "top1": expect, "top3": expect, "top10_hit": expect,
            "found_in_context": expect, "top10": pool[:10], "pool": pool}


def write(tmp_path, name, final, repo="r"):
    qs = [question("q01", "tune"), question("q03", "holdout"), question("q22", "tune", expect=False), question("q21", "holdout", expect=False)]
    meta = {"repo": repo, "model": "m@1", "max_tokens": 6000, "k": 10, "keep": 30}
    if final is not None:
        meta["final"] = final
    path = tmp_path / name
    path.write_text(json.dumps({"meta": meta, "questions": qs}))
    return str(path)


def tool_calls(tmp_path, final):
    a, b = write(tmp_path, "a.json", final), write(tmp_path, "b.json", final)
    return {
        "compare": lambda *extra: compare.main(["--a", a, "--b", b, *extra], out=lambda s: None),
        "replay_report": lambda *extra: replay_report.main(["--runs", a, "--policies", "none", *extra], out=lambda s: None),
        "budget_report": lambda *extra: budget_report.main(["--runs", a, "--budgets", "1000", "--policies", "none", *extra], out=lambda s: None),
        "floor": lambda *extra: floor.main(["--runs", a, *extra], out=lambda s: None),
        "gate": lambda *extra: gate.main(["--runs", a, *extra], out=lambda s: None),
    }


# ---- the helper

def test_final_runs_pass_and_anything_else_is_refused():
    require_final([{"meta": {"repo": "a", "final": True}}, {"meta": {"repo": "b", "final": True}}])
    for meta in ({"repo": "a"}, {"repo": "a", "final": False}, {"repo": "a", "final": "yes"}, {"repo": "a", "final": 1}):
        with pytest.raises(ValueError, match="--final"):
            require_final([{"meta": meta}])


def test_the_refusal_names_the_runs_that_were_not_final():
    with pytest.raises(ValueError, match="flask"):
        require_final([{"meta": {"repo": "clank", "final": True}}, {"meta": {"repo": "flask"}}])


def test_nothing_to_check_is_refused_not_waved_through():
    with pytest.raises(ValueError, match="no runs"):
        require_final([])


# ---- every tool

@pytest.mark.parametrize("tool", ["compare", "replay_report", "budget_report", "floor", "gate"])
def test_a_tool_refuses_the_holdout_for_runs_that_were_not_made_with_final(tmp_path, tool):
    calls = tool_calls(tmp_path, final=None)
    assert calls[tool]() == 0, "without the flag the tools work as before (tune only)"
    assert calls[tool]("--include-holdout") == 2


@pytest.mark.parametrize("tool", ["compare", "replay_report", "budget_report", "floor", "gate"])
def test_a_tool_shows_the_holdout_for_runs_made_with_final(tmp_path, tool):
    calls = tool_calls(tmp_path, final=True)
    assert calls[tool]("--include-holdout") == 0


@pytest.mark.parametrize("tool", ["compare", "replay_report", "budget_report", "floor", "gate"])
def test_a_run_marked_final_false_is_refused_too(tmp_path, tool):
    assert tool_calls(tmp_path, final=False)[tool]("--include-holdout") == 2


def test_the_refusal_message_says_why_and_how(tmp_path):
    lines = []
    a, b = write(tmp_path, "a.json", None), write(tmp_path, "b.json", None)
    assert compare.main(["--a", a, "--b", b, "--include-holdout"], out=lines.append) == 2
    assert "--final" in lines[0] and "holdout" in lines[0]


def test_one_run_that_is_not_final_among_final_ones_is_enough_to_refuse(tmp_path):
    final_a, final_b = write(tmp_path, "fa.json", True, repo="r1"), write(tmp_path, "fb.json", True, repo="r1")
    plain_a, plain_b = write(tmp_path, "pa.json", None, repo="r2"), write(tmp_path, "pb.json", None, repo="r2")
    quiet = lambda s: None
    assert compare.main(["--a", final_a, "--b", plain_b, "--include-holdout"], out=quiet) == 2, "the SECOND file of a pair counts too"
    assert compare.main(["--a", plain_a, "--b", final_b, "--include-holdout"], out=quiet) == 2
    assert compare.main(["--a", final_a, "--b", final_b, "--include-holdout"], out=quiet) == 0
    for name, main, extra in (("replay_report", replay_report.main, ["--policies", "none"]), ("budget_report", budget_report.main, ["--budgets", "1000", "--policies", "none"]),
                              ("floor", floor.main, []), ("gate", gate.main, [])):
        assert main(["--runs", final_a, plain_a, *extra, "--include-holdout"], out=quiet) == 2, name
        assert main(["--runs", plain_a, final_a, *extra, "--include-holdout"], out=quiet) == 2, name
