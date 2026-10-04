"""Task 8.6: the backend's entry point (`python run.py --port N`) can only listen on 127.0.0.1.

There is no host option at all: the address is not a setting that could be set wrong. The security settings are checked BEFORE the server starts, so a missing
token is a clear message and exit code 2 instead of a server that runs but refuses to serve. The token is never printed.
"""
import pytest

import run


TOKEN = "k" * 40


def call(argv, env, capsys):
    started = []
    code = run.main(argv, environ=env, serve=lambda **kw: started.append(kw))
    return code, started, capsys.readouterr()


def test_it_listens_on_loopback_only(capsys):
    code, started, _ = call(["--port", "8123"], {"CLANK_API_TOKEN": TOKEN}, capsys)
    assert code == 0 and len(started) == 1
    assert started[0]["host"] == "127.0.0.1" and started[0]["port"] == 8123


@pytest.mark.parametrize("extra", [["--host", "0.0.0.0"], ["--host=127.0.0.1"], ["--bind", "0.0.0.0:80"], ["-H", "x"]])
def test_there_is_no_host_option(extra, capsys):
    with pytest.raises(SystemExit) as caught:
        run.main(["--port", "8123", *extra], environ={"CLANK_API_TOKEN": TOKEN}, serve=lambda **kw: pytest.fail("must not start"))
    assert caught.value.code == 2


@pytest.mark.parametrize("port", ["0", "-1", "65536", "abc", ""])
def test_the_port_must_be_a_real_one(port, capsys):
    with pytest.raises(SystemExit) as caught:
        run.main(["--port", port], environ={"CLANK_API_TOKEN": TOKEN}, serve=lambda **kw: pytest.fail("must not start"))
    assert caught.value.code == 2


def test_a_port_is_required(capsys):
    with pytest.raises(SystemExit):
        run.main([], environ={"CLANK_API_TOKEN": TOKEN}, serve=lambda **kw: pytest.fail("must not start"))


def test_a_missing_or_short_token_stops_before_the_server_starts_and_says_what_to_set(capsys):
    for env in ({}, {"CLANK_API_TOKEN": "short"}):
        code, started, out = call(["--port", "8123"], env, capsys)
        assert code == 2 and started == [] and "CLANK_API_TOKEN" in out.err
        assert "short" not in out.err.replace("short enough", "")


def test_a_bad_origin_list_stops_it_too(capsys):
    code, started, out = call(["--port", "8123"], {"CLANK_API_TOKEN": TOKEN, "CLANK_ALLOWED_ORIGINS": "*"}, capsys)
    assert code == 2 and started == [] and "CLANK_ALLOWED_ORIGINS" in out.err


def test_the_token_is_never_printed(capsys):
    _, _, out = call(["--port", "8123"], {"CLANK_API_TOKEN": TOKEN}, capsys)
    assert TOKEN not in out.out + out.err


def test_the_server_gets_the_app_and_quiet_proxy_settings(capsys):
    _, started, _ = call(["--port", "8123"], {"CLANK_API_TOKEN": TOKEN}, capsys)
    assert started[0]["app"] == "main:app" and started[0].get("proxy_headers") is False, "X-Forwarded-* headers are never trusted"


# ---- task 8.7: the backend dies with the app that started it

import io
import subprocess
import sys
import threading
import time
from pathlib import Path

from run import watch_stdin


def test_watch_stdin_calls_back_when_the_stream_ends():
    closed = threading.Event()
    thread = watch_stdin(io.BytesIO(b""), closed.set)
    thread.join(5)
    assert closed.is_set() and not thread.is_alive()


def test_watch_stdin_reads_and_ignores_data_until_the_end():
    closed = threading.Event()
    watch_stdin(io.BytesIO(b"anything\nthe parent writes\n"), closed.set).join(5)
    assert closed.is_set()


def test_watch_stdin_does_not_call_back_while_the_stream_stays_open():
    read_end, write_end = __import__("os").pipe()
    closed = threading.Event()
    with open(read_end, "rb", buffering=0) as stream:
        thread = watch_stdin(stream, closed.set)
        time.sleep(0.3)
        assert not closed.is_set() and thread.is_alive()
        __import__("os").close(write_end)
        thread.join(5)
    assert closed.is_set()


def test_watch_stdin_is_a_daemon_thread_so_it_never_keeps_the_process_alive():
    thread = watch_stdin(io.BytesIO(b""), lambda: None)
    assert thread.daemon


def test_the_flag_is_off_by_default_and_turns_the_watch_on(capsys):
    watching = []
    for argv, expected in ((["--port", "8123"], False), (["--port", "8123", "--exit-when-stdin-closes"], True)):
        run.main(argv, environ={"CLANK_API_TOKEN": TOKEN}, serve=lambda **kw: None, watch=lambda: watching.append(True))
        assert bool(watching) is expected
        watching.clear()


def test_a_real_process_ends_when_its_parent_closes_its_stdin():
    """The real thing: run.py's watcher with the real SIGTERM, in a child process whose stdin we then close (what happens when Electron dies)."""
    code = ("import os, signal, sys, time\n"
            "sys.path.insert(0, %r)\n"
            "import run\n"
            "run.watch_stdin(sys.stdin.buffer, lambda: os.kill(os.getpid(), signal.SIGTERM))\n"
            "print('up', flush=True)\n"
            "time.sleep(60)\n") % str(Path(run.__file__).parent)
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert child.stdout.readline().strip() == b"up"
    child.stdin.close()
    assert child.wait(timeout=10) == -15, "ended by SIGTERM, not by waiting out its 60 seconds"


def test_the_real_stop_is_a_SIGTERM_so_uvicorn_shuts_down_cleanly():
    """`_stop_this_process` itself, in a child: SIGTERM is what uvicorn turns into a clean shutdown (a SIGINT would look like Ctrl-C to the app)."""
    code = f"import sys, time\nsys.path.insert(0, {str(Path(run.__file__).parent)!r})\nimport run\nrun._stop_this_process()\ntime.sleep(30)\n"
    child = subprocess.Popen([sys.executable, "-c", code])
    assert child.wait(timeout=10) == -15
