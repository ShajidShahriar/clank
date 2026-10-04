"""Task I-7.6, step 1: replay ranking policies OFFLINE on a saved pool (no embedding, no pipeline code involved).

A policy only reorders what was stored, so the pool must be wider than the top 10. The replay reports, per question, the rank under a policy,
and an ESTIMATE of "found in context" using the real budget rule (passages in order, a prefix that fits, the best one always kept). The estimate is
only trustworthy if it reproduces the REAL recorded answers when no policy is applied: a test pins that.
"""
import copy

import pytest

from eval.replay import make_policy, replay_run, simulate_context


def e(id, score, path, symbol=None, test=False, log=False, key=None, cost=100, names=(), parent=None):
    return {"id": id, "score": score, "path": path, "symbol": symbol, "parent": parent, "names": list(names), "kind": "function", "part": None,
            "is_test": test, "is_changelog": log, "passage_key": key or id, "cost": cost}


def question(pool, expect=(("a.py", "f"),), id="q01", kind="name", split="tune", text="how does login work?"):
    return {"id": id, "kind": kind, "split": split, "question": text, "expect": [{"path": p, "symbol": s} for p, s in expect], "pool": pool,
            "rank": None, "top1": False, "top3": False, "top10_hit": False, "found_in_context": False}


def run(*questions, model="m@1"):
    return {"meta": {"repo": "r", "model": model, "max_tokens": 6000, "k": 10, "keep": 30}, "questions": list(questions)}


def ranks(replayed):
    return {q["id"]: q["rank"] for q in replayed["questions"]}


# ---- policies

def test_none_keeps_the_saved_order_and_ranks_are_taken_from_the_first_ten():
    pool = [e(f"n{i}", 0.9 - i * 0.01, "x.py", f"s{i}") for i in range(11)] + [e("ans", 0.5, "a.py", "f")]
    pool.insert(3, e("ans", 0.8, "a.py", "f"))
    assert ranks(replay_run(run(question(pool)), "none")) == {"q01": 4}
    far = [e(f"n{i}", 0.9 - i * 0.01, "x.py", f"s{i}") for i in range(10)] + [e("ans", 0.5, "a.py", "f")]
    assert ranks(replay_run(run(question(far)), "none")) == {"q01": None}, "rank 11 is a miss for top-10"


def test_hide_removes_tests_and_changelogs_and_backfills_from_beyond_the_top_ten():
    pool = [e(f"t{i}", 0.9 - i * 0.01, "tests/t.py", f"t{i}", test=True) for i in range(9)] + [e("ans", 0.7, "a.py", "f"), e("code", 0.69, "c.py", "g")]
    assert ranks(replay_run(run(question(pool)), "none")) == {"q01": 10}
    assert ranks(replay_run(run(question(pool)), "hide")) == {"q01": 1}


def test_hide_gated_keeps_tests_when_the_question_asks_for_them():
    pool = [e("t", 0.8, "tests/t.py", "test_f", test=True), e("c", 0.7, "a.py", "f")]
    asked = question(pool, expect=(("tests/t.py", "test_f"),), text="where is f tested?")
    assert ranks(replay_run(run(asked), "hide_gated")) == {"q01": 1}
    plain = question(pool, expect=(("tests/t.py", "test_f"),), text="how does f work?")
    assert ranks(replay_run(run(plain), "hide_gated")) == {"q01": None}
    assert ranks(replay_run(run(plain), "hide")) == {"q01": None}


def test_multiply_scales_only_tests_and_changelogs_and_reorders():
    pool = [e("t", 0.70, "tests/t.py", "t", test=True), e("ans", 0.66, "a.py", "f")]
    assert ranks(replay_run(run(question(pool)), "multiply:0.9")) == {"q01": 1}        # 0.63 < 0.66
    pool2 = [e("t", 0.70, "tests/t.py", "t", test=True), e("ans", 0.60, "a.py", "f")]
    assert ranks(replay_run(run(question(pool2)), "multiply:0.9")) == {"q01": 2}       # 0.63 > 0.60
    assert ranks(replay_run(run(question(pool2)), "multiply:0.8")) == {"q01": 1}       # 0.56 < 0.60


def test_demote_uses_the_real_policy_and_respects_the_intent_gate():
    pool = [e("t", 0.70, "tests/t.py", "test_f", test=True), e("ans", 0.66, "a.py", "f")]
    assert ranks(replay_run(run(question(pool)), "demote:0.06")) == {"q01": 1}
    asked = question(pool, text="where is f tested?")
    assert ranks(replay_run(run(asked), "demote:0.06")) == {"q01": 2}, "a question that asks for tests is not demoted"
    assert ranks(replay_run(run(asked), "demote_ungated:0.06")) == {"q01": 1}, "the ungated control ignores the intent"


