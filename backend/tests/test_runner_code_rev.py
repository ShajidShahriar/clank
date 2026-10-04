"""Task I-7.13: a results file says which code made it: the commit of the Clank repo and whether the working tree had uncommitted changes."""
import subprocess

from eval.runner import git_dirty, git_head


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=t", *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "x")
    return tmp_path


def test_a_clean_repo_is_not_dirty(tmp_path):
    assert git_dirty(make_repo(tmp_path)) is False


def test_an_edited_or_a_new_file_makes_it_dirty(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "a.py").write_text("x = 2\n")
    assert git_dirty(repo) is True
    git(repo, "checkout", "--", "a.py")
    assert git_dirty(repo) is False
    (repo / "new.py").write_text("y = 1\n")
    assert git_dirty(repo) is True, "an untracked file counts too"


def test_a_folder_that_is_not_a_repo_has_no_answer(tmp_path):
    assert git_dirty(tmp_path / "nope") is None and git_head(tmp_path / "nope") is None
