"""Task I-7.8: the relevance-floor analysis on saved runs (eval/floor.py; scripts/floor_report.py only calls it).

Pure functions on hand-made runs. The rule (devlog 74, written before any number was seen):
  W = the lowest raw score of an answer that is in the top 3; N = the highest raw score of any hit of a negative question.
  A gap of at least 0.05 (W - N) allows an absolute floor = (N + W) / 2 rounded DOWN to 0.01, adopted only if it removes no answer that is in the
  top 10 and none that is in the real context. Otherwise no absolute floor. A relative margin can never silence a question. Holdout is hidden.
"""
import pytest

from eval.floor import floor_stats, propose_floor, render, simulate

WANT = [{"path": "a.py", "symbol": "f"}]


def hit(score, path="x.py", symbol="other"):
    return {"id": f"{path}:{symbol}:{score}", "score": score, "path": path, "symbol": symbol, "parent": None, "names": [], "kind": "function", "part": None,
            "is_test": False, "is_changelog": False}


def answer(score):
    return hit(score, "a.py", "f")


def q(id, hits, expect=WANT, kind="name", split="tune", rank=None, found=None):
    if rank is None and expect:
        rank = next((i for i, h in enumerate(hits, 1) if h["path"] == "a.py" and h["symbol"] == "f"), None)
    return {"id": id, "kind": kind, "split": split, "expect": expect, "top10": hits, "rank": rank,
            "found_in_context": (rank is not None) if found is None else found}


def neg(id, hits, split="tune"):
    return q(id, hits, expect=[], kind="negative", split=split)


def run(*questions, repo="r", model="m@1"):
    return {"meta": {"repo": repo, "model": model}, "questions": list(questions)}


# ---- the two numbers and A10

def test_w_is_the_lowest_score_of_an_answer_in_the_top_three_and_n_the_highest_score_of_a_negative():
    r = run(q("q01", [answer(0.80), hit(0.5)]), q("q02", [hit(0.7), answer(0.66), hit(0.5)]), neg("q22", [hit(0.45), hit(0.30)]), neg("q23", [hit(0.52)]))
    s = floor_stats([r])
    assert s.W == pytest.approx(0.66) and s.N == pytest.approx(0.52)
    assert s.gap == pytest.approx(0.14) and s.clear_gap


def test_an_answer_below_the_top_three_is_in_a10_but_not_in_w():
    r = run(q("q01", [answer(0.80)]), q("q02", [hit(0.7), hit(0.69), hit(0.68), answer(0.61)]), neg("q22", [hit(0.3)]))
    s = floor_stats([r])
    assert s.W == pytest.approx(0.80) and s.A10 == pytest.approx(0.61)


def test_the_best_scoring_match_of_a_question_counts_when_several_hits_match():
    r = run(q("q01", [answer(0.70), answer(0.62)]), neg("q22", [hit(0.3)]))
    assert floor_stats([r]).W == pytest.approx(0.70)


def test_a_question_whose_answer_is_not_in_the_top_ten_has_no_score_and_is_listed_as_a_miss():
    r = run(q("q01", [hit(0.7)]), q("q02", [answer(0.7)]), neg("q22", [hit(0.3)]))
    s = floor_stats([r])
    assert s.misses == ["q01"] and s.W == pytest.approx(0.7)


def test_the_holdout_is_left_out_unless_asked_for():
    r = run(q("q01", [answer(0.80)]), q("q03", [answer(0.40)], split="holdout"), neg("q22", [hit(0.3)]), neg("q21", [hit(0.9)], split="holdout"))
    assert floor_stats([r]).W == pytest.approx(0.80) and floor_stats([r]).N == pytest.approx(0.3)
    s = floor_stats([r], include_holdout=True)
    assert s.W == pytest.approx(0.40) and s.N == pytest.approx(0.9)


def test_scores_are_pooled_over_repos_and_each_repo_is_also_reported_on_its_own():
    a = run(q("q01", [answer(0.80)]), neg("q22", [hit(0.34)]), repo="clank")
    b = run(q("q02", [answer(0.70)]), neg("q25", [hit(0.59)]), repo="express")
    s = floor_stats([a, b])
    assert s.W == pytest.approx(0.70) and s.N == pytest.approx(0.59)
    assert s.per_repo["clank"]["N"] == pytest.approx(0.34) and s.per_repo["express"]["W"] == pytest.approx(0.70)


