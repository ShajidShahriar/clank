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
