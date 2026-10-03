"""I-6 review finding #1: nothing checked whether the index still matches the files.

After editing or deleting files WITHOUT re-indexing, search kept showing the old code with the old line numbers, and offered files that no longer
exist, with no hint. The LLM would repeat `auth.py:1-5` to the user, who opens the file and finds something else: wrong, and silently so.

Now, for the files that appear in the results (at most k), the live file's hash is compared with the hash stored when it was indexed:
  changed  -> the passage stays, marked STALE in its header, and one note says the line numbers and code may be out of date;
  deleted  -> the passage is dropped BEFORE the budget is spent, and one note says so;
  same     -> nothing, exactly as before. (Content, not modification time: rewriting the same bytes is still fresh.)
"""
import pytest

import chunk_store
from embedding import FakeEmbedder
from indexing import index_project
from search import build_context, retrieve
from vectorstore import InMemoryVectorStore

FILES = {
    "auth.py": "def login(user):\n    return user.ok\n\n\ndef logout(user):\n    return None\n",
    "gone.py": "def removed_feature():\n    return 'bye'\n",
    "util.py": "def slugify(text):\n    return text.lower().replace(' ', '-')\n",
}


@pytest.fixture
def world(conn, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in FILES.items():
        (repo / name).write_text(text)
    e, store = FakeEmbedder(), InMemoryVectorStore()
    index_project(conn, 1, repo, e, store)
    return repo, e, store


def own_text(conn, rel, symbol=None):
    return next(r["embed_text"] for r in chunk_store.chunks_for_file(conn, 1, rel) if symbol is None or r["symbol"] == symbol)


def ask(conn, world, question, k=10, max_tokens=100_000):
    repo, e, store = world
    return build_context(conn, 1, repo, e, store, question, k=k, max_tokens=max_tokens)


def test_an_untouched_file_shows_no_mark_and_no_note(conn, world):
    ctx = ask(conn, world, own_text(conn, "auth.py", "login"))
    assert "STALE" not in ctx.text and "out of date" not in ctx.text and "no longer exist" not in ctx.text
    assert ctx.stale_files == [] and ctx.deleted_files == [] and not any(p.stale for p in ctx.passages)


def test_a_file_edited_after_indexing_is_marked_stale_and_the_note_names_it(conn, world):
    repo, *_ = world
    (repo / "auth.py").write_text("# 10 lines of new header\n" * 10 + FILES["auth.py"])         # every line number in the index is now wrong
    ctx = ask(conn, world, own_text(conn, "auth.py", "login"))
    header = next(line for line in ctx.text.split("\n") if line.startswith("### auth.py:"))
    assert "STALE" in header and header.startswith("### auth.py:1-2")                            # the OLD line numbers, honestly marked
    assert ctx.stale_files == ["auth.py"] and all(p.stale for p in ctx.passages if p.rel_path == "auth.py")
    note = ctx.text.split("[Notes]")[1]
    assert "changed after they were indexed" in note and "`auth.py`" in note and "re-index" in note.lower()
    assert not any(p.stale for p in ctx.passages if p.rel_path != "auth.py")


def test_rewriting_the_same_bytes_is_still_fresh(conn, world):
    repo, *_ = world
    (repo / "auth.py").write_text(FILES["auth.py"])                                               # a new modification time, identical content
    assert ask(conn, world, own_text(conn, "auth.py", "login")).stale_files == []


def test_a_deleted_file_is_dropped_before_the_budget_is_spent_and_the_note_says_so(conn, world):
    repo, *_ = world
    (repo / "gone.py").unlink()
    question = own_text(conn, "gone.py")                                                         # the deleted file would be the best match
    one_block = len(ask(conn, world, question, k=1, max_tokens=100_000).text) // 3
    ctx = ask(conn, world, question, k=10, max_tokens=one_block + 80)
    assert "gone.py" not in {p.rel_path for p in ctx.passages} | {p.rel_path for p in ctx.dropped}
    assert ctx.passages, "the budget must go to files that still exist, not to the deleted one"
    assert ctx.deleted_files == ["gone.py"] and "no longer exist" in ctx.text and "`gone.py`" in ctx.text.split("[Notes]")[1]
    assert "removed_feature" not in ctx.text.split("[Notes]")[0]


def test_only_files_in_the_results_are_checked(conn, world):
    repo, *_ = world
    (repo / "util.py").write_text("# edited\n" + FILES["util.py"])
    ctx = ask(conn, world, own_text(conn, "auth.py", "login"), k=1)                              # util.py is not among the results
    assert [p.rel_path for p in ctx.passages] == ["auth.py"] and ctx.stale_files == [] and "out of date" not in ctx.text


def test_two_passages_from_one_file_are_both_marked_and_the_file_is_listed_once(conn, world):
    repo, *_ = world
    (repo / "auth.py").write_text("# edited\n" + FILES["auth.py"])
    ctx = ask(conn, world, own_text(conn, "auth.py", "login"), k=10)
    assert sum(1 for p in ctx.passages if p.rel_path == "auth.py") >= 2
    assert all(p.stale for p in ctx.passages if p.rel_path == "auth.py") and ctx.stale_files == ["auth.py"]
    assert ctx.text.count("`auth.py`") == 1


def test_a_file_that_cannot_be_read_is_treated_as_stale_not_as_fine(conn, world, monkeypatch):
    from pathlib import Path
    real = Path.read_bytes

    def read_bytes(self):
        if self.name == "auth.py":
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    ctx = ask(conn, world, own_text(conn, "auth.py", "login"))
    assert ctx.stale_files == ["auth.py"] and ctx.deleted_files == []


def test_retrieve_reports_them_too(conn, world):
    repo, e, store = world
    (repo / "auth.py").write_text("# edited\n" + FILES["auth.py"])
    (repo / "gone.py").unlink()
    result = retrieve(conn, 1, repo, e, store, own_text(conn, "auth.py", "login"), k=10, max_tokens=100_000)
    assert result.stale_files == ["auth.py"] and result.deleted_files == ["gone.py"]
    assert "gone.py" not in {p.rel_path for p in result.passages}


@pytest.mark.parametrize("max_tokens", [80, 160, 300, 800, 3000])
def test_the_budget_still_counts_exactly_what_is_sent_with_the_new_notes(conn, world, max_tokens):
    from chunker.core import estimate_tokens
    repo, *_ = world
    (repo / "auth.py").write_text("# edited\n" + FILES["auth.py"])
    (repo / "gone.py").unlink()
    for question in (own_text(conn, "auth.py", "login"), own_text(conn, "gone.py"), "anything"):
        ctx = ask(conn, world, question, k=10, max_tokens=max_tokens)
        assert ctx.tokens_used == estimate_tokens(ctx.text)
        assert ctx.over_budget or ctx.tokens_used <= max_tokens


def test_a_changed_file_that_is_also_flagged_is_hidden_not_marked(conn, world):
    repo, e, store = world
    (repo / "auth.py").write_text("def login(user):\n    return '" + "x" * 400 + "'\n")
    index_project(conn, 1, repo, FakeEmbedder(max_chars=300), store)                              # too long: skipped and flagged
    ctx = ask(conn, world, own_text(conn, "auth.py", "login"))
    assert "auth.py" in ctx.hidden_files and "auth.py" not in {p.rel_path for p in ctx.passages}
    assert ctx.stale_files == []


def test_a_stale_file_whose_passage_did_not_fit_the_budget_is_not_mentioned(conn, world):
    repo, *_ = world
    (repo / "util.py").write_text("# edited\n" + FILES["util.py"])                                # stale, but ranked below auth.py
    question = own_text(conn, "auth.py", "login")
    one_block = len(ask(conn, world, question, k=1, max_tokens=100_000).text) // 3
    ctx = ask(conn, world, question, k=10, max_tokens=one_block + 30)
    assert "util.py" not in {p.rel_path for p in ctx.passages}
    assert ctx.stale_files == [] and "out of date" not in ctx.text and "util.py" not in ctx.text


def test_a_result_whose_path_leaves_the_repo_is_never_read_and_is_dropped(conn, world, tmp_path):
    from search import Passage, check_freshness
    repo, *_ = world
    (tmp_path / "outside.py").write_text("secret = 1\n")                                         # a real file, but not a file of this repo
    escaping = Passage(["x"], "../outside.py", "f", None, "function", 1, 1, "secret = 1", 0.5, "index", True, [])
    kept, stale, deleted = check_freshness(conn, 1, repo, [escaping])
    assert kept == [] and stale == [] and deleted == ["../outside.py"]
