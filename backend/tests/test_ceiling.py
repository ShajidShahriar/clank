"""I-6 review finding #2: "the best passage is never dropped" had no ceiling.

A 3,000-line function (dozens of parts) with a 1,000-token budget came back whole: tens of thousands of tokens, flagged over_budget. The flag
existed, but a caller that sent it anyway would overflow the LLM's window.

Policy now: NO passage is ever bigger than 2x the budget (less a small allowance for the notes). A passage above that is NARROWED: only the parts that
were hit, then their immediate neighbours while there is room, with a marker for every gap, and a header and a note that say what is shown
(the lines, the parts, how long the whole thing is). The rest can be read from the file by line range (the pointer idea): nothing is lost silently.
A single chunk is at most 800 tokens, so one hit part always fits unless the budget itself is tiny (then that one part is returned, flagged).
"""
import pytest

import chunk_store
from chunker.core import estimate_tokens
from embedding import FakeEmbedder
from indexing import index_project
from search import Hit, build_context, expand
from vectorstore import InMemoryVectorStore

LINES = 3000
BIG = "def big():\n" + "\n".join(f"    x{i} = compute({i}) + other_function_name({i})" for i in range(LINES)) + "\n"
SMALL = "def small():\n    return 1\n"


@pytest.fixture
def world(conn, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "big.py").write_text(BIG)
    (repo / "small.py").write_text(SMALL)
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, repo, e, store)
    return repo, e, store


def parts(conn):
    rows = [r for r in chunk_store.chunks_for_file(conn, 1, "big.py") if r["part_count"]]
    assert len(rows) > 40 and rows[0]["part"] == 0                      # a real many-part function
    return rows


def file_lines(repo, start, end):
    return "\n".join((repo / "big.py").read_text().split("\n")[start - 1:end])


def ask(conn, world, question, k=3, max_tokens=1000):
    repo, e, store = world
    return build_context(conn, 1, repo, e, store, question, k=k, max_tokens=max_tokens)


