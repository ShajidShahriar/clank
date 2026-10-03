"""Task I-6.4: the context text the LLM receives.

Each passage becomes a block: a header line (file, line range, symbol, kind), then the code in a fence. Notes at the end tell the LLM what it is NOT
seeing: files that could not be indexed (and why), results that did not fit, a best match bigger than the space. The promise that matters:
the budget counts EXACTLY what is sent (blocks, fences, separators and notes), so the final text never exceeds `max_tokens` unless the best passage
alone is bigger than the whole budget (flagged, and the note says so).
"""
import re

import pytest

import chunk_store
from chunker.core import estimate_tokens
from embedding import FakeEmbedder
from indexing import index_project
from search import IndexOutOfDate, Passage, build_context, render_passage
from vectorstore import InMemoryVectorStore


def passage(text="return 1", rel="shop.py", symbol="order", parent=None, kind="function", start=3, end=4, complete=True, missing=(), score=0.9):
    return Passage(["id1"], rel, symbol, parent, kind, start, end, text, score, "index", complete, list(missing))


def parse_block(block):
    """header, language, code of one rendered block (the fence is the first line starting with backticks)."""
    lines = block.split("\n")
    header, fence_open = lines[0], lines[1]
    fence = re.match(r"(`+)(.*)", fence_open)
    assert fence and lines[-1] == fence.group(1), f"block is not fenced properly: {block[:200]!r}"
    return header, fence.group(2), "\n".join(lines[2:-1])


# ---- one block ----

def test_a_block_has_a_header_with_file_lines_symbol_and_kind_then_the_code_in_a_fence():
    block = render_passage(passage("def order(x):\n    return x", parent="Shop", start=12, end=40))
    header, language, code = parse_block(block)
    assert header == "### shop.py:12-40 · Shop.order (function)" and language == "python" and code == "def order(x):\n    return x"


@pytest.mark.parametrize("symbol,parent,kind,label", [("order", None, "function", "order"), ("order", "Shop", "method", "Shop.order"),
                                                       (None, None, "imports", "imports"), (None, None, "module_code", "module_code")])
def test_the_label_is_parent_dot_symbol_or_the_kind_when_there_is_no_name(symbol, parent, kind, label):
    header, _, _ = parse_block(render_passage(passage(symbol=symbol, parent=parent, kind=kind)))
    assert header == f"### shop.py:3-4 · {label} ({kind})"


@pytest.mark.parametrize("rel,language", [("a.py", "python"), ("web/app.tsx", "typescript"), ("x.js", "javascript"), ("README.md", "markdown"), ("Makefile", "")])
def test_the_fence_names_the_language_when_it_is_known(rel, language):
    _, found, _ = parse_block(render_passage(passage(rel=rel)))
    assert found == language


@pytest.mark.parametrize("longest_run", [0, 1, 2, 3, 5])
def test_code_that_contains_backticks_cannot_break_out_of_its_fence(longest_run):
    # Markdown chunks contain ``` blocks of their own. The fence must be longer than any run of backticks inside.
    code = "before\n" + ("`" * longest_run) + "inner\nafter"
    header, _, recovered = parse_block(render_passage(passage(code, rel="README.md", kind="doc_section")))
    assert recovered == code
    fence_length = len(re.match(r"(`+)", render_passage(passage(code, rel="README.md")).split("\n")[1]).group(1))
    assert fence_length >= 3 and fence_length > longest_run


def test_an_incomplete_passage_says_which_parts_are_missing_in_its_header():
    header, _, _ = parse_block(render_passage(passage(complete=False, missing=[1, 3])))
    assert "INCOMPLETE" in header and "parts 2, 4" in header
    assert "INCOMPLETE" not in parse_block(render_passage(passage()))[0]


def test_the_score_is_not_shown_to_the_llm():
    assert "0.9" not in render_passage(passage(score=0.9123)) and "0.9123" not in render_passage(passage(score=0.9123))


# ---- the whole context ----

FILES = {
    "shop.py": "def order_total(items):\n    return sum(i.price for i in items)\n\n\ndef apply_discount(total, pct):\n    return total * (1 - pct)\n",
    "net.py": "def retry_request(url, attempts=3):\n    for _ in range(attempts):\n        pass\n    return None\n",
    "util.py": "def slugify(text):\n    return text.lower().replace(' ', '-')\n",
    "README.md": "# Shop\n\nA tiny shop.\n\n## Usage\n\n```sh\nmake run\n```\n\nThat is all.\n",
}
BIG = "def big():\n    return '" + "x" * 400 + "'\n"


@pytest.fixture
def world(conn, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in FILES.items():
        (repo / name).write_text(text)
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, repo, e, store)
    return repo, e, store


def ask(conn, world, question, k=10, max_tokens=100_000):
    repo, e, store = world
    return build_context(conn, 1, repo, e, store, question, k=k, max_tokens=max_tokens)


