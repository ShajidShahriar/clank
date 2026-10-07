"""Task: clickable sources, the backend half: `POST /projects/{id}/source` returns the lines of one file of the project, for a read-only viewer.

This is the one place where the window can make the backend read a file chosen by a path, so it is strict. What is promised:
- ONLY a file the index knows (an exact `rel_path` of the project's `files` table) can be read. `..`, an absolute path, `.env`, `.git/config`, a file that was
  never indexed, a file of ANOTHER project: all refused, with the SAME 404 `file_not_available` and the SAME message (no way to ask "does this exist?");
- the real file (symlinks resolved) must lie inside the project's folder: an indexed file later replaced by a symlink to somewhere else, or reached through
  a symlinked folder, is refused like any other;
- only a regular text file: a folder, a binary file (a NUL byte at the start) and a file over 1 MB are refused (413 `file_too_big`, 415 `file_not_text`);
- the range is validated, never clamped: whole numbers, start at least 1, end not before start, at most MAX_VIEW_LINES lines (else 422); an end past the
  end of the file is cut to the real end and reported; a start past the end is 409 `file_changed`;
- the answer says whether the file changed since it was indexed (`stale`), carries the real line count, and cuts a very long line with a marker;
- no message or field of an error repeats the path the caller sent; the project's folder missing is 409 `repo_not_found`; the project must exist (404).
"""
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

import source_view
from app_for_tests import create_app
from embedding import FakeEmbedder
from jobs import IndexJobs
from services import Services
from vectorstore import InMemoryVectorStore


def sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


