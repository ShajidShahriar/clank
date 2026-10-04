"""Task I-7.13: the write-up generator (eval/final_report.py). The numbers in the committed results come FROM the result files, not from anyone's typing:
a summary per stage, a 20-row rank grid with every miss labelled by cause, and the timings. Hand-made runs here; the real files in the real run.
"""
import json

from eval.final_report import REPOS, grid_rows, main, render_grid, render_report, summarize_stage, timing_lines


def hit(path="x.py", symbol="other", score=0.5):
    return {"id": f"{path}:{symbol}", "score": score, "path": path, "symbol": symbol, "parent": None, "names": [], "kind": "function", "part": None,
            "is_test": False, "is_changelog": False}


def q(id, rank, found, split="tune", kind="name", expect=True, ms=40.0, pool_position=None):
    pool = [hit("x.py", f"s{i}") for i in range(30)]
    if pool_position:
        pool[pool_position - 1] = hit("a.py", "f")
    return {"id": id, "kind": kind if expect else "negative", "split": split, "expect": [{"path": "a.py", "symbol": "f"}] if expect else [], "rank": rank,
            "top1": rank == 1, "top3": bool(rank and rank <= 3), "top10_hit": bool(rank), "found_in_context": found, "top10": pool[:10], "pool": pool,
            "timings_ms": {"total": ms}}


def run(*questions, repo="clank", index_seconds=None, final=False):
    meta = {"repo": repo, "model": "m@1", "final": final}
    out = {"meta": meta, "questions": list(questions)}
    if index_seconds is not None:
        out["index"] = {"seconds": index_seconds, "chunks": 100, "embedded": 100, "embed_calls": 10, "files_seen": 9}
    return out


# ---- the stage summary

def test_a_stage_summary_counts_top_1_3_10_and_the_real_context_per_split():
    r = run(q("q01", 1, True), q("q02", 3, False), q("q03", 8, True, split="holdout"), q("q04", None, False, split="holdout"), q("q22", None, False, expect=False))
    tune, holdout = summarize_stage([r], "tune"), summarize_stage([r], "holdout")
    assert tune == {"n": 2, "top1": 1, "top3": 2, "top10": 2, "context": 1}
    assert holdout == {"n": 2, "top1": 0, "top3": 0, "top10": 1, "context": 1}
    assert summarize_stage([r], None)["n"] == 4, "no split means every counted question"


def test_a_stage_summary_adds_the_repos_together():
    a, b = run(q("q01", 1, True), repo="clank"), run(q("q02", 2, True), q("q05", None, False), repo="flask")
    assert summarize_stage([a, b], None) == {"n": 3, "top1": 1, "top3": 2, "top10": 2, "context": 2}


# ---- the rank grid

def stages(baseline, demoted, final_on, final_off, pools=None):
    return {"baseline": [baseline], "demotion": [demoted], "final_on": [final_on], "final_off": [final_off], "pools": [pools or final_on]}


def test_the_grid_has_one_row_per_counted_question_in_repo_then_id_order_with_a_rank_at_every_stage():
    base = run(q("q06", 9, True, split="holdout"), q("q01", None, False))
    mid = run(q("q06", 9, True, split="holdout"), q("q01", None, False))
    on = run(q("q06", 3, True, split="holdout"), q("q01", None, False, pool_position=None), q("q22", None, False, expect=False))
    off = run(q("q06", 9, True, split="holdout"), q("q01", None, False))
    rows = grid_rows(**stages(base, mid, on, off))
    assert [r["id"] for r in rows] == ["q01", "q06"], "negatives are not in the grid"
    row = next(r for r in rows if r["id"] == "q06")
    assert (row["baseline"], row["demotion"], row["final_on"], row["final_off"], row["split"], row["kind"], row["repo"]) == (9, 9, 3, 9, "holdout", "name", "clank")


def test_every_miss_is_labelled_by_cause_from_the_pool_and_a_found_question_has_no_cause():
    base = run(q("q01", None, False), q("q02", 4, False), q("q03", 1, True), q("q04", None, False))
    on = run(q("q01", None, False), q("q02", 4, False, pool_position=8), q("q03", 1, True), q("q04", None, False, pool_position=25))
    rows = {r["id"]: r for r in grid_rows(**stages(base, base, on, base))}
    assert rows["q01"]["cause"] == "not retrieved"
    assert rows["q02"]["cause"] == "ranked 4-10, not in context"
    assert rows["q03"]["cause"] is None
    assert rows["q04"]["cause"] == "ranked 11-30"


def test_a_question_missing_from_a_stage_shows_a_dash_not_a_made_up_rank():
    base = run(q("q01", 2, True))
    on = run(q("q01", 1, True), q("q02", 1, True))                       # q02 did not exist in the baseline file
    rows = {r["id"]: r for r in grid_rows(**stages(base, base, on, on))}
    assert rows["q02"]["baseline"] == "-" and rows["q01"]["baseline"] == 2


