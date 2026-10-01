"""Chunker invariants. Written BEFORE changing the chunker: the failures are the to-do list.

CRLF is read as one line break (the chunker normalizes it to LF in chunk text).
Line convention (matches chunker.py today): start_line / end_line are 1-indexed and
inclusive, so a chunk's pointer covers source_lines[start_line - 1 : end_line]. If the
convention ever changes, change `pointed_lines` and nothing else.

Fixtures:
  - every dummy_* file next to this test
  - every file in fixtures/edge/ (JSDoc, module.exports, class-field arrows, stacked
    decorators, ...), drop new edge cases there and they are picked up automatically
  - a few generated inputs below (empty file, CRLF, syntax error)
"""
from pathlib import Path

import pytest

from chunker import chunk_file

HERE = Path(__file__).parent
SUPPORTED = {".py", ".js", ".jsx", ".md", ".json", ".yaml"}

GENERATED = {
    "empty.py": b"",
    "empty.js": b"",
    "crlf.py": b"import os\r\n\r\n\r\ndef f(x):\r\n    return x + 1\r\n\r\n\r\nclass A:\r\n    def m(self):\r\n        return 1\r\n",
    "crlf.js": b"function f(x) {\r\n  return x + 1;\r\n}\r\n\r\nclass A {\r\n  m() {\r\n    return 1;\r\n  }\r\n}\r\n",
    "syntax_error.py": b"import os\n\ndef broken(:\n    return 1\n\ndef fine():\n    return 2\n",
    "doc.md": b"# Title\n\nIntro.\n\n## Setup\n\n### Install\nRun it.\n\n```sh\n# not a heading\nmake\n```\n\nSetext\n======\nbody\n\n## Usage\nUse it.\n",
    "frontmatter.md": b"---\ntitle: x\n---\n\nbefore any heading\n\n# Real\ntext\n",
    "crlf.md": b"# A\r\n\r\ntext\r\n\r\n## B\r\nmore\r\n",
    "no_headings.md": b"just a paragraph\n\nand another\n",
    "a.json": b'{\n  "name": "x",\n  "scripts": {"test": "pytest"}\n}\n',
    "a.yaml": b"name: x\nitems:\n  - one\n  - two\n",
    "syntax_error.js": b"function broken( {\n  return 1;\n}\n\nfunction fine() {\n  return 2;\n}\n",
}


def fixture_paths():
    found = [p for p in HERE.glob("dummy_*") if p.suffix in SUPPORTED]
    edge_dir = HERE / "fixtures" / "edge"
    if edge_dir.is_dir():
        found += [p for p in edge_dir.iterdir() if p.suffix in SUPPORTED]
    return sorted(found)


@pytest.fixture(params=fixture_paths() + list(GENERATED), ids=lambda p: p if isinstance(p, str) else p.name)
def source_file(request, tmp_path):
    """Yields (path, source_bytes) for each fixture."""
    if isinstance(request.param, str):
        path = tmp_path / request.param
        path.write_bytes(GENERATED[request.param])
    else:
        path = request.param
    return path, path.read_bytes()


def run_chunker(path):
    try:
        return chunk_file(str(path))
    except Exception as e:  # a crash is a failure of its own, reported readably
        pytest.fail(f"chunk_file raised {type(e).__name__}: {e}")


def pointed_lines(source_lines, chunk):
    return "\n".join(source_lines[chunk["start_line"] - 1 : chunk["end_line"]])


def has_pointer(chunk):
    return chunk["start_line"] is not None and chunk["end_line"] is not None


def test_no_crashes_no_empties(source_file):
    path, source = source_file
    chunks = run_chunker(path)
    problems = []
    for i, c in enumerate(chunks):
        label = f"#{i} {c['kind']} {c['symbol']!r}"
        if not has_pointer(c):
            problems.append(f"{label}: null line numbers ({c['start_line']}, {c['end_line']})")
        if not c["text"] or not c["text"].strip():
            problems.append(f"{label}: empty text")
    assert not problems, "\n" + "\n".join(problems)


def test_pointer_truth(source_file):
    path, source = source_file
    source_lines = source.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    problems = []
    for i, c in enumerate(run_chunker(path)):
        if not has_pointer(c):
            continue  # reported by test_no_crashes_no_empties
        if c.get("synthetic"):
            continue  # built summary (class_overview), not source lines
        pointed = pointed_lines(source_lines, c)
        if pointed != c["text"]:
            problems.append(
                f"#{i} {c['kind']} {c['symbol']!r} lines {c['start_line']}-{c['end_line']}:\n"
                f"    text:    {c['text'][:70]!r}\n"
                f"    pointed: {pointed[:70]!r}"
            )
    assert not problems, "\n" + "\n".join(problems)


def test_coverage(source_file):
    """Every line holding a non-whitespace byte is inside some chunk's pointer range.

    Chunks are line ranges, and test_pointer_truth proves a range's text is the source,
    so line coverage here is byte coverage. Chunks with null pointers cover nothing.
    """
    path, source = source_file
    source_lines = source.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    covered = set()
    for c in run_chunker(path):
        if has_pointer(c):
            covered.update(range(c["start_line"], c["end_line"] + 1))
    uncovered = [
        f"line {n}: {line.strip()[:70]!r}"
        for n, line in enumerate(source_lines, start=1)
        if line.strip() and n not in covered
    ]
    assert not uncovered, "\n" + "\n".join(uncovered)
