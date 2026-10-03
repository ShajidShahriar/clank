"""Task I-6.2: sibling expansion. A hit that is one part of a split chunk should reach the LLM as the WHOLE chunk, in order.

The four shapes of split parts (checked on real chunker output):
  statement splits   contiguous lines (1-51, 52-101, 102-121): join them;
  line-window splits OVERLAPPING lines (part 0 ends at 90, part 1 starts at 86): drop the repeated lines;
  class overviews    synthetic, every part points at the whole class and repeats the class header: keep the header once;
  character cuts     synthetic pieces of ONE long line: cannot be joined from the rows, read the live line instead.
Integrity rules: all parts must be present (0..part_count-1) and consistent, else the lines are read from the LIVE file by pointer, but ONLY if
the file's hash still equals the indexed one (a pointer into a changed file is a guess). If that is impossible too, return what exists with an
explicit marker for what is missing: never a quietly shorter function.
"""
import hashlib

import pytest

import chunk_store
from embedding import FakeEmbedder
from indexing import index_project
from search import Hit, expand
from search.stitch import read_live_lines
from vectorstore import InMemoryVectorStore

STATEMENTS = "def big():\n" + "\n".join(f"    x{i} = compute({i}) + other_function_name({i})" for i in range(120)) + "\n"
BIGDATA = "DATA = [\n" + "\n".join(f"    ({i}, 'value-{i}', {i * 7})," for i in range(400)) + "\n]\n"
CLASS = "class Big:\n    '''A class with many methods.'''\n    flag = True\n" + "\n".join(f"    def method_{i}(self, a, b):\n        return a + b + {i}\n" for i in range(150))
LONG_LINE = 'X = "' + "a" * 6000 + '"\n'
SMALL = "def small():\n    return 1\n"


