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


def test_big_data_files_skipped_small_ones_kept(tmp_path):
    make(tmp_path, "small.json", "small.yaml", "app.py")
    (tmp_path / "big.json").write_text('{"a": 1}\n' * 4000)   # ~36 KB
    (tmp_path / "big.yaml").write_text("a: 1\n" * 6000)       # ~30 KB
    (tmp_path / "big.py").write_text("x = 1\n" * 6000)         # same size, but code: kept
    assert names(tmp_path) == ["app.py", "big.py", "small.json", "small.yaml"]


def test_restructuredtext_docs_are_found(tmp_path):
    make(tmp_path, "docs/errorhandling.rst", "CHANGES.rst", "a.py")
    assert names(tmp_path) == ["CHANGES.rst", "a.py", "docs/errorhandling.rst"]


# ---- what is not a plain file in the repo (a FIFO, a socket, a link that leads out of the repo)

import socket
import tempfile
import threading
from pathlib import Path

from file_discovery import is_binary


def run_with_deadline(function, seconds=2.0):
    """Runs `function` in a thread that is given up on after `seconds`. Returns (finished, result). A function that blocks forever does not stop the test."""
    box = {}

    def target():
        box["result"] = function()
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    return (not thread.is_alive()), box.get("result")


def free_a_blocked_open(fifo):
    """Unblocks a thread stuck opening a FIFO for reading (by opening the other end), so a failing test leaves no stuck thread behind."""
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
    except OSError:
        pass


def test_a_fifo_with_a_source_name_never_blocks_discovery(tmp_path):
    make(tmp_path, "a.py")
    fifo = tmp_path / "x.py"
    os.mkfifo(fifo)
    try:
        finished, found = run_with_deadline(lambda: names(tmp_path))
    finally:
        free_a_blocked_open(fifo)
    assert finished, "discovery blocked on a FIFO"
    assert found == ["a.py"]


def test_a_fifo_is_binary_for_the_reader_and_never_blocks_it(tmp_path):
    fifo = tmp_path / "x.py"
    os.mkfifo(fifo)
    try:
        finished, result = run_with_deadline(lambda: is_binary(fifo))
    finally:
        free_a_blocked_open(fifo)
    assert finished, "is_binary blocked on a FIFO"
    assert result is True


def test_a_socket_with_a_source_name_is_skipped():
    short = Path(tempfile.mkdtemp(prefix="cd")).resolve()             # a socket path must be short
    try:
        make(short, "a.py")
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(short / "x.py"))
            assert names(short) == ["a.py"]
        finally:
            sock.close()
    finally:
        import shutil
        shutil.rmtree(short, ignore_errors=True)


def test_a_link_to_a_device_is_skipped(tmp_path):
    make(tmp_path, "a.py")
    (tmp_path / "dev.py").symlink_to("/dev/null")
    assert names(tmp_path) == ["a.py"]


def test_a_symlinked_file_that_leads_out_of_the_repo_is_skipped(tmp_path):
    outside = tmp_path / "outside"
    repo = tmp_path / "repo"
    make(outside, "secret.py")
    make(repo, "a.py")
    (repo / "link.py").symlink_to(outside / "secret.py")
    assert names(repo) == ["a.py"]


def test_a_chain_of_links_that_ends_outside_the_repo_is_skipped(tmp_path):
    outside = tmp_path / "outside"
    repo = tmp_path / "repo"
    make(outside, "secret.py")
    make(repo, "a.py")
    (repo / "first.py").symlink_to(repo / "second.py")
    (repo / "second.py").symlink_to(outside / "secret.py")
    assert names(repo) == ["a.py"]


def test_a_symlinked_file_inside_the_repo_still_works(tmp_path):
    make(tmp_path, "real.py", "pkg/other.py")
    (tmp_path / "alias.py").symlink_to(tmp_path / "real.py")
    (tmp_path / "pkg" / "relative.py").symlink_to("../real.py")
    assert names(tmp_path) == ["alias.py", "pkg/other.py", "pkg/relative.py", "real.py"]


def test_a_broken_link_and_a_loop_of_links_are_skipped_without_an_error(tmp_path):
    make(tmp_path, "a.py")
    (tmp_path / "broken.py").symlink_to(tmp_path / "missing.py")
    (tmp_path / "loop1.py").symlink_to(tmp_path / "loop2.py")
    (tmp_path / "loop2.py").symlink_to(tmp_path / "loop1.py")
    finished, found = run_with_deadline(lambda: names(tmp_path))
    assert finished and found == ["a.py"]