def test_a_changelog_is_demoted_twice_as_much_as_a_test():
    pool = [e("l", 0.75, "CHANGELOG.md", "x", log=True), e("ans", 0.66, "a.py", "f")]
    assert ranks(replay_run(run(question(pool)), "demote:0.06")) == {"q01": 1}
    pool = [e("t", 0.75, "tests/t.py", "x", test=True), e("ans", 0.66, "a.py", "f")]
    assert ranks(replay_run(run(question(pool)), "demote:0.06")) == {"q01": 2}


def test_unknown_policies_are_refused():
    for bad in ("shuffle", "multiply", "multiply:x", "demote:-1", "demote:", "multiply:0", "multiply:1.5"):
        with pytest.raises(ValueError):
            make_policy(bad)


def test_the_replay_never_changes_the_input_or_the_raw_scores():
    r = run(question([e("t", 0.70, "tests/t.py", "t", test=True), e("ans", 0.66, "a.py", "f")]))
    before = copy.deepcopy(r)
    out = replay_run(r, "demote:0.06")
    assert r == before
    assert [h["score"] for h in out["questions"][0]["pool"]] == [0.66, 0.70], "reordered, but each hit keeps its raw score"


def test_the_replayed_run_records_the_policy_and_keeps_the_meta():
    out = replay_run(run(question([e("c", 0.7, "a.py", "f")])), "demote:0.06")
    assert out["meta"]["policy"] == "demote:0.06" and out["meta"]["model"] == "m@1" and out["meta"]["repo"] == "r"


def test_flags_top1_top3_top10_follow_the_replayed_rank():
    pool = [e(f"n{i}", 0.9 - i * 0.01, "x.py", f"s{i}") for i in range(2)] + [e("ans", 0.5, "a.py", "f")]
    q = replay_run(run(question(pool)), "none")["questions"][0]
    assert (q["rank"], q["top1"], q["top3"], q["top10_hit"]) == (3, False, True, True)


def test_negatives_have_no_rank_even_if_the_pool_is_full():
    q = replay_run(run(question([e("c", 0.7, "a.py", "f")], expect=())), "none")["questions"][0]
    assert q["rank"] is None and q["expect"] == [] and q["found_in_context"] is False


# ---- the context estimate

def test_hits_in_one_passage_cost_once_and_the_answer_is_found_if_that_passage_fits():
    hits = [e("p0", 0.9, "a.py", "other", key="K", cost=400), e("p1", 0.8, "a.py", "f", key="K", cost=400)]
    assert simulate_context(hits, [{"path": "a.py", "symbol": "f"}], max_tokens=500), "one passage of 400 tokens fits; counted twice it would not"
    apart = [e("p0", 0.9, "a.py", "other", key="K1", cost=400), e("p1", 0.8, "a.py", "f", key="K2", cost=400)]
    assert not simulate_context(apart, [{"path": "a.py", "symbol": "f"}], max_tokens=500)


def test_the_budget_is_a_prefix_a_passage_that_does_not_fit_stops_the_list():
    hits = [e("a", 0.9, "x.py", "x", cost=300), e("b", 0.8, "y.py", "y", cost=300), e("c", 0.7, "a.py", "f", cost=10)]
    expect = [{"path": "a.py", "symbol": "f"}]
    assert not simulate_context(hits, expect, max_tokens=500), "b does not fit, so c (which would) is NOT squeezed in"
    assert simulate_context(hits, expect, max_tokens=700)


def test_the_best_passage_is_always_kept_even_if_it_alone_is_over_budget():
    hits = [e("a", 0.9, "a.py", "f", cost=5000)]
    assert simulate_context(hits, [{"path": "a.py", "symbol": "f"}], max_tokens=100)


def test_only_the_first_k_hits_are_considered():
    hits = [e(f"n{i}", 0.9 - i * 0.01, "x.py", f"s{i}", cost=1) for i in range(10)] + [e("ans", 0.5, "a.py", "f", cost=1)]
    assert not simulate_context(hits, [{"path": "a.py", "symbol": "f"}], max_tokens=6000, k=10)
    assert simulate_context(hits, [{"path": "a.py", "symbol": "f"}], max_tokens=6000, k=11)


def test_a_group_chunk_is_found_by_its_names():
    hits = [e("g", 0.9, "lib/app.js", None, names=["app.engine", "app.param"], cost=50)]
    assert simulate_context(hits, [{"path": "lib/app.js", "symbol": "app.param"}], max_tokens=500)


