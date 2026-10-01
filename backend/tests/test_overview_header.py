"""The overview's first line is the class line as written, bases included."""
from chunker import chunk_file


def overview_of(tmp_path, name, source, symbol):
    p = tmp_path / name
    p.write_text(source)
    return next(c for c in chunk_file(str(p)) if c["kind"] == "class_overview" and c["symbol"] == symbol)


def first_line(chunk):
    return chunk["text"].split("\n")[0]


def test_python_bases_are_kept_including_generics(tmp_path):
    src = "class SessionMixin(MutableMapping[str, t.Any]):\n    permanent = False\n"
    assert first_line(overview_of(tmp_path, "a.py", src, "SessionMixin")) == "class SessionMixin(MutableMapping[str, t.Any]):"


def test_python_multiple_and_keyword_bases(tmp_path):
    src = "class Both(Base, Mixin, metaclass=Meta):\n    x = 1\n"
    assert first_line(overview_of(tmp_path, "a.py", src, "Both")) == "class Both(Base, Mixin, metaclass=Meta):"


def test_python_multiline_bases_are_kept_verbatim(tmp_path):
    src = "class Wide(\n    First,\n    Second,\n):\n    x = 1\n"
    assert overview_of(tmp_path, "a.py", src, "Wide")["text"].startswith("class Wide(\n    First,\n    Second,\n):\n")


def test_python_decorator_and_nested_class_keep_their_lines(tmp_path):
    src = "@dataclass(frozen=True)\nclass Point(Base):\n    x: int = 0\n\n    class Meta(Other):\n        pass\n"
    assert overview_of(tmp_path, "a.py", src, "Point")["text"].startswith("@dataclass(frozen=True)\nclass Point(Base):\n")
    inner = overview_of(tmp_path, "a.py", src, "Meta")
    assert inner["parent"] == "Point" and first_line(inner) == "class Meta(Other):"


def test_js_extends_is_kept(tmp_path):
    src = "class Counter extends Base {\n  count = 0;\n}\n"
    assert first_line(overview_of(tmp_path, "a.js", src, "Counter")) == "class Counter extends Base"


def test_js_export_default_and_member_expression_extends(tmp_path):
    src = "export default class App extends React.Component {\n  render() { return 1; }\n}\n"
    assert first_line(overview_of(tmp_path, "a.js", src, "App")) == "export default class App extends React.Component"


def test_class_without_bases_is_unchanged(tmp_path):
    assert first_line(overview_of(tmp_path, "a.py", "class Plain:\n    x = 1\n", "Plain")) == "class Plain:"
    assert first_line(overview_of(tmp_path, "a.js", "class Plain {\n  x = 1;\n}\n", "Plain")) == "class Plain"


# --- a big overview splits into parts, and every part starts with the class line -------------

import pytest

from chunker import MAX_CHUNK_TOKENS, estimate_tokens


def overview_parts(tmp_path, name, source, symbol):
    p = tmp_path / name
    p.write_text(source)
    return [c for c in chunk_file(str(p)) if c["kind"] == "class_overview" and c["symbol"] == symbol]


def big_python_class(methods=120):
    body = "".join(f"    def method_number_{i}(self, first_argument, second_argument=None):\n        return {i}\n\n" for i in range(methods))
    return "@total_ordering\nclass Session(SessionRedirectMixin, Base):\n    \"\"\"A big session.\"\"\"\n    timeout = 30\n\n" + body


def test_every_part_of_a_big_python_overview_starts_with_the_class_header(tmp_path):
    parts = overview_parts(tmp_path, "a.py", big_python_class(), "Session")
    assert len(parts) > 1
    header = "@total_ordering\nclass Session(SessionRedirectMixin, Base):\n"
    assert all(p["text"].startswith(header) for p in parts)
    assert all(estimate_tokens(p["text"]) <= MAX_CHUNK_TOKENS for p in parts)
    assert [p["part"] for p in parts] == list(range(len(parts)))
    assert all(p["symbol"] == "Session" and p["part_count"] == len(parts) for p in parts)


def test_split_overview_loses_and_repeats_no_member(tmp_path):
    parts = overview_parts(tmp_path, "a.py", big_python_class(), "Session")
    text = "\n".join(p["text"] for p in parts)
    for i in range(120):
        assert text.count(f"def method_number_{i}(") == 1  # present once: not dropped, not duplicated
    assert "A big session." in parts[0]["text"] and "timeout = 30" in parts[0]["text"]
    # no part begins in the middle of the signature list: after the header comes a real member
    for p in parts:
        assert p["text"].split("\n")[2].startswith("    ")


def test_split_overview_parts_point_at_the_whole_class(tmp_path):
    parts = overview_parts(tmp_path, "a.py", big_python_class(), "Session")
    assert len({(p["start_line"], p["end_line"]) for p in parts}) == 1
    assert len({p["id"] for p in parts}) == len(parts)
    assert parts[1]["embed_text"].startswith(f"a.py · Session (part 2/{len(parts)})\n@total_ordering")


def test_js_big_overview_parts_keep_extends(tmp_path):
    fields = "".join(f"  field_number_{i} = 'a fairly long default value for field {i}';\n" for i in range(150))
    parts = overview_parts(tmp_path, "a.js", f"class Big extends Base {{\n{fields}}}\n", "Big")
    assert len(parts) > 1
    assert all(p["text"].startswith("class Big extends Base\n") for p in parts)
    assert all(estimate_tokens(p["text"]) <= MAX_CHUNK_TOKENS for p in parts)


def test_one_huge_member_still_gets_the_header_and_stays_under_the_cap(tmp_path):
    src = "class Table(Base):\n    DATA = {" + ", ".join(f"'key_{i}': {i}" for i in range(2000)) + "}\n\n    def go(self):\n        pass\n"
    parts = overview_parts(tmp_path, "a.py", src, "Table")
    assert len(parts) > 2
    assert all(p["text"].startswith("class Table(Base):\n") for p in parts)
    assert all(estimate_tokens(p["text"]) <= MAX_CHUNK_TOKENS for p in parts)
    assert any("def go(self)" in p["text"] for p in parts)


def test_small_overview_is_still_one_chunk(tmp_path):
    parts = overview_parts(tmp_path, "a.py", "class Tiny(Base):\n    x = 1\n\n    def go(self):\n        pass\n", "Tiny")
    assert len(parts) == 1 and parts[0]["part"] is None
