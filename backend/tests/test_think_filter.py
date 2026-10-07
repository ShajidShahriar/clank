"""Task: streaming answers, part 1: models that write their thinking INLINE as `<think>...</think>` at the start of the answer (some local models do).

The thinking must never reach the answer text, and a tag can arrive cut in pieces ("<thi" + "nk>"), so a tiny pure filter reads the text as it comes. What is promised:
- ordinary text passes through at once, unchanged, with nothing held back;
- `<think>` is only a thinking tag at the very START of the answer (after spaces or blank lines); anywhere else it is just text;
- a tag cut in pieces is still found, and text that only LOOKS like the start of a tag ("<div>", "<th") is never lost or reordered;
- the end of the thinking is `</think>`; the blank lines after it are dropped once; everything after it is answer text, and a second `<think>` there is text;
- nothing is ever lost: for EVERY way of cutting a text into pieces, thinking + text together hold exactly the same characters;
- no piece is ever an empty string; at the end of the stream `finish()` gives back anything still held (an unclosed thinking stays thinking).
"""
import pytest

from llm.think_filter import ThinkTagFilter


def run(*pieces):
    """Feed the pieces, finish, and return (all thinking text, all answer text, the raw list of (kind, text))."""
    f, out = ThinkTagFilter(), []
    for piece in pieces:
        out += f.feed(piece)
    out += f.finish()
    return "".join(t for k, t in out if k == "thinking"), "".join(t for k, t in out if k == "text"), out


def test_plain_text_passes_through_at_once_and_unchanged():
    f = ThinkTagFilter()
    assert f.feed("Hello") == [("text", "Hello")]
    assert f.feed(" world") == [("text", " world")]
    assert f.finish() == []


def test_a_whole_think_block_in_one_piece():
    thinking, text, _ = run("<think>let me see</think>The answer.")
    assert (thinking, text) == ("let me see", "The answer.")


def test_a_tag_cut_in_pieces_is_still_found():
    thinking, text, _ = run("<thi", "nk>reason", "ing</th", "ink>\n\nHello")
    assert (thinking, text) == ("reasoning", "Hello"), "the blank lines after the closing tag are dropped"


def test_every_possible_cut_gives_the_same_result():
    whole = "<think>step one. step two.</think>\n\nFinal <b>answer</b> text."
    for i in range(len(whole) + 1):
        for j in range(i, len(whole) + 1):
            thinking, text, _ = run(whole[:i], whole[i:j], whole[j:])
            assert (thinking, text) == ("step one. step two.", "Final <b>answer</b> text."), (i, j)


def test_a_cut_into_single_characters_works_too():
    thinking, text, _ = run(*"<think>ab</think>\ncd")
    assert (thinking, text) == ("ab", "cd")


def test_the_result_never_depends_on_where_the_text_was_cut():
    for whole in ("<think>x", "<think>x</think>", "<th", "<thinking>no</thinking>", "<think", "plain", "  <think>x</think>y", "</think>stray", "<think></think>", "",
                  "<think>a</think>\n\n b <think>c</think>", "<<think>x</think>", "<think>x</thinky</think>z"):
        expected = run(whole)[:2]
        for i in range(len(whole) + 1):
            assert run(whole[:i], whole[i:])[:2] == expected, (whole, i)


def test_every_character_is_either_kept_or_part_of_a_removed_tag():
    thinking, text, _ = run("<think>one</think>\n\ntwo")
    assert thinking + text == "onetwo"
    thinking, text, _ = run("plain <think>x</think>")
    assert thinking + text == "plain <think>x</think>"


def test_a_think_tag_that_is_not_at_the_start_is_just_text():
    thinking, text, _ = run("Hello <think>x</think> there")
    assert (thinking, text) == ("", "Hello <think>x</think> there")
    thinking, text, _ = run("Hello", " <think>x</think>")
    assert thinking == ""


def test_spaces_and_blank_lines_before_the_tag_are_allowed():
    thinking, text, _ = run("\n  <think>x</think>y")
    assert (thinking, text) == ("x", "y")
    thinking, text, _ = run("\n", " ", "<think>x</think>y")
    assert (thinking, text) == ("x", "y")