def test_the_rendered_grid_is_twenty_rows_when_there_are_twenty_questions():
    qs = [q(f"q{i:02d}", 1, True) for i in range(1, 21)]
    text = render_grid(grid_rows(**stages(run(*qs), run(*qs), run(*qs), run(*qs))))
    body = [line for line in text.splitlines() if line.startswith("q")]
    assert len(body) == 20
    assert "baseline" in text and "final" in text and "cause" in text


def test_a_rank_of_none_is_printed_as_miss_and_a_found_in_context_answer_with_a_star():
    base = run(q("q01", None, False), q("q02", 2, True))
    text = render_grid(grid_rows(**stages(base, base, base, base)))
    line1 = next(l for l in text.splitlines() if l.startswith("q01"))
    line2 = next(l for l in text.splitlines() if l.startswith("q02"))
    assert "miss" in line1 and "2*" in line2


# ---- timings

def test_the_timings_report_the_cold_index_the_median_query_and_the_slowest():
    base = run(q("q01", 1, True, ms=30.0), repo="clank", index_seconds=101.5)
    final = run(q("q01", 1, True, ms=30.0), q("q02", 1, True, ms=50.0), q("q03", 1, True, ms=70.0), repo="clank", final=True)
    lines = "\n".join(timing_lines([base], [final]))
    assert "clank" in lines and "101.5" in lines and "50.0" in lines and "70.0" in lines


# ---- the whole report and the command line

def test_the_report_names_the_stages_and_the_holdout_rule_result():
    base = run(q("q01", None, False), q("q06", 9, True, split="holdout"))
    on = run(q("q01", 1, True), q("q06", 3, True, split="holdout"), final=True)
    off = run(q("q01", 1, True), q("q06", 9, True, split="holdout"), final=True)
    text = render_report(baseline=[base], demotion=[base], final_on=[on], final_off=[off], pools=[on])
    for word in ("baseline", "final", "demotion", "holdout", "top-3", "does not look bad"):
        assert word in text


def test_the_report_says_it_looks_bad_when_the_demotion_loses_three_questions_in_the_top_three():
    qs_on = [q(f"q{i:02d}", 5, False, split="holdout") for i in range(3, 9)]
    qs_off = [q(f"q{i:02d}", 1, True, split="holdout") for i in range(3, 9)]
    text = render_report(baseline=[run(*qs_off)], demotion=[run(*qs_off)], final_on=[run(*qs_on, final=True)], final_off=[run(*qs_off, final=True)], pools=[run(*qs_on)])
    assert "LOOKS BAD" in text and "limitation" in text


def test_the_command_line_reads_the_files_and_writes_the_text(tmp_path):
    results, final = tmp_path / "results", tmp_path / "final"
    results.mkdir(), final.mkdir()
    for repo in REPOS:
        for name in (f"{repo}-baseline.json", f"{repo}-policy-on.json"):
            (results / name).write_text(json.dumps(run(q("q01", 2, True), repo=repo, index_seconds=100.0)))
        (results / f"{repo}-pool.json").write_text(json.dumps(run(q("q01", 2, True, pool_position=2), repo=repo)))
        for tag in ("on", "off"):
            (final / f"{repo}-final-{tag}.json").write_text(json.dumps(run(q("q01", 1, True), repo=repo, final=True)))
    out = tmp_path / "RESULTS.txt"
    lines = []
    assert main(["--results", str(results), "--final", str(final), "--out", str(out)], out=lines.append) == 0
    text = out.read_text()
    assert "baseline" in text and "clank" in text and out.exists()


def test_the_command_line_refuses_files_that_were_not_final_runs(tmp_path):
    results, final = tmp_path / "results", tmp_path / "final"
    results.mkdir(), final.mkdir()
    for repo in REPOS:
        for name in (f"{repo}-baseline.json", f"{repo}-policy-on.json", f"{repo}-pool.json"):
            (results / name).write_text(json.dumps(run(q("q01", 2, True), repo=repo)))
        for tag in ("on", "off"):
            (final / f"{repo}-final-{tag}.json").write_text(json.dumps(run(q("q01", 1, True), repo=repo, final=False)))
    lines = []
    assert main(["--results", str(results), "--final", str(final), "--out", str(tmp_path / "o.txt")], out=lines.append) == 2
    assert "--final" in lines[0] and not (tmp_path / "o.txt").exists()


def test_a_pool_file_can_be_named_per_repo(tmp_path):
    results, final = tmp_path / "results", tmp_path / "final"
    results.mkdir(), final.mkdir()
    for repo in REPOS:
        for name in (f"{repo}-baseline.json", f"{repo}-policy-on.json", f"{repo}-pool.json"):
            (results / name).write_text(json.dumps(run(q("q01", 2, False), repo=repo)))
        for tag in ("on", "off"):
            (final / f"{repo}-final-{tag}.json").write_text(json.dumps(run(q("q01", 4, False), repo=repo, final=True)))
    (results / "flask-special.json").write_text(json.dumps(run(q("q01", 4, False, pool_position=6), repo="flask")))
    out = tmp_path / "RESULTS.txt"
    assert main(["--results", str(results), "--final", str(final), "--out", str(out), "--pool", "flask=flask-special.json"], out=lambda s: None) == 0
    flask_line = next(l for l in out.read_text().splitlines() if l.startswith("q01") and "flask" in l)
    assert "ranked 4-10, not in context" in flask_line, "flask used the named pool file; the others had no answer in theirs"
    clank_line = next(l for l in out.read_text().splitlines() if l.startswith("q01") and "clank" in l)
    assert "not retrieved" in clank_line