def test_a_policy_that_reorders_changes_what_reaches_the_context():
    pool = [e("t", 0.70, "tests/t.py", "test_f", test=True, cost=900), e("ans", 0.66, "a.py", "f", cost=200)]
    r = run(question(pool))
    r["meta"]["max_tokens"] = 1000
    assert replay_run(r, "none")["questions"][0]["found_in_context"] is False       # the test passage uses 900 of 1000, the answer (200) does not fit
    assert replay_run(r, "demote:0.06")["questions"][0]["found_in_context"] is True


# ---- the estimate is only trusted if it reproduces the real recorded answers

def test_with_no_policy_the_estimate_reproduces_the_real_found_in_context_flags():
    from eval.replay import estimate_matches_real
    pool = [e("a", 0.9, "x.py", "x", cost=300), e("ans", 0.8, "a.py", "f", cost=300)]
    q_ok = question(pool, id="q01")
    q_ok["found_in_context"] = True
    q_bad = question(pool, id="q02")
    q_bad["found_in_context"] = False              # the real run says "not found" but the estimate says found: they disagree
    agreement = estimate_matches_real(run(q_ok, q_bad))
    assert agreement == {"q01": True, "q02": False}


# ---- 7.9: the budget rule and the budget can be varied in a replay

def test_squeeze_skips_a_passage_that_does_not_fit_and_keeps_trying_the_next_ones():
    hits = [e("a", 0.9, "x.py", "x", cost=300), e("b", 0.8, "y.py", "y", cost=300), e("c", 0.7, "a.py", "f", cost=10)]
    expect = [{"path": "a.py", "symbol": "f"}]
    assert not simulate_context(hits, expect, max_tokens=500, rule="prefix")
    assert simulate_context(hits, expect, max_tokens=500, rule="squeeze"), "b (300) does not fit after a (300), but c (10) does"


def test_squeeze_never_loses_an_answer_the_prefix_rule_keeps():
    import itertools
    expect = [{"path": "a.py", "symbol": "f"}]
    for costs in itertools.product([10, 150, 400, 900], repeat=3):
        for budget in (100, 500, 1000, 2000):
            for answer_at in range(3):
                hits = [e(f"h{i}", 0.9 - i * 0.1, "a.py" if i == answer_at else "x.py", "f" if i == answer_at else f"s{i}", cost=c) for i, c in enumerate(costs)]
                if simulate_context(hits, expect, budget, rule="prefix"):
                    assert simulate_context(hits, expect, budget, rule="squeeze"), (costs, budget, answer_at)


def test_squeeze_also_always_keeps_the_best_passage():
    assert simulate_context([e("a", 0.9, "a.py", "f", cost=5000)], [{"path": "a.py", "symbol": "f"}], max_tokens=100, rule="squeeze")


def test_an_unknown_budget_rule_is_refused():
    with pytest.raises(ValueError, match="rule"):
        simulate_context([e("a", 0.9, "a.py", "f")], [{"path": "a.py", "symbol": "f"}], max_tokens=100, rule="greedy")
    with pytest.raises(ValueError, match="rule"):
        replay_run(run(question([e("a", 0.9, "a.py", "f")])), "none", rule="greedy")


def test_a_replay_can_use_another_budget_and_another_rule_than_the_run_was_made_with():
    pool = [e("a", 0.9, "x.py", "x", cost=300), e("b", 0.8, "y.py", "y", cost=300), e("c", 0.7, "a.py", "f", cost=10)]
    r = run(question(pool))                                     # the run's own budget is 6000: everything fits
    assert replay_run(r, "none")["questions"][0]["found_in_context"] is True
    assert replay_run(r, "none", max_tokens=500)["questions"][0]["found_in_context"] is False
    squeezed = replay_run(r, "none", max_tokens=500, rule="squeeze")
    assert squeezed["questions"][0]["found_in_context"] is True and squeezed["meta"]["rule"] == "squeeze" and squeezed["meta"]["max_tokens"] == 500
    assert r["meta"]["max_tokens"] == 6000, "the input run is not changed"
    assert replay_run(r, "none")["meta"]["rule"] == "prefix"


def test_squeeze_does_not_include_the_passage_that_does_not_fit():
    hits = [e("a", 0.9, "x.py", "x", cost=300), e("b", 0.8, "a.py", "f", cost=300)]
    assert not simulate_context(hits, [{"path": "a.py", "symbol": "f"}], max_tokens=500, rule="squeeze"), "the answer sits in a passage that does not fit: skipped, so not found"
    assert simulate_context(hits, [{"path": "a.py", "symbol": "f"}], max_tokens=600, rule="squeeze")


def test_an_unknown_rule_is_refused_even_when_no_question_has_an_answer_to_look_for():
    only_a_negative = run(question([e("a", 0.9, "a.py", "f")], expect=()))
    with pytest.raises(ValueError, match="rule"):
        replay_run(only_a_negative, "none", rule="greedy")