def test_text_that_only_looks_like_the_start_of_a_tag_is_kept_in_order():
    for whole in ("<div>hi</div>", "<thinking>no</thinking>", "<thx", "< think>", "<"):
        thinking, text, _ = run(whole)
        assert (thinking, text) == ("", whole), whole
    thinking, text, _ = run("<", "div>", "hi")
    assert (thinking, text) == ("", "<div>hi")


def test_a_start_that_is_never_completed_comes_back_as_text_at_the_end():
    f = ThinkTagFilter()
    assert f.feed("<thin") == []
    assert f.finish() == [("text", "<thin")]


def test_leading_spaces_are_kept_when_the_text_turns_out_to_be_plain():
    assert run("  ", "hello")[1] == "  hello"


def test_an_unclosed_thinking_stays_thinking():
    thinking, text, _ = run("<think>abc", "def")
    assert (thinking, text) == ("abcdef", "")


def test_a_closing_tag_without_an_opening_one_is_text():
    thinking, text, _ = run("answer </think> more")
    assert (thinking, text) == ("", "answer </think> more")


def test_a_second_think_block_after_the_first_is_text():
    thinking, text, _ = run("<think>a</think>b <think>c</think> d")
    assert (thinking, text) == ("a", "b <think>c</think> d")


def test_an_empty_thinking_gives_no_thinking_piece():
    thinking, text, raw = run("<think></think>answer")
    assert (thinking, text) == ("", "answer") and all(k == "text" for k, _ in raw)


def test_the_blank_lines_after_the_closing_tag_go_but_the_indentation_of_the_first_real_line_stays():
    assert run("<think>x</think>\n\n  indented")[1] == "  indented"
    assert run("<think>x</think>  \n \n    code")[1] == "    code"
    assert run("<think>x</think>\r\n\r\nHello")[1] == "Hello"
    assert run("<think>x</think>   ")[1] == "", "only blank space after the tag: nothing to show"
    assert run("<think>x</think>", "  ", "\n", "  y")[1] == "  y"


def test_a_closing_tag_cut_before_its_end_is_held_not_shown():
    f = ThinkTagFilter()
    assert f.feed("<think>abc</thi") == [("thinking", "abc")]
    assert f.feed("nk>x") == [("text", "x")]


def test_a_look_alike_of_the_closing_tag_inside_the_thinking_is_thinking():
    thinking, text, _ = run("<think>a</thinky>b</think>c")
    assert (thinking, text) == ("a</thinky>b", "c")


def test_no_piece_is_ever_empty():
    for pieces in (("<think>a</think>b",), ("<thi", "nk>", "x", "</think>", "y"), ("", "a", ""), ("<think>", "</think>", "z")):
        assert all(t != "" for _, t in run(*pieces)[2]), pieces


@pytest.mark.parametrize("junk", ["\x00", "\r\n", "é", "日本語", "<think>" * 3])
def test_odd_characters_do_not_break_it(junk):
    thinking, text, _ = run(junk + "a")
    assert thinking + text


def test_a_long_run_of_open_brackets_cannot_grow_without_bound():
    f = ThinkTagFilter()
    held = 0
    for _ in range(1000):
        f.feed("<<<<<<<<")
        held = max(held, len(f._held))
    assert held <= len("<think>") + 8


def test_a_flood_of_blank_space_after_the_closing_tag_is_answer_text_not_held_forever():
    f = ThinkTagFilter()
    out = f.feed("<think>x</think>")
    for _ in range(10):
        out += f.feed(" " * 30)
    out += f.finish()
    assert any(k == "text" for k, _ in out), "after a while the spaces are shown: they are the answer"


def test_a_cut_closing_tag_at_the_end_of_an_unclosed_thinking_stays_thinking():
    thinking, text, raw = run("<think>abc</th")
    assert (thinking, text) == ("abc</th", "")
    assert raw[-1] == ("thinking", "</th"), "what was held back comes out as thinking at the end"