# ---- boundaries and the details the first tests did not pin

def test_a_rank_of_four_is_in_the_top_ten_but_not_in_the_top_three():
    s = summarize_stage([run(q("q01", 3, True), q("q02", 4, False), q("q03", 10, False), q("q04", None, False))], None)
    assert (s["top3"], s["top10"]) == (1, 3)


def test_a_question_whose_pool_was_not_saved_says_so_instead_of_inventing_a_cause():
    on = run(q("q01", None, False), q("q02", None, False))
    pools = run(q("q01", None, False))                                    # no pool entry for q02
    rows = {r["id"]: r for r in grid_rows(**stages(on, on, on, on, pools=pools))}
    assert rows["q01"]["cause"] == "not retrieved" and rows["q02"]["cause"] == "no pool saved"


def test_the_grid_lists_clank_then_flask_then_express_whatever_order_the_files_come_in():
    flask, express, clank = run(q("q01", 1, True), repo="flask"), run(q("q01", 1, True), repo="express"), run(q("q01", 1, True), repo="clank")
    rows = grid_rows(baseline=[flask, express, clank], demotion=[flask, express, clank], final_on=[express, flask, clank], final_off=[flask, express, clank],
                     pools=[flask, express, clank])
    assert [r["repo"] for r in rows] == ["clank", "flask", "express"]


def test_a_stage_that_never_asked_a_question_prints_a_dash_in_its_cell():
    base = run(q("q01", 2, True))
    on = run(q("q01", 1, True), q("q02", 1, True))
    line = next(l for l in render_grid(grid_rows(**stages(base, base, on, on))).splitlines() if l.startswith("q02"))
    assert line.split()[4] == "-" and line.split()[7] == "1*", "baseline cell is a dash, the final-on cell is a rank"


def test_the_median_not_the_mean_is_reported():
    final = run(q("q01", 1, True, ms=30.0), q("q02", 1, True, ms=40.0), q("q03", 1, True, ms=200.0), repo="clank", final=True)
    text = "\n".join(timing_lines([run(q("q01", 1, True), repo="clank", index_seconds=1.0)], [final]))
    assert "median 40.0" in text and "90.0" not in text


def holdout_report(on_top3, off_top3=6, tests_on=None):
    """six holdout questions: `on_top3` of them in the top 3 with the demotion on, `off_top3` with it off."""
    mk = lambda hits, kind_for=None: run(*[q(f"q{i:02d}", 1 if i < hits else 8, i < hits, split="holdout", kind="name") for i in range(6)], final=True)
    on, off = mk(on_top3), mk(off_top3)
    if tests_on is not None:
        for run_, rank in ((on, tests_on), (off, 1)):
            run_["questions"][0]["kind"], run_["questions"][0]["rank"] = "tests", rank
    return render_report(baseline=[off], demotion=[off], final_on=[on], final_off=[off], pools=[on])


def test_losing_two_top_three_hits_is_noise_and_losing_three_looks_bad():
    assert "does not look bad" in holdout_report(on_top3=4) and "LOOKS BAD" not in holdout_report(on_top3=4)
    assert "LOOKS BAD" in holdout_report(on_top3=3) and "limitation" in holdout_report(on_top3=3)
    assert "does not look bad" in holdout_report(on_top3=6)


def test_ranking_a_tests_question_lower_looks_bad_even_when_the_totals_are_fine():
    text = holdout_report(on_top3=6, tests_on=5)
    verdict = next(line for line in text.splitlines() if line.startswith("verdict:"))
    assert "LOOKS BAD" in verdict and "q00" in verdict, "the verdict line itself names the tests question"
    assert "does not look bad" in holdout_report(on_top3=6, tests_on=1)


import pytest


@pytest.mark.parametrize("on_final, off_final", [(True, False), (False, True)])
def test_one_final_file_set_that_is_not_final_is_enough_to_refuse(tmp_path, on_final, off_final):
    results, final = tmp_path / "results", tmp_path / "final"
    results.mkdir(), final.mkdir()
    for repo in REPOS:
        for name in (f"{repo}-baseline.json", f"{repo}-policy-on.json", f"{repo}-pool.json"):
            (results / name).write_text(json.dumps(run(q("q01", 2, True), repo=repo)))
        (final / f"{repo}-final-on.json").write_text(json.dumps(run(q("q01", 1, True), repo=repo, final=on_final)))
        (final / f"{repo}-final-off.json").write_text(json.dumps(run(q("q01", 1, True), repo=repo, final=off_final)))
    lines = []
    assert main(["--results", str(results), "--final", str(final), "--out", str(tmp_path / "o.txt")], out=lines.append) == 2 and "--final" in lines[0]
