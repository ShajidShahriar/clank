"""Neighbouring tiny chunks merge into `group` chunks. Grouping is off in other tests (conftest)."""
import pytest

import chunker
from chunker import MAX_CHUNK_TOKENS, MIN_MERGE_TOKENS, chunk_file, estimate_tokens
from test_invariants import GENERATED, fixture_paths


@pytest.fixture(autouse=True)
def grouping_on(monkeypatch):
    monkeypatch.setattr(chunker, "GROUP_SMALL_CHUNKS", True)


def chunks_of(tmp_path, name, source, grouped=True):
    p = tmp_path / name
    p.write_text(source)
    return chunk_file(str(p), repo_root=str(tmp_path))


def shape(chunks):
    return [(c["kind"], c["start_line"], c["end_line"]) for c in chunks]


UTILS = """import os
import sys

MAX_RETRIES = 3
TIMEOUT = 30

def helper_a():
    return 1

def helper_b():
    return 2
"""


def test_tiny_neighbours_become_one_group_listing_every_name(tmp_path):
    chunks = chunks_of(tmp_path, "utils.py", UTILS)
    assert shape(chunks) == [("imports", 1, 2), ("group", 4, 11)]
    group = chunks[1]
    assert group["names"] == ["MAX_RETRIES", "TIMEOUT", "helper_a", "helper_b"]
    assert group["embed_text"].startswith("utils.py · MAX_RETRIES, TIMEOUT, helper_a, helper_b\n")
    assert group["symbol"] is None and group["parent"] is None
    assert group["text"] == UTILS.split("\n", 3)[3].rstrip("\n")  # verbatim lines, blank lines kept


def test_imports_never_merge_with_code(tmp_path):
    chunks = chunks_of(tmp_path, "a.py", "import os\nX = 1\nimport sys\nY = 2\n")
    assert [c["kind"] for c in chunks] == ["imports", "module_code", "imports", "module_code"]


def test_adjacent_import_runs_stay_one_chunk_and_two_import_groups_do_not_form(tmp_path):
    chunks = chunks_of(tmp_path, "a.py", "import os\nimport sys\n\ndef f():\n    return 1\n\ndef g():\n    return 2\n")
    assert [c["kind"] for c in chunks] == ["imports", "group"]


def test_a_big_neighbour_ends_the_run(tmp_path):
    big = "def big():\n" + "".join(f"    value_{i} = compute_something({i}, 'some text here')\n" for i in range(40))
    src = "def a():\n    return 1\n\ndef b():\n    return 2\n\n" + big + "\ndef c():\n    return 3\n\ndef d():\n    return 4\n"
    chunks = chunks_of(tmp_path, "a.py", src)
    assert [c["kind"] for c in chunks] == ["group", "function", "group"]
    assert chunks[1]["symbol"] == "big"
    assert chunks[0]["names"] == ["a", "b"] and chunks[2]["names"] == ["c", "d"]


def test_nothing_merges_across_a_class(tmp_path):
    src = "def a():\n    return 1\n\ndef b():\n    return 2\n\nclass K:\n    x = 1\n\ndef c():\n    return 3\n\ndef d():\n    return 4\n"
    chunks = chunks_of(tmp_path, "a.py", src)
    top = [c for c in chunks if c["parent"] is None]
    assert [c["kind"] for c in top] == ["group", "class_overview", "group"]
    assert top[0]["names"] == ["a", "b"] and top[2]["names"] == ["c", "d"]


def test_small_methods_of_one_class_group_but_stay_inside_that_class(tmp_path):
    src = "class A:\n    def one(self):\n        return 1\n\n    def two(self):\n        return 2\n\nclass B:\n    def three(self):\n        return 3\n"
    chunks = chunks_of(tmp_path, "a.py", src)
    groups = [c for c in chunks if c["kind"] == "group"]
    assert len(groups) == 1
    assert groups[0]["parent"] == "A" and groups[0]["names"] == ["A.one", "A.two"]
    assert any(c["kind"] == "method" and c["symbol"] == "three" for c in chunks)  # alone, not grouped


def test_group_stops_at_the_cap(tmp_path):
    src = "".join(f"def f{i}():\n    return {'x' * 150}\n\n" for i in range(30))
    chunks = chunks_of(tmp_path, "a.py", src)
    groups = [c for c in chunks if c["kind"] == "group"]
    assert len(groups) > 1
    assert all(estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS for c in groups)
    assert sorted(n for g in groups for n in g["names"]) == sorted(f"f{i}" for i in range(30))


def test_only_pieces_under_the_minimum_merge(tmp_path):
    medium = "def medium():\n" + "".join(f"    step_{i}(argument_number_{i})\n" for i in range(14))
    assert MIN_MERGE_TOKENS <= estimate_tokens(medium) < MAX_CHUNK_TOKENS
    chunks = chunks_of(tmp_path, "a.py", "def a():\n    return 1\n\n" + medium + "\ndef b():\n    return 2\n")
    assert [c["kind"] for c in chunks] == ["function", "function", "function"]


def test_comments_between_members_are_kept_in_the_group_text(tmp_path):
    src = "X = 1\n\n# section two\n\nY = 2\n"
    chunks = chunks_of(tmp_path, "a.py", src)
    assert [c["kind"] for c in chunks] == ["module_code"]  # one run already; nothing to merge
    src = "def a():\n    return 1\n\n# note about b\ndef b():\n    return 2\n"
    group = chunks_of(tmp_path, "b.py", src)[0]
    assert group["kind"] == "group" and "# note about b" in group["text"]


def test_group_id_is_stable_when_a_later_member_changes(tmp_path):
    before = chunks_of(tmp_path, "a.py", "def a():\n    return 1\n\ndef b():\n    return 2\n")[0]
    after = chunks_of(tmp_path, "a.py", "def a():\n    return 1\n\ndef b():\n    return 22\n")[0]
    assert before["id"] == after["id"] and before["content_hash"] != after["content_hash"]


def test_parse_error_files_are_never_grouped(tmp_path):
    chunks = chunks_of(tmp_path, "a.py", "def a():\n    return 1\n\ndef broken(:\n    pass\n")
    assert [c["kind"] for c in chunks] == ["text_fallback"]


# The same three invariants as test_invariants.py, with grouping on, over every fixture.
@pytest.fixture(params=fixture_paths() + list(GENERATED), ids=lambda p: p if isinstance(p, str) else p.name)
def grouped_chunks(request, tmp_path):
    if isinstance(request.param, str):
        path = tmp_path / request.param
        path.write_bytes(GENERATED[request.param])
    else:
        path = request.param
    source = path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    return source, chunk_file(str(path))


def test_invariants_hold_with_grouping_on(grouped_chunks):
    source, chunks = grouped_chunks
    covered = set()
    for c in chunks:
        assert c["start_line"] is not None and c["end_line"] is not None and c["text"].strip()
        covered.update(range(c["start_line"], c["end_line"] + 1))
        if not c["synthetic"]:
            assert c["text"] == "\n".join(source[c["start_line"] - 1:c["end_line"]]), (c["kind"], c["symbol"])
    assert [n for n, line in enumerate(source, 1) if line.strip() and n not in covered] == []
    assert len({c["id"] for c in chunks}) == len(chunks)