def own_text(conn, rel, symbol=None):
    return next(r["embed_text"] for r in chunk_store.chunks_for_file(conn, 1, rel) if symbol is None or r["symbol"] == symbol)


def blocks_of(text):
    return [b for b in text.split("\n\n### ") if b]


def test_blocks_come_in_rank_order_and_every_chunk_text_appears_exactly_once(conn, world):
    ctx = ask(conn, world, own_text(conn, "net.py", "retry_request"))
    assert ctx.text.startswith("### net.py:1-4 · retry_request (function)")
    for p in ctx.passages:
        assert ctx.text.count(p.text) == 1
    assert [p.rel_path for p in ctx.passages][0] == "net.py" and not ctx.dropped


def test_no_notes_when_there_is_nothing_to_say(conn, world):
    ctx = ask(conn, world, "anything")
    assert "could not be indexed" not in ctx.text and "left out" not in ctx.text and "longer than" not in ctx.text


def test_a_markdown_chunk_with_its_own_code_fence_survives_in_the_context(conn, world):
    ctx = ask(conn, world, own_text(conn, "README.md"))
    block = next(b for b in ("### " + x for x in blocks_of(ctx.text)) if "README.md" in b.split("\n")[0] and "make run" in b)
    header, language, code = parse_block(block.split("\n\n[Notes]")[0])
    assert language == "markdown" and "```sh\nmake run\n```" in code


# ---- notes ----

def flag(conn, world, rel, e_limit=300):
    repo, e, store = world
    (repo / rel).write_text(BIG)
    index_project(conn, 1, repo, FakeEmbedder(max_chars=e_limit), store)
    assert rel in chunk_store.failed_files(conn, 1)


def test_files_that_could_not_be_indexed_are_named_with_their_reason_and_their_old_text_is_absent(conn, world):
    stale = own_text(conn, "net.py", "retry_request")
    stale_code = next(r["text"] for r in chunk_store.chunks_for_file(conn, 1, "net.py"))
    flag(conn, world, "net.py")
    ctx = ask(conn, world, stale)
    assert "net.py" in ctx.hidden_files and "could not be indexed" in ctx.text
    note = ctx.text.split("could not be indexed")[1]
    assert "net.py" in note and "EmbeddingTooLong" in note
    assert stale_code not in ctx.text


def test_the_reason_is_one_short_line_and_a_long_list_is_collapsed(conn, world):
    repo, e, store = world
    for i in range(30):
        chunk_store.mark_file_failed(conn, 1, f"gen/f{i:02d}.py", "EmbeddingTooLong: " + "chunk-id-" * 60 + "\nsecond line", file_hash="h", chunker_version="v")
    ctx = ask(conn, world, "anything")
    note = ctx.text.split("could not be indexed", 1)[1]
    assert "second line" not in note and "chunk-id-" * 60 not in note and "and 10 more" in note
    assert note.count("gen/f") == 20


def test_results_that_did_not_fit_are_counted(conn, world):
    everything = ask(conn, world, "anything")
    first_block_tokens = estimate_tokens(render_passage(everything.passages[0])) + 80
    tight = ask(conn, world, "anything", max_tokens=first_block_tokens)
    assert tight.dropped and f"{len(tight.dropped)} more matching result" in tight.text and "left out" in tight.text


def test_a_best_match_bigger_than_the_space_is_returned_alone_and_the_note_says_so(conn, world):
    ctx = ask(conn, world, own_text(conn, "net.py", "retry_request"), max_tokens=5)
    assert ctx.over_budget and len(ctx.passages) == 1 and "longer than the space available" in ctx.text


def test_nothing_found_says_so_instead_of_returning_an_empty_string(conn, world):
    for rel in ("shop.py", "net.py", "util.py", "README.md"):
        (world[0] / rel).write_text(BIG)
    index_project(conn, 1, world[0], FakeEmbedder(max_chars=300), world[2])
    ctx = ask(conn, world, "anything")
    assert ctx.passages == [] and ctx.text.startswith("No matching code was found") and "could not be indexed" in ctx.text


# ---- the budget counts exactly what is sent ----

@pytest.mark.parametrize("max_tokens", [60, 100, 160, 250, 400, 800, 3000])
@pytest.mark.parametrize("with_hidden", [False, True])
def test_the_final_text_never_exceeds_the_budget_unless_the_best_passage_alone_is_bigger(conn, world, max_tokens, with_hidden):
    if with_hidden:
        flag(conn, world, "util.py")
    for question in (own_text(conn, "shop.py", "order_total"), "anything at all", own_text(conn, "README.md")):
        ctx = ask(conn, world, question, k=10, max_tokens=max_tokens)
        assert ctx.tokens_used == estimate_tokens(ctx.text)                       # what it reports is what was sent
        assert ctx.over_budget or ctx.tokens_used <= max_tokens, f"{ctx.tokens_used} > {max_tokens}: {ctx.text[:80]!r}"


