"""Task 8.6: local API hardening.

The backend is a local helper for one app, not a web service. So:
- it listens on 127.0.0.1 only (`run.py` has no host option at all);
- EVERY request needs the per-launch token in `X-Clank-Token` (no exceptions: not /health, not the docs, not an unknown address, so nothing can be probed);
  the token comes from the environment (`CLANK_API_TOKEN`, set by Electron), is compared in constant time, and the app REFUSES TO START without one;
- the Host header must be 127.0.0.1 or localhost (a web page that rebinds its own DNS name to 127.0.0.1 sends another Host and is refused);
- an Origin header (only browsers send it) must be on the allow list (`CLANK_ALLOWED_ORIGINS`, empty by default, never "*"); a preflight from an allowed
  origin is answered without a token (browsers cannot attach one), and every answer to an allowed origin, errors included, carries the CORS headers;
- every refusal uses the one JSON error shape and never says what the right token or origin is.
The order of checks is: Host, Origin, preflight, token.
"""
import logging
import secrets

import pytest
from fastapi.testclient import TestClient

import security as security_module
from embedding import FakeEmbedder
from main import create_app
from security import Security, SecurityConfigError
from services import Services
from vectorstore import InMemoryVectorStore

TOKEN = "t" * 20 + secrets.token_urlsafe(24)               # 44 characters
SEC = Security(token=TOKEN, allowed_origins=("http://localhost:5173",))
BASE = "http://127.0.0.1:8123"
GOOD = {"x-clank-token": TOKEN}


@pytest.fixture
def make(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "sec.db"))
    clients = []

    def build(security=SEC, base_url=BASE, **kw):
        services = Services(FakeEmbedder(dim=4, digest="d1"), store_factory=lambda pid: InMemoryVectorStore())
        app = create_app(services, auto_sync=False, security=security, **kw)

        @app.get("/boom")
        def boom():
            raise RuntimeError("secret internal detail /Users/someone/x.py")
        client = TestClient(app, base_url=base_url, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        services.wait_for_warmup()
        return client
    yield build
    for c in clients:
        c.__exit__(None, None, None)


def code(response):
    return response.json()["error"]["code"]


# ---- configuration

def test_the_token_comes_from_the_environment_and_must_be_long_enough():
    s = Security.from_env({"CLANK_API_TOKEN": TOKEN})
    assert s.token == TOKEN and s.allowed_origins == () and s.allowed_hosts == ("127.0.0.1", "localhost")
    for env in ({}, {"CLANK_API_TOKEN": ""}, {"CLANK_API_TOKEN": "short-token-1234"}, {"CLANK_API_TOKEN": "x" * 31}):
        with pytest.raises(SecurityConfigError, match="CLANK_API_TOKEN") as caught:
            Security.from_env(env)
        assert env.get("CLANK_API_TOKEN") is None or env["CLANK_API_TOKEN"] not in str(caught.value) or env["CLANK_API_TOKEN"] == "", "the value is never echoed"
    assert Security.from_env({"CLANK_API_TOKEN": "x" * 32}).token == "x" * 32


def test_allowed_origins_are_a_comma_list_of_exact_origins_and_never_a_wildcard():
    env = {"CLANK_API_TOKEN": TOKEN}
    assert Security.from_env({**env, "CLANK_ALLOWED_ORIGINS": " http://localhost:5173 , http://127.0.0.1:5173,null "}).allowed_origins == (
        "http://localhost:5173", "http://127.0.0.1:5173", "null")
    assert Security.from_env({**env, "CLANK_ALLOWED_ORIGINS": ""}).allowed_origins == ()
    for bad in ("*", "http://*", "localhost:5173", "http://localhost:5173/", "http://a.com/path", "ftp://a.com", "http://a.com,*"):
        with pytest.raises(SecurityConfigError, match="CLANK_ALLOWED_ORIGINS"):
            Security.from_env({**env, "CLANK_ALLOWED_ORIGINS": bad})


def test_the_real_app_refuses_to_start_without_a_token(monkeypatch, tmp_path):
    monkeypatch.delenv("CLANK_API_TOKEN", raising=False)
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "x.db"))
    with pytest.raises(SecurityConfigError, match="CLANK_API_TOKEN"):
        with TestClient(create_app(Services(FakeEmbedder(dim=4), store_factory=lambda pid: InMemoryVectorStore()), auto_sync=False)):
            pass


