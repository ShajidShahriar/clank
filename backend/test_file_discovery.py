import os

from file_discovery import discover_files


def make(root, *rel_paths):
    for rel in rel_paths:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")


def names(root):
    return sorted(str(p.relative_to(root)) for p in discover_files(str(root)))


def test_finds_normal_source_files(tmp_path):
    make(tmp_path, "a.py", "src/b.js")
    assert names(tmp_path) == ["a.py", "src/b.js"]


def test_lockfiles_ignored(tmp_path):
    make(tmp_path, "a.py", "package-lock.json", "web/package-lock.json", "pnpm-lock.yaml", "package.json")
    assert names(tmp_path) == ["a.py", "package.json"]


def test_hard_ignore_dirs_skipped_without_a_gitignore(tmp_path):
    make(tmp_path, "a.py", "node_modules/lib/index.js", "dist/out.js", "pkg/__pycache__/x.py")
    assert names(tmp_path) == ["a.py"]


def test_parent_folder_named_like_an_ignored_dir_is_fine(tmp_path):
    repo = tmp_path / "build" / "repo"
    make(repo, "a.py")
    assert names(repo) == ["a.py"]


def test_gitignore_still_applies(tmp_path):
    make(tmp_path, "a.py", "secret.py")
    (tmp_path / ".gitignore").write_text("secret.py\n")
    assert names(tmp_path) == ["a.py"]


def test_node_modules_never_entered_without_gitignore(tmp_path, monkeypatch):
    make(tmp_path, "a.py", "node_modules/deep/x/index.js")
    entered = []
    real_walk = os.walk

    def spy(top, *a, **k):
        for dirpath, dirnames, filenames in real_walk(top, *a, **k):
            entered.append(dirpath)
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(os, "walk", spy)
    assert names(tmp_path) == ["a.py"]
    # compare inside the repo only: pytest names tmp_path after this test, which contains "node_modules"
    assert not any("node_modules" in os.path.relpath(d, tmp_path) for d in entered)


def test_gitignored_directory_is_pruned(tmp_path):
    make(tmp_path, "a.py", "generated/out.py")
    (tmp_path / ".gitignore").write_text("generated/\n")
    assert names(tmp_path) == ["a.py"]


def test_minified_js_skipped(tmp_path):
    make(tmp_path, "app.js", "app.min.js")
    assert names(tmp_path) == ["app.js"]


def test_big_files_skipped(tmp_path):
    make(tmp_path, "small.py")
    (tmp_path / "big.py").write_text("x = 1\n" * 200_000)  # ~1.2 MB
    assert names(tmp_path) == ["small.py"]


def test_binary_files_skipped(tmp_path):
    make(tmp_path, "a.py")
    (tmp_path / "blob.py").write_bytes(b"x = 1\n\x00\x01\x02")
    assert names(tmp_path) == ["a.py"]
