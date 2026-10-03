"""Task I-6.3: the token budget. Rank first, expand second, count tokens AFTER expansion, and never cut a passage in half.

Two design decisions, both tested:
- The result is always a PREFIX of the ranking: when passage #2 does not fit, a smaller #3 is not squeezed in around it. The LLM never sees
  a lower-ranked passage while a higher-ranked one is missing, which keeps the result easy to reason about.
- If the BEST passage alone is bigger than the whole budget it is still returned, alone, flagged `over_budget`: dropping the best answer is the
  worst outcome. (Chunks are capped at 800 tokens, so this only happens for a very long split chunk; the flag lets the caller decide.)
"""
import pytest

import chunk_store
from chunker.core import estimate_tokens
from embedding import FakeEmbedder
from indexing import index_project
from search import IndexOutOfDate, Passage, fit_to_budget, retrieve
from search.budget import PASSAGE_OVERHEAD_TOKENS
from vectorstore import InMemoryVectorStore


def passage(chars, score=0.5, rel="a.py", ids=None):
    return Passage(ids or [f"{rel}-{chars}"], rel, "f", None, "function", 1, 2, "x" * chars, score, "index", True, [])


def cost(p):
    return estimate_tokens(p.text) + PASSAGE_OVERHEAD_TOKENS


# ---- fit_to_budget ----

def test_everything_that_fits_is_returned_in_order_with_the_tokens_it_uses():
    ps = [passage(300, 0.9, "a.py"), passage(600, 0.8, "b.py"), passage(90, 0.7, "c.py")]
    result = fit_to_budget(ps, max_tokens=1000)
    assert result.passages == ps and result.dropped == [] and not result.over_budget
    assert result.tokens_used == sum(cost(p) for p in ps)


def test_the_result_is_a_prefix_of_the_ranking_a_big_second_blocks_the_small_third():
    a, big, small = passage(300, 0.9), passage(3000, 0.8), passage(30, 0.7)
    result = fit_to_budget([a, big, small], max_tokens=300)
    assert result.passages == [a] and result.dropped == [big, small]      # small would have fit, but #2 did not: nothing is skipped over


def test_a_passage_that_uses_exactly_the_whole_budget_fits_and_one_token_more_does_not():
    p = passage(300)
    assert fit_to_budget([p], max_tokens=cost(p)).passages == [p]
    second = passage(30, 0.4)
    assert fit_to_budget([p, second], max_tokens=cost(p) + cost(second)).dropped == []
    assert fit_to_budget([p, second], max_tokens=cost(p) + cost(second) - 1).dropped == [second]


def test_the_best_passage_is_returned_alone_even_when_it_is_bigger_than_the_whole_budget():
    huge, other = passage(6000, 0.9), passage(30, 0.8)
    result = fit_to_budget([huge, other], max_tokens=100)
    assert result.passages == [huge] and result.over_budget and result.dropped == [other]
    assert result.tokens_used == cost(huge)


def test_over_budget_is_only_ever_about_the_best_passage():
    a, b = passage(300, 0.9), passage(6000, 0.8)
    result = fit_to_budget([a, b], max_tokens=200)
    assert result.passages == [a] and not result.over_budget and result.dropped == [b]


def test_passages_are_never_cut_or_changed():
    ps = [passage(450, 0.9), passage(450, 0.8)]
    original = [p.text for p in ps]
    result = fit_to_budget(ps, max_tokens=cost(ps[0]) + 5)
    assert [p.text for p in result.passages] == original[:1] and result.passages[0] is ps[0]


def test_nothing_in_nothing_out():
    result = fit_to_budget([], max_tokens=100)
    assert result.passages == [] and result.dropped == [] and result.tokens_used == 0 and not result.over_budget


def test_the_default_cost_is_the_estimate_plus_a_header_allowance():
    p = passage(300)
    assert fit_to_budget([p], max_tokens=10_000).tokens_used == estimate_tokens("x" * 300) + PASSAGE_OVERHEAD_TOKENS == 100 + PASSAGE_OVERHEAD_TOKENS


def test_the_cost_function_can_be_replaced():
    ps = [passage(300, 0.9), passage(300, 0.8), passage(300, 0.7)]
    result = fit_to_budget(ps, max_tokens=250, cost=lambda p: 100)             # e.g. a real tokenizer, or the formatted block of task 6.4
    assert result.passages == ps[:2] and result.tokens_used == 200 and result.dropped == ps[2:]


@pytest.mark.parametrize("bad", [0, -5, 2.5, None, True, "100"])
def test_a_bad_budget_is_refused(bad):
    with pytest.raises(ValueError):
        fit_to_budget([passage(10)], max_tokens=bad)


# ---- retrieve: search -> expand -> budget, in that order ----

STATEMENTS = "def big():\n" + "\n".join(f"    x{i} = compute({i}) + other_function_name({i})" for i in range(120)) + "\n"
FILES = {"stmts.py": STATEMENTS, "small.py": "def small():\n    return 1\n", "other.py": "def other():\n    return 2\n"}