@pytest.fixture
def repo(conn, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    conn.execute("UPDATE projects SET repo_path = ? WHERE id = 1", (str(root),))
    conn.commit()
    return root


def index(conn, root, rel, data: bytes, project=1):
    """Write the file and give it an index row with its real hash, as an index run would."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    conn.execute("INSERT OR REPLACE INTO files (project_id, rel_path, hash) VALUES (?, ?, ?)", (project, rel, sha(data)))
    conn.commit()


NUMBERED = b"".join(f"line {i}\n".encode() for i in range(1, 101))        # 100 lines: "line 1" ... "line 100"


def read(conn, rel, start=1, end=5, project=1):
    return source_view.read_source(conn, project, rel, start, end)


# ---- the happy path

def test_the_asked_lines_come_back_with_their_numbers(conn, repo):
    index(conn, repo, "src/a.py", NUMBERED)
    out = read(conn, "src/a.py", 10, 12)
    assert out["lines"] == ["line 10", "line 11", "line 12"]
    assert (out["start_line"], out["end_line"], out["total_lines"], out["stale"], out["path"]) == (10, 12, 100, False, "src/a.py")


def test_a_file_without_a_final_newline_and_with_windows_line_ends(conn, repo):
    index(conn, repo, "a.txt", b"one\r\ntwo\r\nthree")
    assert read(conn, "a.txt", 1, 3)["lines"] == ["one", "two", "three"]
    assert read(conn, "a.txt", 1, 3)["total_lines"] == 3


def test_an_end_past_the_end_of_the_file_is_cut_and_reported(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    out = read(conn, "a.py", 99, 150)
    assert out["lines"] == ["line 99", "line 100"] and out["end_line"] == 100 and out["total_lines"] == 100


def test_the_last_line_can_be_asked_for_alone(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    out = read(conn, "a.py", 100, 100)
    assert out["lines"] == ["line 100"] and out["start_line"] == out["end_line"] == 100


def test_a_start_past_the_end_of_the_file_means_the_file_changed(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    with pytest.raises(source_view.SourceChanged):
        read(conn, "a.py", 101, 105)


def test_a_changed_file_is_marked_stale_and_the_live_lines_are_returned(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    (repo / "a.py").write_bytes(b"new first\n" + NUMBERED)
    out = read(conn, "a.py", 1, 2)
    assert out["stale"] is True and out["lines"] == ["new first", "line 1"]


def test_rewriting_the_same_bytes_is_not_stale(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    (repo / "a.py").write_bytes(NUMBERED)
    assert read(conn, "a.py")["stale"] is False


def test_invalid_utf8_is_shown_with_replacement_marks_not_a_crash(conn, repo):
    index(conn, repo, "a.py", b"caf\xe9\nok\n")
    assert read(conn, "a.py", 1, 2)["lines"] == ["caf\ufffd", "ok"]


def test_a_very_long_line_is_cut_with_a_marker(conn, repo):
    index(conn, repo, "min.js", b"x" * (source_view.MAX_LINE_CHARS * 3) + b"\nshort\n")
    first, second = read(conn, "min.js", 1, 2)["lines"]
    assert first.startswith("x" * 100) and first.endswith("…") and len(first) == source_view.MAX_LINE_CHARS + 1
    assert second == "short"


# ---- what may be read

@pytest.mark.parametrize("rel", ["../outside.py", "src/../../outside.py", "/etc/passwd", "~/x.py", "", "src/", ".", "a\x00b.py", "C:\\x.py", "src//a.py", "./src/a.py"])
def test_a_path_that_is_not_an_exact_indexed_path_is_refused(conn, repo, tmp_path, rel):
    index(conn, repo, "src/a.py", NUMBERED)
    (tmp_path / "outside.py").write_text("secret\n")
    with pytest.raises(source_view.SourceNotAvailable):
        read(conn, rel)


@pytest.mark.parametrize("rel", [["src/a.py"], {"a": 1}, None, 5, b"src/a.py", 1.5])
def test_a_path_that_is_not_a_string_is_refused_not_a_crash(conn, repo, rel):
    index(conn, repo, "src/a.py", NUMBERED)
    with pytest.raises(source_view.SourceNotAvailable):
        read(conn, rel)


def test_a_project_folder_stored_through_a_symlink_still_works(conn, repo, tmp_path):
    index(conn, repo, "src/a.py", NUMBERED)
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo)
    conn.execute("UPDATE projects SET repo_path = ? WHERE id = 1", (str(link),))
    conn.commit()
    assert read(conn, "src/a.py", 1, 1)["lines"] == ["line 1"]


def test_the_file_is_opened_without_following_a_symlink_and_without_blocking(conn, repo, monkeypatch):
    """A swap to a symlink between the check and the open cannot be staged reliably in a test, so the two flags that close that gap are pinned."""
    index(conn, repo, "src/a.py", NUMBERED)
    seen = []
    real = os.open

    def spy(path, flags, *args, **kwargs):
        seen.append(flags)
        return real(path, flags, *args, **kwargs)
    monkeypatch.setattr(source_view.os, "open", spy)
    read(conn, "src/a.py", 1, 1)
    assert len(seen) == 1 and seen[0] & os.O_NOFOLLOW and seen[0] & os.O_NONBLOCK and not seen[0] & (os.O_WRONLY | os.O_RDWR)


def test_a_real_file_that_was_never_indexed_is_refused(conn, repo):
    index(conn, repo, "src/a.py", NUMBERED)
    (repo / ".env").write_text("TOKEN=abc\n")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]\n")
    for rel in (".env", ".git/config", "src/b.py"):
        with pytest.raises(source_view.SourceNotAvailable):
            read(conn, rel)


def test_another_projects_file_is_refused(conn, repo):
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('q', ?, 'now')", (str(repo),))
    conn.commit()
    index(conn, repo, "only_in_two.py", NUMBERED, project=2)
    with pytest.raises(source_view.SourceNotAvailable):
        read(conn, "only_in_two.py", project=1)
    assert read(conn, "only_in_two.py", project=2)["lines"][0] == "line 1"


def test_an_indexed_file_that_was_deleted_is_refused(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    (repo / "a.py").unlink()
    with pytest.raises(source_view.SourceNotAvailable):
        read(conn, "a.py")


def test_an_indexed_file_replaced_by_a_symlink_to_outside_is_refused(conn, repo, tmp_path):
    index(conn, repo, "a.py", NUMBERED)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside secret\n")
    (repo / "a.py").unlink()
    (repo / "a.py").symlink_to(outside)
    with pytest.raises(source_view.SourceNotAvailable):
        read(conn, "a.py", 1, 1)


def test_a_file_reached_through_a_symlinked_folder_to_outside_is_refused(conn, repo, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "b.py").write_bytes(NUMBERED)
    (repo / "linked").symlink_to(outside)
    conn.execute("INSERT INTO files (project_id, rel_path, hash) VALUES (1, 'linked/b.py', ?)", (sha(NUMBERED),))
    conn.commit()
    with pytest.raises(source_view.SourceNotAvailable):
        read(conn, "linked/b.py")


def test_a_symlink_that_stays_inside_the_project_is_allowed(conn, repo):
    index(conn, repo, "real.py", NUMBERED)
    (repo / "alias.py").symlink_to(repo / "real.py")
    conn.execute("INSERT INTO files (project_id, rel_path, hash) VALUES (1, 'alias.py', ?)", (sha(NUMBERED),))
    conn.commit()
    assert read(conn, "alias.py", 1, 1)["lines"] == ["line 1"]


def test_a_folder_a_pipe_and_a_binary_file_are_not_read(conn, repo):
    (repo / "folder.py").mkdir()
    os.mkfifo(repo / "pipe.py")
    for rel in ("folder.py", "pipe.py"):
        conn.execute("INSERT INTO files (project_id, rel_path, hash) VALUES (1, ?, 'h')", (rel,))
    conn.commit()
    for rel in ("folder.py", "pipe.py"):
        with pytest.raises(source_view.SourceNotAvailable):
            read(conn, rel)
    index(conn, repo, "image.py", b"\x89PNG\r\n\x00\x00data")
    with pytest.raises(source_view.SourceNotText):
        read(conn, "image.py")


def test_a_file_over_the_size_limit_is_refused_without_reading_it_all(conn, repo, monkeypatch):
    index(conn, repo, "big.py", b"a\n" * 10)
    monkeypatch.setattr(source_view, "MAX_VIEW_BYTES", 10)
    with pytest.raises(source_view.SourceTooBig):
        read(conn, "big.py")


def test_at_most_one_byte_over_the_limit_is_ever_read(conn, repo, monkeypatch):
    """The memory bound: a 10 GB file must never be loaded just to be refused. The size cannot be faked cheaply, so the size of the read is pinned."""
    index(conn, repo, "big.py", b"a\n" * 50)
    monkeypatch.setattr(source_view, "MAX_VIEW_BYTES", 10)
    asked = []
    real = source_view.os.fdopen

    def spy(fd, mode="r", *args, **kwargs):
        handle = real(fd, mode, *args, **kwargs)

        class Wrapper:
            def read(self, n=-1):
                asked.append(n)
                return handle.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return handle.__exit__(*exc)
        return Wrapper()
    monkeypatch.setattr(source_view.os, "fdopen", spy)
    with pytest.raises(source_view.SourceTooBig):
        read(conn, "big.py")
    assert asked == [11]


def test_a_file_just_under_the_limit_is_read(conn, repo, monkeypatch):
    index(conn, repo, "ok.py", b"a\n" * 5)
    monkeypatch.setattr(source_view, "MAX_VIEW_BYTES", 10)
    assert read(conn, "ok.py", 1, 1)["lines"] == ["a"]


def test_the_missing_project_folder_is_its_own_error(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    (repo / "a.py").unlink()
    repo.rmdir()
    with pytest.raises(source_view.RepoGone):
        read(conn, "a.py")


# ---- the range

@pytest.mark.parametrize("start,end", [(0, 5), (-1, 5), (5, 4), (1, source_view.MAX_VIEW_LINES + 1), (True, 5), (1.5, 5), ("1", 5), (None, 5), (1, None)])
def test_a_bad_range_is_refused_not_clamped(conn, repo, start, end):
    index(conn, repo, "a.py", NUMBERED)
    with pytest.raises(source_view.BadRange):
        read(conn, "a.py", start, end)


def test_the_biggest_allowed_range_works(conn, repo):
    index(conn, repo, "a.py", NUMBERED)
    assert len(read(conn, "a.py", 1, source_view.MAX_VIEW_LINES)["lines"]) == 100


# ---- the endpoint

@pytest.fixture
def client(conn, repo):
    services = Services(FakeEmbedder(), store_factory=lambda pid: InMemoryVectorStore())
    c = TestClient(create_app(services, IndexJobs(services), auto_sync=False))
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


def post(client, **body):
    return client.post("/projects/1/source", json={"path": "src/a.py", "start_line": 3, "end_line": 4, **body})


def test_the_endpoint_returns_the_lines(conn, repo, client):
    index(conn, repo, "src/a.py", NUMBERED)
    r = post(client)
    assert r.status_code == 200
    assert r.json() == {"path": "src/a.py", "start_line": 3, "end_line": 4, "total_lines": 100, "stale": False, "lines": ["line 3", "line 4"]}


def test_every_refusal_has_its_status_and_code_and_never_repeats_the_path(conn, repo, client, tmp_path):
    index(conn, repo, "src/a.py", NUMBERED)
    index(conn, repo, "bin.py", b"\x00\x01")
    index(conn, repo, "src/c.py", NUMBERED)
    (repo / "src" / "c.py").write_bytes(b"x\n")
    (tmp_path / "secret.txt").write_text("secret")
    sneaky = "../secret.txt"
    cases = [
        (post(client, path=sneaky), 404, "file_not_available"),
        (post(client, path="/etc/passwd"), 404, "file_not_available"),
        (post(client, path="nope.py"), 404, "file_not_available"),
        (post(client, path="bin.py"), 415, "file_not_text"),
        (post(client, path="src/c.py", start_line=50, end_line=60), 409, "file_changed"),
        (post(client, start_line=0), 422, "invalid_request"),
        (post(client, end_line=1), 422, "invalid_request"),
        (post(client, end_line=3 + source_view.MAX_VIEW_LINES), 422, "invalid_request"),
    ]
    for r, status, code in cases:
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), r.text
    assert cases[0][0].json()["error"]["message"] == "This file cannot be shown. It may have moved, changed or never been indexed."
    for r, *_ in cases[:3]:
        assert sneaky not in r.text and "passwd" not in r.text and "nope" not in r.text
    assert len({r.json()["error"]["message"] for r, *_ in cases[:3]}) == 1, "one message for every kind of 'not available'"


def test_a_file_over_the_limit_is_413_with_its_own_code(conn, repo, client, monkeypatch):
    index(conn, repo, "src/a.py", NUMBERED)
    monkeypatch.setattr(source_view, "MAX_VIEW_BYTES", 10)
    r = post(client)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_big"


def test_unknown_project_and_missing_folder(conn, repo, client):
    index(conn, repo, "src/a.py", NUMBERED)
    r = client.post("/projects/9/source", json={"path": "src/a.py", "start_line": 1, "end_line": 2})
    assert r.status_code == 404 and r.json()["error"]["code"] == "project_not_found"
    (repo / "src" / "a.py").unlink()
    (repo / "src").rmdir()
    repo.rmdir()
    r = post(client)
    assert r.status_code == 409 and r.json()["error"]["code"] == "repo_not_found"


def test_the_endpoint_is_strict_about_the_body(conn, repo, client):
    index(conn, repo, "src/a.py", NUMBERED)
    for body in ({"path": "src/a.py", "start_line": 1}, {"start_line": 1, "end_line": 2}, {"path": 5, "start_line": 1, "end_line": 2},
                 {"path": "src/a.py", "start_line": "1", "end_line": 2}, {"path": "src/a.py", "start_line": True, "end_line": 2},
                 {"path": "src/a.py", "start_line": 1, "end_line": 2, "root": "/"}, {"path": "x" * 5000, "start_line": 1, "end_line": 2}):
        r = client.post("/projects/1/source", json=body)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request", body


def test_it_needs_neither_ollama_nor_the_answer_model(conn, repo, client):
    """The viewer must work with Ollama off and no key: it reads a file, nothing more."""
    index(conn, repo, "src/a.py", NUMBERED)
    assert post(client).status_code == 200