def test_a_linked_folder_is_not_entered(tmp_path):
    outside = tmp_path / "outside"
    repo = tmp_path / "repo"
    make(outside, "secret.py")
    make(repo, "a.py")
    (repo / "linked").symlink_to(outside, target_is_directory=True)
    assert names(repo) == ["a.py"]


def test_a_symlink_that_leads_out_does_not_depend_on_the_repo_being_given_through_a_link(tmp_path):
    outside = tmp_path / "outside"
    real = tmp_path / "real"
    make(outside, "secret.py")
    make(real, "a.py")
    (real / "link.py").symlink_to(outside / "secret.py")
    entrance = tmp_path / "entrance"
    entrance.symlink_to(real, target_is_directory=True)               # the repo is opened through a link
    assert sorted(str(p.relative_to(real)) for p in discover_files(str(entrance))) == ["a.py"]


# ---- each guard on its own (they overlap on purpose: a FIFO is stopped by the discovery check AND by the reader)

def test_discovery_itself_skips_a_fifo_even_if_the_reader_would_accept_it(tmp_path, monkeypatch):
    import file_discovery
    monkeypatch.setattr(file_discovery, "is_binary", lambda path: False)
    make(tmp_path, "a.py")
    fifo = tmp_path / "x.py"
    os.mkfifo(fifo)
    finished, found = run_with_deadline(lambda: names(tmp_path))
    assert finished and found == ["a.py"]


def test_discovery_itself_skips_a_socket_even_if_the_reader_would_accept_it(monkeypatch):
    import file_discovery
    monkeypatch.setattr(file_discovery, "is_binary", lambda path: False)
    short = Path(tempfile.mkdtemp(prefix="cd")).resolve()
    try:
        make(short, "a.py")
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(short / "x.py"))
            assert names(short) == ["a.py"]
        finally:
            sock.close()
    finally:
        import shutil
        shutil.rmtree(short, ignore_errors=True)


def test_the_size_of_a_linked_file_is_the_size_of_its_target(tmp_path):
    make(tmp_path, "small.py")
    (tmp_path / "big.py").write_text("x = 1\n" * 200_000)             # ~1.2 MB: too big
    (tmp_path / "alias_big.py").symlink_to(tmp_path / "big.py")
    (tmp_path / "alias_small.py").symlink_to(tmp_path / "small.py")
    assert names(tmp_path) == ["alias_small.py", "small.py"]


def test_a_file_that_cannot_be_read_is_skipped(tmp_path):
    make(tmp_path, "a.py", "locked.py")
    (tmp_path / "locked.py").chmod(0)
    try:
        if os.access(tmp_path / "locked.py", os.R_OK):
            return                                                    # running as someone who can read it anyway
        assert names(tmp_path) == ["a.py"]
        assert is_binary(tmp_path / "locked.py") is True
    finally:
        (tmp_path / "locked.py").chmod(0o644)


def test_only_the_first_8192_bytes_are_looked_at_for_a_null_byte(tmp_path):
    (tmp_path / "late.py").write_bytes(b"a" * 8192 + b"\0")           # the null byte is the 8193rd: not looked at
    (tmp_path / "edge.py").write_bytes(b"a" * 8191 + b"\0")           # the null byte is the 8192nd: seen
    assert is_binary(tmp_path / "late.py") is False
    assert is_binary(tmp_path / "edge.py") is True


def test_discovery_does_not_leave_files_open(tmp_path):
    for i in range(40):
        make(tmp_path, f"f{i}.py")
    before = len(os.listdir("/dev/fd"))
    assert len(discover_files(str(tmp_path))) == 40
    assert len(os.listdir("/dev/fd")) <= before


def test_a_read_that_fails_counts_as_binary_and_the_file_is_closed(tmp_path, monkeypatch):
    import file_discovery
    (tmp_path / "a.py").write_text("x = 1\n")
    closed = []
    real_close = os.close
    monkeypatch.setattr(file_discovery.os, "read", lambda fd, n: (_ for _ in ()).throw(OSError("input/output error")))
    monkeypatch.setattr(file_discovery.os, "close", lambda fd: (closed.append(fd), real_close(fd)))
    assert is_binary(tmp_path / "a.py") is True
    assert len(closed) == 1