@pytest.fixture
def world(conn, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in FILES.items():
        (repo / name).write_text(text)
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, repo, e, store)
    return repo, e, store


def own_text(conn, rel, part=None, symbol=None):
    rows = [r for r in chunk_store.chunks_for_file(conn, 1, rel) if (part is None or r["part"] == part) and (symbol is None or r["symbol"] == symbol)]
    return rows[0]["embed_text"]


def test_the_budget_counts_the_expanded_passage_not_the_hit(conn, world):
    from search.budget import NOTES_ALLOWANCE_TOKENS
    repo, e, store = world
    middle = own_text(conn, "stmts.py", part=1)                               # a hit on ONE part (about 600 chars)
    whole = estimate_tokens(STATEMENTS.rstrip("\n")) + PASSAGE_OVERHEAD_TOKENS
    one_part = estimate_tokens(next(r["text"] for r in chunk_store.chunks_for_file(conn, 1, "stmts.py") if r["part"] == 1)) + PASSAGE_OVERHEAD_TOKENS
    # In the band where the whole function is bigger than the budget but not bigger than the ceiling (2x the budget less the notes allowance),
    # it is returned whole, judged by its real size. Room for the part, not for the whole function:
    budget = (whole + NOTES_ALLOWANCE_TOKENS) // 2 + 1
    assert one_part < budget < whole
    result = retrieve(conn, 1, repo, e, store, middle, k=1, max_tokens=budget)
    (top,) = result.passages
    assert len(top.chunk_ids) == 3 and top.text.startswith("def big():") and not top.narrowed   # the WHOLE function was expanded
    assert result.over_budget and result.tokens_used == whole                                     # and then judged by its real size


def test_a_function_bigger_than_twice_the_budget_is_narrowed_instead_of_returned_whole(conn, world):
    repo, e, store = world
    middle = own_text(conn, "stmts.py", part=1)
    result = retrieve(conn, 1, repo, e, store, middle, k=1, max_tokens=150)                       # the whole function is far over the ceiling
    (top,) = result.passages
    assert top.narrowed and 1 in top.shown_parts and len(top.chunk_ids) < 3


def test_two_hits_on_one_function_cost_one_passage_not_two(conn, world):
    repo, e, store = world
    whole = estimate_tokens(STATEMENTS.rstrip("\n")) + PASSAGE_OVERHEAD_TOKENS
    result = retrieve(conn, 1, repo, e, store, own_text(conn, "stmts.py", part=0), k=10, max_tokens=whole)
    stmts = [p for p in result.passages if p.rel_path == "stmts.py"]
    assert len(stmts) == 1 and not result.over_budget and result.tokens_used == whole   # parts 0, 1, 2 all hit, counted once


def test_lower_ranked_passages_are_dropped_first_and_reported(conn, world):
    repo, e, store = world
    question = own_text(conn, "small.py")
    everything = retrieve(conn, 1, repo, e, store, question, k=10, max_tokens=100_000)
    assert everything.dropped == [] and everything.passages[0].rel_path == "small.py"
    tight = retrieve(conn, 1, repo, e, store, question, k=10, max_tokens=cost(everything.passages[0]) + 1)               # room for the best one only
    assert tight.passages == everything.passages[:len(tight.passages)]        # a prefix of the same ranking
    assert [p.rel_path for p in tight.passages + tight.dropped] == [p.rel_path for p in everything.passages] and tight.dropped   # the same ranking


def test_a_flagged_file_is_hidden_and_its_reason_reaches_the_answer_layer(conn, world):
    repo, e, store = world
    stale_text = own_text(conn, "small.py")
    (repo / "small.py").write_text("def small():\n    return '" + "x" * 400 + "'\n")           # now too long for the model: skipped and flagged
    index_project(conn, 1, repo, FakeEmbedder(max_chars=300), store)
    result = retrieve(conn, 1, repo, e, store, stale_text, k=10, max_tokens=100_000)
    assert set(result.hidden_files) == {"small.py"} and "EmbeddingTooLong" in result.hidden_files["small.py"]
    assert "small.py" not in {p.rel_path for p in result.passages} | {p.rel_path for p in result.dropped}


def test_the_cost_function_is_used_by_retrieve(conn, world):
    repo, e, store = world
    cheap = retrieve(conn, 1, repo, e, store, "anything", k=10, max_tokens=2500)
    assert len(cheap.passages) == 3 and cheap.dropped == []                              # three files, all small
    costly = retrieve(conn, 1, repo, e, store, "anything", k=10, max_tokens=2500, cost=lambda p: 1000)
    assert len(costly.passages) == 2 and len(costly.dropped) == 1 and costly.tokens_used == 2000


def test_a_stale_index_is_still_refused(conn, world):
    repo, e, store = world
    with pytest.raises(IndexOutOfDate):
        retrieve(conn, 1, repo, FakeEmbedder(digest="replaced"), store, "anything", k=3, max_tokens=5000)
