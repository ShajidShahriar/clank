import re

import pytest

from chunker import chunk_file
from test_invariants import fixture_paths


def chunks_of(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source)
    return chunk_file(str(p), repo_root=str(tmp_path))


def by_symbol(chunks, symbol, kind=None):
    return [c for c in chunks if c["symbol"] == symbol and (kind is None or c["kind"] == kind)]


PY = '''import os

def first():
    return 1

def second():
    return 2

class Box:
    @property
    def value(self):
        return self._v

    @value.setter
    def value(self, v):
        self._v = v
'''


def test_ids_are_16_hex_chars_and_content_hash_too(tmp_path):
    for c in chunks_of(tmp_path, "a.py", PY):
        assert re.fullmatch(r"[0-9a-f]{16}", c["id"])
        assert re.fullmatch(r"[0-9a-f]{16}", c["content_hash"])


def test_chunking_the_same_file_twice_gives_identical_ids(tmp_path):
    first = chunks_of(tmp_path, "a.py", PY)
    second = chunks_of(tmp_path, "a.py", PY)
    assert [(c["id"], c["content_hash"]) for c in first] == [(c["id"], c["content_hash"]) for c in second]


def test_property_getter_and_setter_get_distinct_ids(tmp_path):
    values = by_symbol(chunks_of(tmp_path, "a.py", PY), "value", "method")
    assert len(values) == 2
    assert values[0]["id"] != values[1]["id"]
    assert [v["ordinal"] for v in values] == [0, 1]


def test_js_getter_and_setter_get_distinct_ids(tmp_path):
    src = "class B {\n  get value() { return 1; }\n  set value(v) { this.v = v; }\n}\n"
    values = by_symbol(chunks_of(tmp_path, "b.js", src), "value", "method")
    assert len(values) == 2 and values[0]["id"] != values[1]["id"]


def test_two_top_level_functions_with_the_same_name_get_distinct_ids(tmp_path):
    chunks = chunks_of(tmp_path, "a.py", "def f():\n    return 1\n\ndef f():\n    return 2\n")
    f = by_symbol(chunks, "f")
    assert len(f) == 2 and f[0]["id"] != f[1]["id"]


def test_editing_one_function_changes_only_its_content_hash(tmp_path):
    before = {c["id"]: c["content_hash"] for c in chunks_of(tmp_path, "a.py", PY)}
    after = {c["id"]: c["content_hash"] for c in chunks_of(tmp_path, "a.py", PY.replace("return 2", "return 22"))}
    assert before.keys() == after.keys()  # no id changed
    changed = [i for i in before if before[i] != after[i]]
    first_chunks = chunks_of(tmp_path, "a.py", PY.replace("return 2", "return 22"))
    assert changed == [by_symbol(first_chunks, "second")[0]["id"]]


def test_inserting_lines_above_changes_no_id_and_no_hash(tmp_path):
    before = chunks_of(tmp_path, "a.py", PY)
    after = chunks_of(tmp_path, "a.py", PY.replace("def first", "X = 1\n\ndef first", 1))
    key = lambda cs: {(c["symbol"], c["parent"]): (c["id"], c["content_hash"]) for c in cs if c["kind"] in ("function", "method")}
    assert key(before) == key(after)
    # ...even though every line number below the insert moved
    assert by_symbol(after, "second")[0]["start_line"] == by_symbol(before, "second")[0]["start_line"] + 2


def test_adding_a_new_function_leaves_existing_ids_alone(tmp_path):
    before = {(c["kind"], c["symbol"]): c["id"] for c in chunks_of(tmp_path, "a.py", PY) if c["kind"] == "function"}
    after = {(c["kind"], c["symbol"]): c["id"] for c in chunks_of(tmp_path, "a.py", "def brand_new():\n    pass\n\n" + PY) if c["kind"] == "function"}
    assert all(after[k] == v for k, v in before.items())


def test_same_code_in_a_different_file_gets_different_ids(tmp_path):
    (tmp_path / "pkg").mkdir()
    one = chunks_of(tmp_path, "a.py", PY)
    two = chunks_of(tmp_path, "pkg/a.py", PY)
    assert not {c["id"] for c in one} & {c["id"] for c in two}


def test_parts_of_a_split_chunk_have_distinct_stable_ids(tmp_path):
    body = "".join(f"    total += compute(item_{i}, 12345)\n" for i in range(200))
    src = "def big(a, b):\n    total = 0\n" + body + "    return total\n"
    first = chunks_of(tmp_path, "a.py", src)
    ids = [c["id"] for c in first]
    assert len(ids) > 3 and len(set(ids)) == len(ids)
    assert ids == [c["id"] for c in chunks_of(tmp_path, "a.py", src)]


def test_module_run_parts_and_import_runs_are_distinct(tmp_path):
    src = "".join(f"CONSTANT_NUMBER_{i} = {i} * 1000 + 17\n" for i in range(200)) + "import os\n"
    ids = [c["id"] for c in chunks_of(tmp_path, "m.py", src)]
    assert len(ids) > 2 and len(set(ids)) == len(ids)


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.name)
def test_ids_unique_within_every_fixture(path):
    ids = [c["id"] for c in chunk_file(str(path))]
    assert len(set(ids)) == len(ids)
