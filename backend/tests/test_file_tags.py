"""Task I-4.7: tag every file as test / changelog / language, from its path alone.

The chunker ignores these tags on purpose. They are stored in the `files` table so that retrieval can decide later,
using the eval set, whether to down-weight or hide tests and changelogs (in Express about 72% of chunks are one or the other).
Wrong tags are therefore cheap to fix later, but a tag that disagrees with itself between runs would not be.
"""
import pytest

import indexing
from embedding import FakeEmbedder
from file_discovery import SOURCE_EXTENSIONS
from indexing import classify_file, index_project
from indexing.fingerprint import chunker_source_files
from vectorstore import InMemoryVectorStore


@pytest.mark.parametrize("rel_path", [
    "tests/test_app.py", "backend/tests/fixtures/dummy/dummy_x.py", "test/express.js", "src/__tests__/a.js",
    "__mocks__/fs.js", "e2e/login.ts", "spec/models/user_spec.rb", "app.test.ts", "web/app.spec.tsx", "pkg/foo_test.go",
    "src/test/java/FooTest.java", "FooTests.java", "pkg/test_utils.py", "conftest.py", "tests/conftest.py", "lib/widget.test.js",
    "scripts/test_chunker.py", "src/a_test.py",
])
def test_these_are_tests(rel_path):
    assert classify_file(rel_path).is_test is True


@pytest.mark.parametrize("rel_path", [
    "src/app.py", "src/latest/contest.py", "src/attestation.py", "lib/protest.js", "docs/testing.md", "src/testing_helpers_doc.md",
    "backend/chunker/core.py", "README.md", "lib/response.js", "src/Contest.java", "src/greatest.go", "tests.md.bak.py",
    "src/latest_news.py", "src/fastest_path.py",
])
def test_these_are_not_tests(rel_path):
    assert classify_file(rel_path).is_test is False


@pytest.mark.parametrize("rel_path", [
    "CHANGELOG.md", "changelog.md", "History.md", "CHANGES.rst", "docs/changelog.md", "RELEASE_NOTES.txt", "release-notes.md",
    "ReleaseNotes.md", "NEWS", "docs/changes/123.feature.rst", "changelog/1.0.md", "newsfragments/42.bugfix.md",
])
def test_these_are_changelogs(rel_path):
    assert classify_file(rel_path).is_changelog is True


@pytest.mark.parametrize("rel_path", [
    "src/changes_applier.py", "docs/history_of_x.md", "lib/history.js", "src/changelog.py", "README.md", "docs/guide.md",
    "src/news_feed.ts", "docs/newspaper.md", "CHANGELOG_GENERATOR.py",
])
def test_these_are_not_changelogs(rel_path):
    assert classify_file(rel_path).is_changelog is False


@pytest.mark.parametrize("rel_path,language", [
    ("a.py", "python"), ("a.js", "javascript"), ("a.jsx", "javascript"), ("a.ts", "typescript"), ("a.tsx", "typescript"),
    ("a.md", "markdown"), ("a.json", "json"), ("a.yaml", "yaml"), ("a.yml", "yaml"), ("a.go", "go"), ("a.java", "java"),
    ("A.PY", "python"), ("dir.with.dots/a.py", "python"), ("Makefile", None), ("a.unknown", None), ("noext", None),
])
def test_language_comes_from_the_extension(rel_path, language):
    assert classify_file(rel_path).language == language


def test_every_extension_discovery_accepts_has_a_language_name():
    for ext in SOURCE_EXTENSIONS:
        assert classify_file(f"x{ext}").language, f"discovery indexes {ext} files but tags.py has no language for it"


def test_the_tags_are_a_plain_value_that_can_be_compared():
    assert classify_file("tests/test_a.py") == classify_file("tests/test_a.py")
    assert classify_file("tests/test_a.py") != classify_file("src/a.py")


def test_a_file_can_be_both_a_test_and_a_changelog_or_neither():
    both = classify_file("tests/changelog.md")
    assert both.is_test and both.is_changelog
    neither = classify_file("src/app.py")
    assert not neither.is_test and not neither.is_changelog


def test_the_tag_rules_are_part_of_the_fingerprint():
    # The shortcut skips unchanged files, so a file's stored tags would go stale if the rules changed but the fingerprint did not.
    assert "tags.py" in {p.name for p in chunker_source_files()}


# ---- stored with the file ----

def test_index_project_stores_the_tags_with_every_file(conn, tmp_path):
    root = tmp_path / "repo"
    for rel, text in {"src/app.py": "def f():\n    return 1\n", "tests/test_app.py": "def test_f():\n    assert True\n",
                      "CHANGELOG.md": "# Changes\n\n- first\n", "web/ui.ts": "export const a = 1;\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    index_project(conn, 1, root, FakeEmbedder(), InMemoryVectorStore())
    rows = {r["rel_path"]: (r["is_test"], r["is_changelog"], r["language"])
            for r in conn.execute("SELECT rel_path, is_test, is_changelog, language FROM files")}
    assert rows == {"src/app.py": (0, 0, "python"), "tests/test_app.py": (1, 0, "python"),
                    "CHANGELOG.md": (0, 1, "markdown"), "web/ui.ts": (0, 0, "typescript")}


def test_tags_are_kept_when_a_file_is_edited_and_a_second_run_changes_nothing(conn, tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, root, e, store)
    (root / "tests" / "test_a.py").write_text("def test_a():\n    assert 2\n")
    index_project(conn, 1, root, e, store)
    index_project(conn, 1, root, e, store)
    row = conn.execute("SELECT is_test, is_changelog, language FROM files").fetchone()
    assert tuple(row) == (1, 0, "python")


def test_changing_the_rules_retags_files_even_though_their_bytes_did_not_change(conn, tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "util.py").write_text("def f():\n    return 1\n")
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, root, e, store)
    assert conn.execute("SELECT is_test FROM files").fetchone()[0] == 0
    # A new rule says util.py is a test. In real life this is an edit to tags.py, which changes the fingerprint; simulate both.
    monkeypatch.setattr(indexing.index, "classify_file", lambda rel: indexing.tags.FileTags(True, False, "python"))
    monkeypatch.setattr(indexing.index, "chunker_fingerprint", lambda: "tags-v2")
    seen = e.text_count
    index_project(conn, 1, root, e, store)
    assert conn.execute("SELECT is_test FROM files").fetchone()[0] == 1 and e.text_count == seen
