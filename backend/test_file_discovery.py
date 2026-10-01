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