def test_the_default_security_is_read_from_the_environment_at_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("CLANK_API_TOKEN", TOKEN)
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "x.db"))
    app = create_app(Services(FakeEmbedder(dim=4), store_factory=lambda pid: InMemoryVectorStore()), auto_sync=False)
    with TestClient(app, base_url=BASE) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/health", headers=GOOD).status_code == 200


# ---- the token

def test_every_address_needs_the_token(make):
    client = make()
    for method, path in [("GET", "/health"), ("GET", "/startup-sync"), ("GET", "/projects/1/index"), ("POST", "/projects/1/index"),
                         ("POST", "/projects/1/index/cancel"), ("POST", "/projects/1/context"), ("GET", "/docs"), ("GET", "/openapi.json"),
                         ("GET", "/redoc"), ("GET", "/no-such-address"), ("DELETE", "/health")]:
        r = client.request(method, path)
        assert r.status_code == 401 and code(r) == "missing_token", (method, path)
        assert list(r.json()) == ["error"] and sorted(r.json()["error"]) == ["code", "message"]


def test_a_wrong_token_is_refused_and_the_right_one_works(make):
    client = make()
    for wrong in ("", "wrong", TOKEN[:-1], TOKEN + "x", TOKEN.upper(), " " + TOKEN):
        r = client.get("/health", headers={"x-clank-token": wrong})
        assert r.status_code == 401 and code(r) in ("invalid_token", "missing_token"), repr(wrong)
    r = client.get("/health", headers=GOOD)
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_the_token_goes_in_the_header_only(make):
    client = make()
    assert client.get(f"/health?token={TOKEN}").status_code == 401
    assert client.get("/health", headers={"authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert client.get("/health", cookies={"x-clank-token": TOKEN}).status_code == 401


def test_a_header_that_is_not_ascii_is_a_401_not_a_crash(make):
    client = make()
    r = client.get("/health", headers={"x-clank-token": "tokén".encode("latin-1")})        # raw bytes: what a hostile client can send
    assert r.status_code == 401 and code(r) == "invalid_token"


def test_the_token_is_compared_in_constant_time(make, monkeypatch):
    calls = []
    real = secrets.compare_digest
    monkeypatch.setattr(security_module.secrets, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    make().get("/health", headers=GOOD)
    assert calls and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)


def test_a_refusal_never_says_what_the_token_is(make):
    r = make().get("/health", headers={"x-clank-token": "wrong"})
    assert TOKEN not in r.text and TOKEN[:8] not in r.text


# ---- the Host header

@pytest.mark.parametrize("host, ok", [
    ("127.0.0.1", True), ("127.0.0.1:8123", True), ("localhost", True), ("localhost:5173", True), ("LOCALHOST:80", True),
    ("evil.com", False), ("evil.com:8123", False), ("127.0.0.1.evil.com", False), ("localhost.evil.com", False), ("evil.com@127.0.0.1", False),
    ("127.0.0.1@evil.com", False), ("0.0.0.0", False), ("[::1]", False), ("localhost.", False), ("", False), ("127.0.0.1:abc", False),
])
def test_only_the_loopback_names_are_accepted_as_host(make, host, ok):
    r = make().get("/health", headers={**GOOD, "host": host})
    assert (r.status_code == 200) is ok, (host, r.status_code)
    if not ok:
        assert r.status_code == 403 and code(r) == "bad_host"


def test_a_request_with_no_host_header_at_all_is_refused(make):
    client = make()
    import asyncio
    sent = []

    async def run():
        scope = {"type": "http", "method": "GET", "path": "/health", "raw_path": b"/health", "query_string": b"", "headers": [(b"x-clank-token", TOKEN.encode())],
                 "app": client.app, "http_version": "1.0", "scheme": "http", "server": ("127.0.0.1", 8123), "client": ("127.0.0.1", 5), "root_path": ""}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
        await client.app(scope, receive, send)
    asyncio.run(run())
    assert sent[0]["status"] == 403


def test_the_host_is_checked_before_the_token(make):
    r = make().get("/health", headers={"host": "evil.com"})
    assert r.status_code == 403 and code(r) == "bad_host"


# ---- Origin and CORS

def test_a_request_with_no_origin_is_fine(make):
    assert make().get("/health", headers=GOOD).status_code == 200


def test_an_allowed_origin_gets_the_cors_headers(make):
    r = make().get("/health", headers={**GOOD, "origin": "http://localhost:5173"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173" and "origin" in r.headers["vary"].lower()
    assert "access-control-allow-credentials" not in r.headers


@pytest.mark.parametrize("origin", ["http://evil.com", "http://localhost:5174", "https://localhost:5173", "http://localhost:5173.evil.com", "null", "*", "file://"])
def test_any_other_origin_is_refused_even_with_the_right_token(make, origin):
    r = make().get("/health", headers={**GOOD, "origin": origin})
    assert r.status_code == 403 and code(r) == "bad_origin"
    assert "access-control-allow-origin" not in r.headers


def test_with_no_allowed_origins_no_browser_origin_is_accepted(make):
    client = make(Security(token=TOKEN))
    assert client.get("/health", headers={**GOOD, "origin": "http://localhost:5173"}).status_code == 403
    assert client.get("/health", headers=GOOD).status_code == 200


def test_the_null_origin_is_accepted_only_when_it_is_listed(make):
    assert make(Security(token=TOKEN, allowed_origins=("null",))).get("/health", headers={**GOOD, "origin": "null"}).status_code == 200


def test_a_preflight_from_an_allowed_origin_is_answered_without_a_token(make):
    r = make().options("/projects/1/context", headers={"origin": "http://localhost:5173", "access-control-request-method": "POST",
                                                       "access-control-request-headers": "x-clank-token, content-type"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed = r.headers["access-control-allow-headers"].lower()
    assert "x-clank-token" in allowed and "content-type" in allowed
    assert "POST" in r.headers["access-control-allow-methods"] and "DELETE" in r.headers["access-control-allow-methods"] and r.headers["access-control-max-age"] == "600"


def test_a_preflight_from_another_origin_is_refused(make):
    r = make().options("/projects/1/context", headers={"origin": "http://evil.com", "access-control-request-method": "POST"})
    assert r.status_code == 403 and code(r) == "bad_origin"


def test_a_plain_options_request_is_not_a_preflight_and_needs_the_token(make):
    assert make().options("/health").status_code == 401


def test_errors_reach_an_allowed_origin_with_the_cors_headers(make):
    client = make()
    origin = {"origin": "http://localhost:5173"}
    for headers, status in [(origin, 401), ({**GOOD, **origin}, 200)]:
        r = client.get("/health", headers=headers)
        assert r.status_code == status and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = client.get("/boom", headers={**GOOD, **origin})
    assert r.status_code == 500 and r.headers["access-control-allow-origin"] == "http://localhost:5173", "an unexpected error is readable by the app too"
    assert code(r) == "internal_error" and "secret" not in r.text
    r = client.get("/nope", headers={**GOOD, **origin})
    assert r.status_code == 404 and r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_an_unexpected_error_is_logged_once_with_its_traceback(make, caplog):
    client = make()
    with caplog.at_level(logging.ERROR):
        client.get("/boom", headers=GOOD)
    assert "secret internal detail" in caplog.text and "Traceback" in caplog.text
    assert caplog.text.count("unexpected error on GET /boom") == 1


# ---- order and the other kinds of request

def test_the_order_is_host_then_origin_then_token(make):
    client = make()
    assert code(client.get("/health", headers={"host": "evil.com", "origin": "http://evil.com"})) == "bad_host"
    assert code(client.get("/health", headers={"origin": "http://evil.com"})) == "bad_origin", "a bad origin is refused before the token is looked at"
    assert code(client.get("/health")) == "missing_token"


def test_the_guard_is_not_a_wildcard_cors_policy(make):
    for origin in (None, "http://localhost:5173", "http://evil.com"):
        headers = dict(GOOD, **({"origin": origin} if origin else {}))
        assert make().get("/health", headers=headers).headers.get("access-control-allow-origin") in (None, "http://localhost:5173")


def test_a_websocket_is_refused(make):
    client = make()
    with pytest.raises(Exception):
        with client.websocket_connect("/health", headers=GOOD):
            pass
