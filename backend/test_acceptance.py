"""Final acceptance check: the six promises, each run with grouping off and on."""
import logging
import pathlib

import pytest

import chunker
from chunker import MAX_CHUNK_TOKENS, chunk_file, estimate_tokens
from file_discovery import discover_files
from test_invariants import GENERATED, fixture_paths

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent
OLD_HEADER = "# In" + "side"  # built in two pieces so this file does not contain the marker itself
BLOB = ('BLOB = "' + "x" * 50_000 + '"\n').encode()
MINIFIED = ("".join(f"function f{i}(a){{return a*{i}+1}}var v{i}=f{i}({i});" for i in range(1500)) + "\n").encode()


@pytest.fixture(params=[False, True], ids=["ungrouped", "grouped"], autouse=True)
def grouping(request, monkeypatch):
    monkeypatch.setattr(chunker, "GROUP_SMALL_CHUNKS", request.param)


def every_file(tmp_path):
    """The real repo, every fixture, and the generated bad inputs, as (display name, path)."""
    found = [(str(p.relative_to(REPO)), p) for p in discover_files(str(REPO))]
    found += [(p.name, p) for p in fixture_paths()]
    for name, data in {**GENERATED, "blob.py": BLOB, "min.js": MINIFIED}.items():
        path = tmp_path / name
        path.write_bytes(data)
        found.append((name, path))
    return found


def test_1_discovery_returns_nothing_from_node_modules_without_a_gitignore(tmp_path):
    (tmp_path / "node_modules" / "pkg" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "lib" / "index.js").write_text("module.exports = 1;\n")
    (tmp_path / "node_modules" / "pkg" / "package.json").write_text("{}\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    assert not (tmp_path / ".gitignore").exists()
    assert [p.name for p in discover_files(str(tmp_path))] == ["app.py"]


def test_2_and_3_no_crash_and_no_none_line_numbers_anywhere(tmp_path):
    files = every_file(tmp_path)
    assert any(name.endswith("f.py") for name, _ in files)  # the invalid-UTF-8 fixture is in the pile
    for name, path in files:
        try:
            chunks = chunk_file(str(path))
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"{name} raised {type(e).__name__}: {e}")
        for c in chunks:
            assert c["start_line"] is not None and c["end_line"] is not None, (name, c["kind"], c["symbol"])


def test_4_text_is_exactly_the_files_lines_with_nothing_prepended(tmp_path):
    for name, path in every_file(tmp_path):
        source = path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
        for c in chunk_file(str(path)):
            assert OLD_HEADER not in c["text"], name
            if not c["synthetic"]:  # class overviews and cut-up lines are the two built texts
                assert c["text"] == "\n".join(source[c["start_line"] - 1:c["end_line"]]), (name, c["kind"], c["symbol"])


def test_4_the_class_context_lives_only_in_embed_text():
    method = next(c for c in chunk_file(str(HERE / "dummy_js_class_body.js")) if c["kind"] == "method")
    assert method["symbol"] == "increment" and method["parent"] == "Counter"
    assert method["text"] == "  increment() {\n    this.count++;\n  }"
    assert method["embed_text"] == "dummy_js_class_body.js · Counter.increment\n" + method["text"]
    for _, path in [(p.name, p) for p in fixture_paths()]:
        assert not any(OLD_HEADER in c["text"] for c in chunk_file(str(path)))


def test_5_the_jsdoc_lives_inside_adds_chunk():
    chunks = chunk_file(str(HERE / "dummy_js_jsdoc.js"))
    add = next(c for c in chunks if c["symbol"] == "add")
    assert add["text"] == "/**\n * Adds two numbers together.\n */\nfunction add(a, b) {\n  return a + b;\n}"
    assert add["start_line"] == 1
    assert not any("Adds two numbers" in c["text"] for c in chunks if c is not add)


@pytest.mark.parametrize("name,data", [("blob.py", BLOB), ("min.js", MINIFIED)])
def test_6_a_50kb_one_line_file_and_a_minified_file_stay_under_the_cap(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    chunks = chunk_file(str(path))
    assert 1 < len(chunks) < 200
    assert all(estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS for c in chunks)
