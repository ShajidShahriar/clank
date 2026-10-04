"""Tag a file as test / changelog / language from its path alone.

The chunker ignores these tags on purpose. They are stored in the `files` table so retrieval can decide later, using the eval set,
whether to down-weight or hide tests and changelogs. Wrong tags are cheap to fix; they are only rules about names.
This file is part of the chunker fingerprint, so changing a rule here re-tags every file on the next run.
"""
import re
from pathlib import PurePosixPath
from typing import NamedTuple

TEST_DIRS = {"test", "tests", "__tests__", "__mocks__", "spec", "specs", "e2e", "testdata"}
TEST_FILE_PATTERNS = [
    re.compile(r"^test_"),                          # test_app.py
    re.compile(r"_test\.[A-Za-z]+$"),               # app_test.go
    re.compile(r"\.(test|spec)\.[A-Za-z]+$"),       # app.test.ts, app.spec.tsx
    re.compile(r"_spec\.rb$"),                      # user_spec.rb
    re.compile(r"Tests?\.(java|kt|cs)$"),           # FooTest.java (capital T, so Contest.java does not match)
    re.compile(r"^conftest\.py$"),
]

DOC_EXTENSIONS = {"", ".md", ".markdown", ".rst", ".txt"}
CHANGELOG_STEMS = {"changelog", "changes", "history", "news", "releases", "releasenotes", "whatsnew"}
CHANGELOG_DIRS = {"changelog", "changes", "newsfragments", "changelog.d"}

LANGUAGES = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".md": "markdown", ".markdown": "markdown", ".rst": "rst", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
}


class FileTags(NamedTuple):
    is_test: bool
    is_changelog: bool
    language: str | None


def classify_file(rel_path: str) -> FileTags:
    """`rel_path` is the posix path relative to the repo root."""
    path = PurePosixPath(rel_path)
    dirs = {part.lower() for part in path.parts[:-1]}
    name, ext = path.name, path.suffix.lower()
    is_test = bool(dirs & TEST_DIRS) or any(p.search(name) for p in TEST_FILE_PATTERNS)

    stem = re.sub(r"[^a-z0-9]", "", path.stem.lower())     # "RELEASE_NOTES" and "release-notes" both become "releasenotes"
    is_changelog = ext in DOC_EXTENSIONS and (stem in CHANGELOG_STEMS or bool(dirs & CHANGELOG_DIRS))
    if ext == "":                                           # NEWS has no extension, so its stem is the whole name
        is_changelog = is_changelog or re.sub(r"[^a-z0-9]", "", name.lower()) in CHANGELOG_STEMS
    return FileTags(is_test, is_changelog, LANGUAGES.get(ext))