def test_runs_of_different_models_are_refused():
    with pytest.raises(ValueError, match="model"):
        floor_stats([run(q("q01", [answer(0.8)]), model="m@1"), run(q("q02", [answer(0.8)]), model="m@2")])


def test_without_a_negative_or_an_answer_there_is_nothing_to_measure():
    with pytest.raises(ValueError, match="negative"):
        floor_stats([run(q("q01", [answer(0.8)]))])
    with pytest.raises(ValueError, match="answer"):
        floor_stats([run(neg("q22", [hit(0.3)]))])


# ---- the simulation of a floor

def test_an_absolute_floor_removes_answers_below_it_and_silences_negatives_whose_hits_are_all_below():
    r = run(q("q01", [answer(0.80), hit(0.5)]), q("q02", [hit(0.7), answer(0.58)]), neg("q22", [hit(0.45), hit(0.30)]), neg("q23", [hit(0.62), hit(0.4)]))
    sim = simulate([r], min_score=0.60)
    assert sim.answers_lost == ["q02"] and sim.silenced == ["q22"] and sim.not_silenced == ["q23"]


def test_the_average_number_of_hits_kept_counts_the_counted_questions_only():
    r = run(q("q01", [hit(0.8), hit(0.7), hit(0.4)]), q("q02", [hit(0.9), hit(0.3)]), neg("q22", [hit(0.9)]))
    assert simulate([r], min_score=0.5).mean_hits_kept == pytest.approx(1.5)
    assert simulate([r]).mean_hits_kept == pytest.approx(2.5), "no floor, no margin: nothing is removed"


def test_a_relative_margin_is_measured_from_the_best_raw_score_and_can_never_silence_a_question():
    r = run(q("q01", [hit(0.80), answer(0.74), hit(0.60)]), q("q02", [answer(0.70), hit(0.5)]), neg("q22", [hit(0.30), hit(0.29)]))
    sim = simulate([r], margin=0.06)
    assert sim.answers_lost == [] and sim.silenced == [] and sim.mean_hits_kept == pytest.approx(1.5)
    assert simulate([r], margin=0.02).answers_lost == ["q01"], "0.74 is more than 0.02 below the best score 0.80"


def test_the_best_raw_score_is_used_even_when_the_demotion_put_another_hit_first():
    r = run(q("q01", [hit(0.60), hit(0.80), answer(0.58)]))
    assert simulate([r], margin=0.10).answers_lost == ["q01"], "the demoted order does not matter: 0.80 is the best raw score"


def test_min_score_and_margin_can_be_used_together():
    r = run(q("q01", [hit(0.9), answer(0.78), hit(0.5)]))
    assert simulate([r], min_score=0.6, margin=0.2).mean_hits_kept == pytest.approx(2.0)
    assert simulate([r], min_score=0.8, margin=0.2).answers_lost == ["q01"]


def test_a_real_context_answer_that_the_floor_would_remove_is_reported_separately():
    r = run(q("q01", [answer(0.55)], found=True), q("q02", [answer(0.55)], found=False))
    sim = simulate([r], min_score=0.6)
    assert sim.answers_lost == ["q01", "q02"] and sim.context_lost == ["q01"]


# ---- the rule

def test_a_clear_gap_proposes_the_midpoint_rounded_down_to_a_hundredth():
    r = run(q("q01", [answer(0.70)]), neg("q22", [hit(0.371)]))
    s = floor_stats([r])
    floor, why = propose_floor(s, [r])
    assert floor == pytest.approx(0.53) and "adopt" in why        # (0.371 + 0.70) / 2 = 0.5355 -> 0.53


def test_a_gap_under_005_proposes_nothing():
    r = run(q("q01", [answer(0.64)]), neg("q22", [hit(0.60)]))
    floor, why = propose_floor(floor_stats([r]), [r])
    assert floor is None and "0.05" in why


def test_overlap_proposes_nothing():
    r = run(q("q01", [answer(0.60)]), neg("q22", [hit(0.65)]))
    assert propose_floor(floor_stats([r]), [r])[0] is None