def test_a_huge_function_is_narrowed_to_the_hit_part_and_stays_under_the_ceiling(conn, world):
    ps = parts(conn)
    mid = ps[len(ps) // 2]
    ctx = ask(conn, world, mid["embed_text"], k=3, max_tokens=1000)
    (top,) = [p for p in ctx.passages if p.rel_path == "big.py"]
    assert top.narrowed and top.full_range == (1, LINES + 1) and top.part_count == len(ps)
    assert file_lines(world[0], mid["start_line"], mid["end_line"]) in top.text              # the hit part itself, verbatim
    assert "x0 = compute(0)" not in top.text and f"x{LINES - 1} = compute" not in top.text   # not the start, not the end
    assert ctx.tokens_used <= 2000                                                          # the 2x ceiling, notes included


def test_the_header_and_the_notes_say_what_is_shown_and_how_long_the_whole_thing_is(conn, world):
    ps = parts(conn)
    mid = ps[len(ps) // 2]
    ctx = ask(conn, world, mid["embed_text"], k=3, max_tokens=1000)
    header = next(line for line in ctx.text.split("\n") if line.startswith("### big.py:"))
    assert "NARROWED" in header and f"of {len(ps)}" in header and f"1-{LINES + 1}" in header
    note = ctx.text.split("[Notes]")[1]
    assert "very long" in note and "`big.py`" in note and "read" in note
    import re
    named = [(int(a), int(b)) for a, b in re.findall(r"(\d+)-(\d+)", re.search(r"\(lines ([^)]*)\) are shown", note).group(1))]
    assert any(a <= mid["start_line"] and mid["end_line"] <= b for a, b in named)       # the hit part's lines are inside a range the note names
    top = next(p for p in ctx.passages if p.rel_path == "big.py")
    assert named == top.shown_ranges and all(b - a < 1000 for a, b in named)           # exact ranges, never one span over a gap


def test_neighbours_are_added_while_there_is_room(conn, world):
    ps = parts(conn)
    i = len(ps) // 2
    ctx = ask(conn, world, ps[i]["embed_text"], k=1, max_tokens=3000)                       # ceiling about 5,800 tokens: room for three or four parts
    (top,) = ctx.passages
    assert top.narrowed
    for n in (i - 1, i, i + 1):
        assert ps[n]["part"] in top.shown_parts


def test_a_budget_that_fits_the_whole_function_gets_the_whole_function(conn, world):
    ps = parts(conn)
    ctx = ask(conn, world, ps[3]["embed_text"], k=1, max_tokens=1_000_000)
    (top,) = ctx.passages
    assert not top.narrowed and top.complete and top.text == BIG.rstrip("\n") and "NARROWED" not in ctx.text


def test_two_hit_parts_far_apart_are_shown_with_a_marker_for_exactly_the_lines_between(conn, world):
    ps = parts(conn)
    repo, *_ = world
    (top,) = expand(conn, 1, repo, [Hit(ps[10], 0.9), Hit(ps[40], 0.8)], ceiling_tokens=2400)
    shown = top.shown_parts
    assert 10 in shown and 40 in shown and shown == sorted(shown) and top.narrowed and not top.complete
    for n in shown:
        assert file_lines(repo, ps[n]["start_line"], ps[n]["end_line"]) in top.text                  # every shown part is verbatim
    # the one gap between the two runs of consecutive parts, in lines
    gap_after = max(n for n in shown if n < 40 and n + 1 not in shown)
    gap_before = min(n for n in shown if n > 10 and n - 1 not in shown)
    marker = f"[... lines {ps[gap_after]['end_line'] + 1}-{ps[gap_before]['start_line'] - 1} not shown ...]"
    assert marker in top.text.split("\n")


def test_the_best_hit_is_kept_first_when_not_every_hit_part_fits(conn, world):
    ps = parts(conn)
    repo, *_ = world
    hits = [Hit(ps[30], 0.5), Hit(ps[5], 0.99), Hit(ps[20], 0.7)]                      # the BEST is part 5
    (top,) = expand(conn, 1, repo, hits, ceiling_tokens=1000)                             # room for about one part
    assert 5 in top.shown_parts and 30 not in top.shown_parts and 20 not in top.shown_parts


def test_a_tiny_budget_still_returns_the_best_single_part_and_says_it_is_over(conn, world):
    ps = parts(conn)
    ctx = ask(conn, world, ps[7]["embed_text"], k=1, max_tokens=50)
    (top,) = ctx.passages
    assert top.narrowed and top.shown_parts == [7] and ctx.over_budget
    assert "longer than the space available" in ctx.text


def test_narrowed_text_is_made_of_real_file_lines(conn, world):
    ps = parts(conn)
    repo, *_ = world
    (top,) = expand(conn, 1, repo, [Hit(ps[12], 0.9)], ceiling_tokens=1800)
    file = set((repo / "big.py").read_text().split("\n"))
    for line in top.text.split("\n"):
        assert line in file or line.startswith("[... lines "), f"not a file line: {line!r}"


def test_small_chunks_and_ordinary_passages_are_never_narrowed(conn, world):
    ctx = ask(conn, world, "anything", k=10, max_tokens=100_000)
    assert not any(p.narrowed for p in ctx.passages) and "NARROWED" not in ctx.text


@pytest.mark.parametrize("max_tokens", [400, 700, 1000, 2500, 6000])
def test_no_context_is_ever_bigger_than_twice_the_budget_unless_one_chunk_already_is(conn, world, max_tokens):
    ps = parts(conn)
    one_part = max(estimate_tokens(p["embed_text"]) for p in ps)
    for p in (ps[0], ps[len(ps) // 2], ps[-1]):
        ctx = ask(conn, world, p["embed_text"], k=5, max_tokens=max_tokens)
        assert ctx.tokens_used == estimate_tokens(ctx.text)
        assert ctx.tokens_used <= max(2 * max_tokens, one_part + 400), (max_tokens, ctx.tokens_used)


def test_the_narrowed_text_marks_what_comes_before_and_after_what_is_shown(conn, world):
    ps = parts(conn)
    repo, *_ = world
    (top,) = expand(conn, 1, repo, [Hit(ps[20], 0.9)], ceiling_tokens=1000)
    shown = top.shown_parts
    first, last = ps[min(shown)], ps[max(shown)]
    lines = top.text.split("\n")
    assert lines[0] == f"[... lines 1-{first['start_line'] - 1} not shown ...]"
    assert lines[-1] == f"[... lines {last['end_line'] + 1}-{LINES + 1} not shown ...]"
    assert top.shown_ranges == [(first["start_line"], last["end_line"])]


def test_the_header_names_each_shown_range_not_one_span_over_the_gap(conn, world):
    from search import render_passage
    ps = parts(conn)
    repo, *_ = world
    (top,) = expand(conn, 1, repo, [Hit(ps[10], 0.9), Hit(ps[40], 0.8)], ceiling_tokens=2400)
    assert len(top.shown_ranges) == 2                                                          # two runs, a gap between them
    header = render_passage(top).split("\n")[0]
    (a1, b1), (a2, b2) = top.shown_ranges
    assert f"lines {a1}-{b1}, {a2}-{b2} of 1-{LINES + 1})" in header


def test_narrowing_aims_for_the_budget_not_the_ceiling(conn, world):
    # The ceiling (2x the budget) only decides WHEN to narrow. Once narrowing, the aim is to be back inside the budget: the best hit part,
    # then more parts only while they still fit the budget. (The first version filled up to the ceiling and flagged over_budget needlessly.)
    ps = parts(conn)
    mid = ps[len(ps) // 2]
    ctx = ask(conn, world, mid["embed_text"], k=3, max_tokens=1200)
    (top,) = [p for p in ctx.passages if p.rel_path == "big.py"]
    assert top.narrowed and ps[len(ps) // 2]["part"] in top.shown_parts
    assert ctx.tokens_used <= 1200 and not ctx.over_budget, (ctx.tokens_used, top.shown_parts)


def test_the_narrowing_target_leaves_room_for_the_notes(conn, world):
    # A budget with room for the best part AND its previous neighbour, but not for the notes that must go with them: the neighbour has to be left out.
    from search.budget import PASSAGE_OVERHEAD_TOKENS
    ps = parts(conn)
    i = len(ps) // 2
    cost = lambda n: estimate_tokens(ps[n]["text"]) + 1                              # noqa: E731
    budget = cost(i) + cost(i - 1) + PASSAGE_OVERHEAD_TOKENS + 60
    ctx = ask(conn, world, ps[i]["embed_text"], k=1, max_tokens=budget)
    (top,) = ctx.passages
    assert top.narrowed and i in top.shown_parts and (i - 1) not in top.shown_parts
    assert ctx.tokens_used <= budget and not ctx.over_budget, (ctx.tokens_used, budget)


def test_retrieve_narrows_to_the_budget_too(conn, world):
    from search import retrieve
    repo, e, store = world
    ps = parts(conn)
    result = retrieve(conn, 1, repo, e, store, ps[len(ps) // 2]["embed_text"], k=3, max_tokens=1200)
    assert any(p.narrowed for p in result.passages)
    assert result.tokens_used <= 1200 and not result.over_budget, result.tokens_used