def test_a_budget_that_is_exactly_enough_for_everything_needs_no_notes(conn, world):
    q = own_text(conn, "shop.py", "order_total")
    full = ask(conn, world, q, k=10, max_tokens=100_000)
    exact = ask(conn, world, q, k=10, max_tokens=full.tokens_used)
    assert [p.chunk_ids for p in exact.passages] == [p.chunk_ids for p in full.passages] and exact.dropped == []
    assert exact.text == full.text and "[Notes]" not in exact.text


def test_one_token_less_drops_something_and_the_notes_still_fit_inside_the_budget(conn, world):
    q = own_text(conn, "shop.py", "order_total")
    full = ask(conn, world, q, k=10, max_tokens=100_000)
    tight = ask(conn, world, q, k=10, max_tokens=full.tokens_used - 1)
    assert tight.dropped and "left out" in tight.text and not tight.over_budget
    assert tight.tokens_used <= full.tokens_used - 1


def test_a_best_match_that_fits_is_never_called_too_big_just_because_notes_follow_it(conn, world):
    # The first version reserved room for the "too big" note even when the best passage fit, which shrank the room until it did not fit.
    q = own_text(conn, "shop.py", "order_total")
    one_block = estimate_tokens(ask(conn, world, q, k=1, max_tokens=100_000).text)
    ctx = ask(conn, world, q, k=10, max_tokens=one_block + 60)                 # room for the best block and the "left out" note, not for the rest
    assert not ctx.over_budget and ctx.passages and ctx.dropped and ctx.tokens_used <= one_block + 60


def test_the_same_question_gives_the_same_text(conn, world):
    q = own_text(conn, "util.py")
    assert ask(conn, world, q, max_tokens=500).text == ask(conn, world, q, max_tokens=500).text


def test_a_stale_index_is_still_refused(conn, world):
    repo, e, store = world
    with pytest.raises(IndexOutOfDate):
        build_context(conn, 1, repo, FakeEmbedder(digest="replaced"), store, "q", k=3, max_tokens=1000)


@pytest.mark.parametrize("bad", [0, -1, 2.5, None, True, "100"])
def test_a_bad_budget_is_refused_before_any_work(conn, world, bad):
    repo, e, store = world
    queries = e.query_count
    with pytest.raises(ValueError):
        build_context(conn, 1, repo, e, store, "q", k=3, max_tokens=bad)
    assert e.query_count == queries


def test_when_the_best_block_fits_but_not_the_left_out_note_the_note_is_dropped_not_the_budget_broken(conn, world):
    q = own_text(conn, "shop.py", "order_total")
    exactly_one_block = estimate_tokens(ask(conn, world, q, k=1, max_tokens=100_000).text)
    ctx = ask(conn, world, q, k=10, max_tokens=exactly_one_block)
    assert len(ctx.passages) == 1 and ctx.dropped and not ctx.over_budget
    assert ctx.tokens_used <= exactly_one_block and "left out" not in ctx.text     # information is sacrificed before the budget is


def test_only_the_first_line_of_a_short_multi_line_reason_is_shown(conn, world):
    # (the long-reason test cannot see this: the 100-character cut hides the second line anyway)
    chunk_store.mark_file_failed(conn, 1, "gen.py", "EmbeddingTooLong: two chunks\nsecond line with detail", file_hash="h", chunker_version="v")
    note = ask(conn, world, "anything").text.split("could not be indexed", 1)[1]
    assert "gen.py" in note and "EmbeddingTooLong: two chunks" in note and "second line" not in note


# ---- I-6 review finding #3: a grouped chunk is labelled by its members, not just "group" ----

@pytest.mark.parametrize("kind,names,label", [
    ("group", ["first", "second", "third"], "first, second, third"),
    ("module_code", ["MAX_RETRIES", "TIMEOUT"], "MAX_RETRIES, TIMEOUT"),
    ("group", [f"f{i}" for i in range(12)], "f0, f1, f2, f3, f4, f5, f6, f7, +4 more"),
    ("group", [], "group"),
    ("imports", [], "imports"),
])
def test_a_group_or_a_block_of_constants_is_labelled_by_the_names_it_contains(kind, names, label):
    p = passage(symbol=None, kind=kind)
    p.names = names
    header, _, _ = parse_block(render_passage(p))
    assert header == f"### shop.py:3-4 · {label} ({kind})"


def test_a_real_group_chunk_shows_its_members_in_the_context(conn, tmp_path, monkeypatch):
    from chunker import grouping
    monkeypatch.setattr(grouping, "GROUP_SMALL_CHUNKS", True)
    repo = tmp_path / "g"
    repo.mkdir()
    (repo / "auth.py").write_text("def login(u):\n    return 1\n\n\ndef logout(u):\n    return 2\n")
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, repo, e, store)
    ctx = build_context(conn, 1, repo, e, store, "anything", k=3, max_tokens=5000)
    header = ctx.text.split("\n")[0]
    assert header == "### auth.py:1-6 · login, logout (group)"