def test_a_floor_that_would_remove_an_answer_from_the_top_ten_is_not_adopted():
    r = run(q("q01", [answer(0.80)]), q("q02", [hit(0.7), hit(0.69), hit(0.68), answer(0.45)]), neg("q22", [hit(0.30)]))
    floor, why = propose_floor(floor_stats([r]), [r])        # W = 0.80, N = 0.30: midpoint 0.55, but q02's answer (top 10) is 0.45
    assert floor is None and "q02" in why


def test_a_floor_that_would_remove_an_answer_that_is_in_the_real_context_is_not_adopted():
    r = run(q("q01", [answer(0.80)]), q("q02", [hit(0.7), hit(0.69), hit(0.68), answer(0.50)], found=True), neg("q22", [hit(0.30)]))
    assert propose_floor(floor_stats([r]), [r])[0] is None


def test_the_midpoint_is_never_below_the_negatives_best_score():
    r = run(q("q01", [answer(0.90)]), neg("q22", [hit(0.55)]))
    floor, _ = propose_floor(floor_stats([r]), [r])
    assert floor is not None and floor > 0.55 and floor <= 0.90


# ---- the printed report

def test_the_report_shows_the_numbers_the_proposal_and_never_a_holdout_question():
    r = run(q("q01", [answer(0.70)]), q("q03", [answer(0.123)], split="holdout"), neg("q22", [hit(0.371)]), neg("q21", [hit(0.987)], split="holdout"))
    text = render([r])
    for word in ("W", "N", "gap", "floor", "q22", "per repo"):
        assert word in text
    for hidden in ("q03", "q21", "0.123", "0.987"):
        assert hidden not in text
    assert "q03" in render([r], include_holdout=True) and "0.987" in render([r], include_holdout=True)


# ---- boundaries and the command line

def test_a_hit_exactly_at_the_floor_is_kept():
    r = run(q("q01", [answer(0.60), hit(0.5)]))
    assert simulate([r], min_score=0.60).answers_lost == [] and simulate([r], min_score=0.60).mean_hits_kept == pytest.approx(1.0)
    assert simulate([r], min_score=0.601).answers_lost == ["q01"]


def test_the_command_line_prints_the_report_and_refuses_what_it_cannot_measure(tmp_path):
    import json
    from eval.floor import main
    good = tmp_path / "good.json"
    good.write_text(json.dumps(run(q("q01", [answer(0.70)]), neg("q22", [hit(0.371)]))))
    lines = []
    assert main(["--runs", str(good)], out=lines.append) == 0 and "gap W - N" in lines[0]
    lines.clear()
    only_answers = tmp_path / "only.json"
    only_answers.write_text(json.dumps(run(q("q01", [answer(0.70)]))))
    assert main(["--runs", str(only_answers)], out=lines.append) == 2 and "negative" in lines[0]
    lines.clear()
    final = tmp_path / "final.json"
    final.write_text(json.dumps({**run(q("q01", [answer(0.70)]), neg("q22", [hit(0.371)])), "meta": {"repo": "r", "model": "m@1", "final": True}}))
    lines.clear()
    holdout_flag = main(["--runs", str(final), "--include-holdout"], out=lines.append)
    assert holdout_flag == 0 and "including the holdout" in lines[0]


def test_an_answer_at_rank_three_counts_for_w_but_one_at_rank_four_does_not():
    third = run(q("q01", [hit(0.8), hit(0.79), answer(0.61)]), neg("q22", [hit(0.3)]))
    assert floor_stats([third]).W == pytest.approx(0.61)
    fourth = run(q("q01", [answer(0.9)]), q("q02", [hit(0.8), hit(0.79), hit(0.78), answer(0.61)]), neg("q22", [hit(0.3)]))
    assert floor_stats([fourth]).W == pytest.approx(0.9)


def test_negatives_do_not_count_in_the_mean_number_of_hits_kept():
    r = run(q("q01", [hit(0.9), hit(0.8)]), neg("q22", [hit(0.9), hit(0.8), hit(0.7), hit(0.6), hit(0.5), hit(0.4)]))
    assert simulate([r]).mean_hits_kept == pytest.approx(2.0)
