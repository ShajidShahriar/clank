"""A comment directly above a definition travels with it. A detached one does not."""
import pytest

from chunker import chunk_file

JS = b"""// detached note

/** Adds two numbers. */
function add(a, b) { return a + b; }

/** Class doc. */
class Counter {
  count = 0;

  /** Bumps the count. */
  increment() { this.count++; }

  reset() { this.count = 0; }
}
"""

PY = b'''x = 1  # trailing note
def first():
    pass

# explains second
def second():
    pass

class Config:
    """Config docs."""
    DEBUG = True

    # explains method
    def method(self):
        pass
'''


def chunks_of(tmp_path, name, source):
    path = tmp_path / name
    path.write_bytes(source)
    return chunk_file(str(path))


def one(chunks, **match):
    found = [c for c in chunks if all(c.get(k) == v for k, v in match.items())]
    assert len(found) == 1, f"expected one chunk matching {match}, got {len(found)}"
    return found[0]


def test_js_jsdoc_travels_with_function(tmp_path):
    chunks = chunks_of(tmp_path, "a.js", JS)
    add = one(chunks, symbol="add")
    assert add["text"].startswith("/** Adds two numbers. */\nfunction add")
    assert add["start_line"] == 3


def test_js_detached_comment_stays_module_level(tmp_path):
    chunks = chunks_of(tmp_path, "a.js", JS)
    assert "// detached note" not in one(chunks, symbol="add")["text"]
    assert "// detached note" in one(chunks, kind="module_code")["text"]


def test_js_jsdoc_not_duplicated_in_module_level(tmp_path):
    chunks = chunks_of(tmp_path, "a.js", JS)
    module_text = " ".join(c["text"] for c in chunks if c["kind"] == "module_level")
    assert "Adds two numbers" not in module_text


def test_js_class_jsdoc_in_overview(tmp_path):
    chunks = chunks_of(tmp_path, "a.js", JS)
    overview = one(chunks, kind="class_overview", symbol="Counter")
    assert overview["text"].startswith("/** Class doc. */\nclass Counter\n")
    assert overview["start_line"] == 6


def test_js_method_jsdoc_travels_with_method(tmp_path):
    chunks = chunks_of(tmp_path, "a.js", JS)
    assert one(chunks, symbol="increment")["text"].lstrip().startswith("/** Bumps the count. */")
    assert "Bumps" not in one(chunks, symbol="reset")["text"]
    assert "Bumps" not in one(chunks, kind="class_overview")["text"]


def test_py_comment_above_def_attached(tmp_path):
    chunks = chunks_of(tmp_path, "b.py", PY)
    assert one(chunks, symbol="second")["text"].startswith("# explains second\ndef second")


def test_py_trailing_comment_not_attached_to_next_def(tmp_path):
    chunks = chunks_of(tmp_path, "b.py", PY)
    assert "trailing note" not in one(chunks, symbol="first")["text"]
    assert "trailing note" in one(chunks, kind="module_code")["text"]


def test_py_class_docstring_in_overview(tmp_path):
    chunks = chunks_of(tmp_path, "b.py", PY)
    assert "Config docs." in one(chunks, kind="class_overview", symbol="Config")["text"]


def test_py_method_comment_attached(tmp_path):
    chunks = chunks_of(tmp_path, "b.py", PY)
    assert "# explains method" in one(chunks, symbol="method")["text"]
    assert "explains method" not in one(chunks, kind="class_overview")["text"]


def test_py_license_sized_comment_above_def_is_not_absorbed(tmp_path):
    header = "\n".join(f"# license line {i}" for i in range(40))
    chunks = chunks_of(tmp_path, "c.py", (header + "\ndef first():\n    pass\n").encode())
    assert "license" not in one(chunks, symbol="first")["text"]
    assert "license line 0" in one(chunks, kind="module_code")["text"]


def test_py_comment_just_under_the_cap_is_absorbed(tmp_path):
    block = "\n".join(f"# note {i}" for i in range(30))
    chunks = chunks_of(tmp_path, "c.py", (block + "\ndef first():\n    pass\n").encode())
    assert one(chunks, symbol="first")["text"].startswith("# note 0\n")


def test_js_tall_block_comment_above_function_is_not_absorbed(tmp_path):
    banner = "/*\n" + "\n".join(f" * line {i}" for i in range(40)) + "\n */"
    chunks = chunks_of(tmp_path, "c.js", (banner + "\nfunction f() {}\n").encode())
    assert "line 0" not in one(chunks, symbol="f")["text"]
