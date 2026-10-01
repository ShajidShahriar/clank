import pytest

from chunker import chunk_file, make_chunk
from test_invariants import fixture_paths

KEYS = {"kind", "symbol", "parent", "file_path", "rel_path", "start_line", "end_line",
        "text", "embed_text", "rel_path", "synthetic"}
LINES = ["def f():", "    return 1", "", "x = 2"]


def build(**overrides):
    args = dict(kind="function", symbol="f", parent=None, file_path="a.py",
                start_line=1, end_line=2, source_lines=LINES)
    args.update(overrides)
    return make_chunk(**args)


@pytest.mark.parametrize("missing", ["start_line", "end_line"])
def test_missing_line_number_raises(missing):
    with pytest.raises(ValueError):
        build(**{missing: None})


def test_text_defaults_to_verbatim_source_lines():
    assert build()["text"] == "def f():\n    return 1"
    assert build(start_line=2, end_line=4)["text"] == "    return 1\n\nx = 2"


def test_explicit_text_is_kept_and_embed_text_starts_empty():
    chunk = build(text="built summary", synthetic=True)
    assert chunk["text"] == "built summary"
    assert chunk["synthetic"] is True


def test_needs_source_lines_or_text():
    with pytest.raises(ValueError):
        build(source_lines=None)


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.name)
def test_every_chunk_has_the_same_shape(path):
    for chunk in chunk_file(str(path)):
        assert set(chunk) == KEYS


def test_embed_text_is_label_plus_verbatim_text():
    chunk = build(kind="method", symbol="increment", parent="Counter", rel_path="src/count.js")
    assert chunk["embed_text"] == "src/count.js · Counter.increment\n" + chunk["text"]
    assert chunk["text"] == "def f():\n    return 1"  # text itself never gets a prefix


def test_embed_text_label_for_plain_function_and_unnamed_chunk():
    assert build(rel_path="a.py")["embed_text"].startswith("a.py · f\n")
    assert build(kind="module_code", symbol=None, rel_path="a.py")["embed_text"].startswith("a.py · module_code\n")


def test_chunk_file_uses_path_relative_to_repo_root(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("class Counter:\n    def increment(self):\n        pass\n")
    chunks = chunk_file(str(tmp_path / "pkg" / "m.py"), repo_root=str(tmp_path))
    method = next(c for c in chunks if c["kind"] == "method")
    assert method["embed_text"] == "pkg/m.py · Counter.increment\n    def increment(self):\n        pass"
    assert "Inside" not in method["text"]
    overview = next(c for c in chunks if c["kind"] == "class_overview")
    assert overview["embed_text"].startswith("pkg/m.py · Counter\n")
