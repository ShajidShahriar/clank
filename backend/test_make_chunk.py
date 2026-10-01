import pytest

from chunker import chunk_file, make_chunk
from test_invariants import fixture_paths

KEYS = {"kind", "symbol", "parent", "file_path", "start_line", "end_line",
        "text", "embed_text", "header", "synthetic"}
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
    assert chunk["embed_text"] == ""


def test_needs_source_lines_or_text():
    with pytest.raises(ValueError):
        build(source_lines=None)


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.name)
def test_every_chunk_has_the_same_shape(path):
    for chunk in chunk_file(str(path)):
        assert set(chunk) == KEYS
