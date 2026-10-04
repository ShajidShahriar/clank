"""Task I-7.2: the ruler. Pure functions, hand-made hits, no Ollama.

A hit is a stored chunk row (a dict). A question's `expect` is ANY-OF a list of {path, symbol}:
- the path must equal the chunk's `rel_path`;
- `symbol: None` means the path alone is enough (the chunker gives test files and some getters no symbol);
- otherwise the symbol must equal the chunk's `symbol` (a part of a split chunk keeps the symbol, so it counts), or be one of a group's `names`,
  or be `Parent.symbol` for a method;
- a different file with the same symbol does NOT count, and neither does the right file with a different symbol.
Rank is 1-based. Negatives (empty `expect`) are scored but never counted in the hit rate.
"""
from types import SimpleNamespace

import pytest

from eval.score import in_context, matches, rank_of, score_question, summarize


def row(rel_path="a.py", symbol=None, names=(), parent=None, part=None, part_count=None, kind="function"):
    return {"rel_path": rel_path, "symbol": symbol, "names": list(names), "parent": parent, "part": part, "part_count": part_count, "kind": kind}


def want(path="a.py", symbol="f"):
    return {"path": path, "symbol": symbol}


# ---- matches: one expected entry against one chunk

def test_same_path_and_same_symbol_matches():
    assert matches(want("a.py", "f"), row("a.py", "f"))


def test_the_right_file_with_another_symbol_does_not_match():
    assert not matches(want("a.py", "f"), row("a.py", "g"))


def test_the_same_symbol_in_another_file_does_not_match():
    assert not matches(want("src/app.py", "make_response"), row("src/helpers.py", "make_response"))


def test_a_symbol_inside_a_groups_names_matches_and_a_group_without_it_does_not():
    group = row("lib/app.js", None, names=["app.engine", "app.param"], kind="group")
    assert matches(want("lib/app.js", "app.param"), group)
    assert not matches(want("lib/app.js", "app.use"), group)


def test_names_are_compared_whole_not_by_substring():
    group = row("lib/app.js", None, names=["app.param2", "res.send"], kind="group")
    assert not matches(want("lib/app.js", "app.param"), group)
    assert not matches(want("lib/app.js", "send"), group)


def test_one_part_of_a_split_symbol_matches():
    assert matches(want("a.py", "big"), row("a.py", "big", part=2, part_count=3))


def test_a_method_matches_its_parent_qualified_name_only_for_that_parent():
    method = row("a.py", "make_response", parent="Flask", kind="method")
    assert matches(want("a.py", "Flask.make_response"), method)
    assert matches(want("a.py", "make_response"), method)
    assert not matches(want("a.py", "Other.make_response"), method)


def test_a_null_symbol_means_the_path_alone_is_enough():
    assert matches(want("test/res.cookie.js", None), row("test/res.cookie.js", None, kind="module_code"))
    assert not matches(want("test/res.cookie.js", None), row("test/res.send.js", None, kind="module_code"))


# ---- rank_of: 1-based, first match, any-of

def test_rank_is_one_based_and_first_match_wins():
    hits = [row("x.py", "a"), row("a.py", "f"), row("a.py", "f")]
    assert rank_of([want("a.py", "f")], hits) == 2


def test_rank_is_none_when_nothing_matches_or_there_are_no_hits():
    assert rank_of([want("a.py", "f")], [row("x.py", "a")]) is None
    assert rank_of([want("a.py", "f")], []) is None


def test_any_of_takes_the_best_ranked_entry():
    hits = [row("x.py", "a"), row("b.py", "g"), row("a.py", "f")]
    assert rank_of([want("a.py", "f"), want("b.py", "g")], hits) == 2


# ---- score_question: the top-1 / top-3 / top-10 flags at their boundaries

def q(expect, id="q01", kind="name", split="tune"):
    return {"id": id, "repo": "r", "kind": kind, "split": split, "question": "?", "expect": expect}


def hits_with_answer_at(rank, total=12):
    return [row("a.py", "f") if i == rank else row("x.py", f"n{i}") for i in range(1, total + 1)]