@pytest.fixture
def world(conn, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in {"stmts.py": STATEMENTS, "bigdata.py": BIGDATA, "cls.py": CLASS, "line.py": LONG_LINE, "small.py": SMALL}.items():
        (repo / name).write_text(text)
    index_project(conn, 1, repo, FakeEmbedder(), InMemoryVectorStore())
    return repo


def rows(conn, rel, kind=None):
    return [r for r in chunk_store.chunks_for_file(conn, 1, rel) if kind is None or r["kind"] == kind]


def parts(conn, rel, kind=None):
    return [r for r in rows(conn, rel, kind) if r["part_count"]]


def lines_of(repo, rel, start, end):
    return "\n".join((repo / rel).read_text().split("\n")[start - 1:end])


def expand_one(conn, repo, row, score=0.9):
    (passage,) = expand(conn, 1, repo, [Hit(row, score)])
    return passage


# ---- the four shapes ----

def test_a_hit_on_the_middle_part_of_a_statement_split_returns_the_whole_function_in_order(conn, world):
    ps = parts(conn, "stmts.py")
    assert [p["part"] for p in ps] == [0, 1, 2] and not any(p["synthetic"] for p in ps)
    passage = expand_one(conn, world, ps[1])
    assert passage.text == lines_of(world, "stmts.py", 1, 121) and (passage.start_line, passage.end_line) == (1, 121)
    assert passage.chunk_ids == [p["id"] for p in ps] and passage.complete and passage.source == "index" and passage.missing == []
    assert passage.rel_path == "stmts.py" and passage.symbol == "big" and passage.kind == "function"


def test_overlapping_line_windows_are_stitched_without_repeating_lines(conn, world):
    ps = parts(conn, "bigdata.py")
    assert len(ps) == 6 and ps[1]["start_line"] <= ps[0]["end_line"]          # the parts really overlap
    passage = expand_one(conn, world, ps[3])
    assert passage.text == lines_of(world, "bigdata.py", 1, 402)              # every line exactly once, none missing
    assert passage.complete and passage.source == "index" and len(passage.chunk_ids) == 6


def test_class_overview_parts_are_stitched_with_the_header_once_and_every_member_once(conn, world):
    ps = parts(conn, "cls.py", "class_overview")
    assert len(ps) == 2 and all(p["synthetic"] for p in ps)
    passage = expand_one(conn, world, ps[1])
    assert passage.text.count("class Big:") == 1 and passage.text.startswith("class Big:")
    for i in range(150):
        assert passage.text.count(f"def method_{i}(self, a, b)") == 1, f"method_{i} should appear exactly once"
    assert passage.complete and passage.source == "index" and passage.kind == "class_overview"


def test_character_cut_pieces_are_read_from_the_live_line(conn, world):
    ps = parts(conn, "line.py")
    assert len(ps) == 3 and all(p["synthetic"] for p in ps)
    passage = expand_one(conn, world, ps[1])
    assert passage.text == LONG_LINE.rstrip("\n") and passage.complete and passage.source == "file"


def test_a_whole_chunk_is_returned_as_it_is(conn, world):
    row = rows(conn, "small.py")[0]
    passage = expand_one(conn, world, row)
    assert passage.text == row["text"] and passage.chunk_ids == [row["id"]] and passage.complete and passage.source == "index"


# ---- several hits ----

def test_hits_on_two_parts_of_one_function_become_one_passage_with_the_best_score(conn, world):
    ps = parts(conn, "stmts.py")
    result = expand(conn, 1, world, [Hit(ps[2], 0.7), Hit(ps[0], 0.9)])
    assert len(result) == 1 and result[0].score == 0.9 and result[0].chunk_ids == [p["id"] for p in ps]


def test_passages_keep_the_rank_order_of_their_best_hit(conn, world):
    small, ps = rows(conn, "small.py")[0], parts(conn, "stmts.py")
    result = expand(conn, 1, world, [Hit(small, 0.95), Hit(ps[1], 0.8), Hit(ps[0], 0.6)])
    assert [p.rel_path for p in result] == ["small.py", "stmts.py"] and [p.score for p in result] == [0.95, 0.8]


def test_no_hits_no_passages(conn, world):
    assert expand(conn, 1, world, []) == []


# ---- integrity: a part is missing ----

def forget_part(conn, row):
    conn.execute("DELETE FROM chunks WHERE project_id = 1 AND id = ?", (row["id"],))
    conn.commit()


def test_a_missing_middle_part_is_filled_from_the_live_file_by_pointer(conn, world):
    ps = parts(conn, "stmts.py")
    forget_part(conn, ps[1])
    passage = expand_one(conn, world, ps[0])
    assert passage.text == lines_of(world, "stmts.py", 1, 121) and passage.source == "file" and passage.complete
    assert passage.chunk_ids == [ps[0]["id"], ps[2]["id"]] and passage.missing == [1]


def test_a_missing_first_part_cannot_be_rebuilt_and_says_so(conn, world):
    ps = parts(conn, "stmts.py")
    forget_part(conn, ps[0])                              # the start of the function is unknown: its pointer died with the row
    passage = expand_one(conn, world, ps[1])
    assert not passage.complete and passage.missing == [0] and "part 1 of 3 not available" in passage.text
    assert lines_of(world, "stmts.py", 52, 121) in passage.text and passage.source == "index"


def test_a_missing_last_part_cannot_be_rebuilt_and_says_so(conn, world):
    ps = parts(conn, "stmts.py")
    forget_part(conn, ps[2])
    passage = expand_one(conn, world, ps[1])
    assert not passage.complete and passage.missing == [2] and passage.text.rstrip().endswith("part 3 of 3 not available ...]")


def test_a_changed_file_is_not_trusted_for_pointers(conn, world):
    ps = parts(conn, "stmts.py")
    forget_part(conn, ps[1])
    (world / "stmts.py").write_text("# a new first line shifts everything\n" + STATEMENTS)       # edited since indexing
    passage = expand_one(conn, world, ps[0])
    assert not passage.complete and passage.source == "index" and passage.missing == [1]
    assert "part 2 of 3 not available" in passage.text and "# a new first line" not in passage.text   # nothing from the changed file leaks in


def test_a_deleted_file_is_not_a_crash(conn, world):
    ps = parts(conn, "line.py")
    (world / "line.py").unlink()
    passage = expand_one(conn, world, ps[1])
    assert not passage.complete and passage.source == "index"      # the pieces cannot be joined and there is no file to read: say so


def test_inconsistent_overlap_falls_back_to_the_file_instead_of_guessing(conn, world):
    ps = parts(conn, "bigdata.py")
    conn.execute("UPDATE chunks SET text = ? WHERE project_id = 1 AND id = ?", ("not the lines the pointer says", ps[2]["id"]))
    conn.commit()
    passage = expand_one(conn, world, ps[1])
    assert passage.source == "file" and passage.text == lines_of(world, "bigdata.py", 1, 402) and passage.complete


# ---- reading the live file safely ----

def test_live_lines_are_read_like_the_chunker_read_them(tmp_path):
    (tmp_path / "w.py").write_bytes(b"a = 1\r\nb = '\xff'\r\nc = 3\r\n")
    assert read_live_lines(tmp_path, "w.py", 1, 3) == "a = 1\nb = '�'\nc = 3"      # CRLF -> LF, bad bytes -> U+FFFD, no trailing newline


@pytest.mark.parametrize("rel", ["../outside.py", "/etc/passwd", "sub/../../outside.py"])
def test_a_path_that_leaves_the_repo_is_refused(tmp_path, rel):
    (tmp_path / "outside.py").write_text("secret\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert read_live_lines(repo, rel, 1, 1) is None


def test_the_live_hash_is_compared_with_the_stored_one(conn, world):
    stored = chunk_store.file_hashes(conn, 1)["stmts.py"]
    assert stored == hashlib.sha1((world / "stmts.py").read_bytes()).hexdigest()


# Three integrity checks guard three DIFFERENT lies. Each test below builds a lie that ONLY its check can see, because the checks cover for each
# other otherwise (found by mutation: removing any one of them left the first versions of these tests green).

def test_a_part_whose_text_has_a_line_too_many_is_not_trusted(conn, world):
    ps = parts(conn, "stmts.py")                           # pointers contiguous, no overlap to compare: only "text lines == pointer span" can notice
    conn.execute("UPDATE chunks SET text = text || char(10) || 'an extra line' WHERE project_id = 1 AND id = ?", (ps[1]["id"],))
    conn.commit()
    passage = expand_one(conn, world, ps[0])
    assert passage.source == "file" and passage.text == lines_of(world, "stmts.py", 1, 121) and "an extra line" not in passage.text


def test_overlapping_lines_that_are_not_a_repeat_are_not_trusted(conn, world):
    ps = parts(conn, "bigdata.py")                         # same number of lines, pointers fine: only "the overlap repeats the previous tail" can notice
    tampered = ["TAMPERED"] + ps[1]["text"].split("\n")[1:]
    conn.execute("UPDATE chunks SET text = ? WHERE project_id = 1 AND id = ?", ("\n".join(tampered), ps[1]["id"]))
    conn.commit()
    passage = expand_one(conn, world, ps[2])
    assert passage.source == "file" and passage.text == lines_of(world, "bigdata.py", 1, 402) and "TAMPERED" not in passage.text


def test_a_gap_before_the_last_part_is_not_papered_over(conn, world):
    ps = parts(conn, "stmts.py")                           # the last part starts 3 lines late and its text is trimmed to match: no overlap, nothing after it
    trimmed = "\n".join(ps[2]["text"].split("\n")[3:])
    conn.execute("UPDATE chunks SET start_line = start_line + 3, text = ? WHERE project_id = 1 AND id = ?", (trimmed, ps[2]["id"]))
    conn.commit()
    passage = expand_one(conn, world, ps[0])
    assert passage.source == "file" and passage.text == lines_of(world, "stmts.py", 1, 121) and passage.complete