@pytest.mark.parametrize("rank, top1, top3, top10", [
    (1, True, True, True),
    (2, False, True, True),
    (3, False, True, True),
    (4, False, False, True),
    (10, False, False, True),
    (11, False, False, False),
])
def test_the_top_n_flags_at_the_boundaries(rank, top1, top3, top10):
    s = score_question(q([want("a.py", "f")]), hits_with_answer_at(rank))
    assert (s.rank, s.top1, s.top3, s.top10) == (rank, top1, top3, top10)


def test_a_miss_has_no_rank_and_no_flags():
    s = score_question(q([want("a.py", "f")]), [row("x.py", "a")])
    assert (s.rank, s.top1, s.top3, s.top10) == (None, False, False, False)


def test_the_score_carries_the_question_id_kind_and_split():
    s = score_question(q([want()], id="q07", kind="concept", split="holdout"), [row("a.py", "f")])
    assert (s.id, s.kind, s.split) == ("q07", "concept", "holdout")


def test_a_negative_is_marked_not_counted_and_never_has_a_rank():
    s = score_question(q([], id="q21", kind="negative"), [row("a.py", "f")])
    assert not s.counted
    assert s.rank is None and not s.top10
    assert score_question(q([want()]), [row("a.py", "f")]).counted


# ---- summarize

def scores(*ranks, kind="name", split="tune"):
    out = []
    for i, r in enumerate(ranks, 1):
        hits = hits_with_answer_at(r) if r else [row("x.py", "a")]
        out.append(score_question(q([want("a.py", "f")], id=f"q{i:02d}", kind=kind, split=split), hits))
    return out


def test_summary_rates_are_fractions_of_the_counted_questions():
    s = summarize(scores(1, 2, 5, None))
    assert s["n"] == 4
    assert (s["top1"], s["top3"], s["top10"]) == (0.25, 0.5, 0.75)
    assert s["ranks"] == {"q01": 1, "q02": 2, "q03": 5, "q04": None}
    assert s["misses"] == ["q04"]


def test_negatives_are_left_out_of_the_hit_rate():
    neg = score_question(q([], id="q21", kind="negative"), [row("x.py", "a")])
    s = summarize(scores(1, 1) + [neg])
    assert s["n"] == 2
    assert s["top1"] == 1.0
    assert "q21" not in s["ranks"] and "q21" not in s["misses"]


def test_summary_can_be_limited_to_one_split():
    mixed = scores(1, 1, split="tune") + [score_question(q([want()], id="q09", split="holdout"), [row("x.py", "a")])]
    assert summarize(mixed, split="tune")["top1"] == 1.0
    assert summarize(mixed, split="holdout")["top1"] == 0.0
    assert summarize(mixed)["n"] == 3


def test_summary_of_nothing_does_not_divide_by_zero():
    s = summarize([])
    assert s["n"] == 0 and s["top1"] is None and s["top3"] is None and s["top10"] is None


def test_summary_can_be_split_by_kind():
    mixed = scores(1, split="tune", kind="name") + scores(None, kind="concept")
    s = summarize(mixed, by_kind=True)
    assert s["by_kind"]["name"]["top1"] == 1.0
    assert s["by_kind"]["concept"]["top1"] == 0.0


# ---- in_context: is the answer inside the passages the LLM really got?

def passage(rel_path="a.py", symbol=None, names=(), parent=None):
    return SimpleNamespace(rel_path=rel_path, symbol=symbol, names=list(names), parent=parent)


def test_the_answer_is_in_context_when_a_passage_matches_and_not_otherwise():
    expect = [want("a.py", "f")]
    assert in_context(expect, [passage("x.py", "g"), passage("a.py", "f")])
    assert not in_context(expect, [passage("x.py", "g"), passage("a.py", "other")])
    assert not in_context(expect, [])


def test_in_context_uses_group_names_and_parents_too():
    assert in_context([want("a.js", "app.param")], [passage("a.js", None, names=["app.param"])])
    assert in_context([want("a.py", "Flask.make_response")], [passage("a.py", "make_response", parent="Flask")])


def test_in_context_of_a_negative_is_not_a_hit():
    assert not in_context([], [passage("a.py", "f")])
