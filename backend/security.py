"""Local API hardening (task 8.6): the backend is a local helper for one app, so everything that is not that app is refused.

Every HTTP request passes `GuardMiddleware`, in this order: the Host header (a web page that rebinds its own DNS name to 127.0.0.1 sends another Host), the
Origin header (only browsers send one; it must be on the allow list), a CORS preflight from an allowed origin (answered without a token: a browser cannot
attach one), and the per-launch token in `X-Clank-Token` (compared in constant time). There are NO exceptions to the token: not /health, not the docs, not an
unknown address. A refusal uses the one JSON error shape and never says what the right token or origin is. The guard also turns an unexpected error into the
500 JSON itself, so the answer to an allowed origin always carries the CORS headers (an error layer outside the middleware would not add them).

The token and the allowed origins come from the environment (set by the app that starts the backend). With no valid token the app REFUSES TO START.
"""
import logging
import os
import re
import secrets
from dataclasses import dataclass

from api_errors import error_response

log = logging.getLogger("clank.api")

TOKEN_HEADER = b"x-clank-token"
MIN_TOKEN_CHARS = 32
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
_ORIGIN = re.compile(r"^(?:https?://[A-Za-z0-9.\-]+(?::\d{1,5})?|null)$")
_ALLOWED_METHODS = "GET, POST, DELETE, OPTIONS"
_ALLOWED_HEADERS = "X-Clank-Token, Content-Type"


class SecurityConfigError(RuntimeError):
    """The token or the allowed origins are missing or malformed. The message never contains the token."""


@dataclass(frozen=True)
class Security:
    token: str | None                                    # None only for NO_SECURITY_FOR_TESTS: the guard then checks nothing
    allowed_origins: tuple = ()                          # exact origins; "null" only if listed (it is what a page loaded from a file sends)
    allowed_hosts: tuple = LOOPBACK_HOSTS

    @property
    def enabled(self) -> bool:
        return self.token is not None

    @classmethod
    def from_env(cls, environ=None) -> "Security":
        environ = os.environ if environ is None else environ
        token = environ.get("CLANK_API_TOKEN", "")
        if len(token) < MIN_TOKEN_CHARS:
            raise SecurityConfigError(f"CLANK_API_TOKEN must be set to a random string of at least {MIN_TOKEN_CHARS} characters "
                                      f"(the app that starts the backend makes a new one for every launch)")
        raw = environ.get("CLANK_ALLOWED_ORIGINS", "")
        origins = tuple(part.strip() for part in raw.split(",") if part.strip())
        for origin in origins:
            if not _ORIGIN.match(origin):
                raise SecurityConfigError("CLANK_ALLOWED_ORIGINS must be a comma-separated list of exact origins such as http://localhost:5173 "
                                          "(no wildcard, no path, no trailing slash)")
        return cls(token=token, allowed_origins=origins)


# For tests only: nothing is checked. The real app never uses it (create_app reads the environment when no Security is given).
NO_SECURITY_FOR_TESTS = Security(token=None)


def _hostname(host_header: str) -> str | None:
    """The host name of a Host header, lower case, without a port; None if it is not a plain name or IPv4 address with an optional numeric port."""
    host = host_header.strip().lower()
    if ":" in host:
        host, _, port = host.rpartition(":")
        if not (port.isdigit() and 1 <= len(port) <= 5):
            return None
    return host or None


class GuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":                                    # no websockets here
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        security = getattr(scope["app"].state, "security", None)
        if security is None:
            return await error_response(503, "starting", "Clank is still starting. Try again in a moment.")(scope, receive, send)

        headers = {name: value for name, value in scope["headers"]}
        origin = headers.get(b"origin", b"").decode("latin-1") if b"origin" in headers else None
        origin_ok = security.enabled and origin is not None and origin in security.allowed_origins
        send = _with_cors(send, origin if origin_ok else None)

        if security.enabled:
            refusal = self._refusal(security, headers, origin, origin_ok, scope["method"])
            if refusal is not None:
                if refusal == "preflight":
                    return await _preflight(origin)(scope, receive, send)
                status, code, message = refusal
                return await error_response(status, code, message)(scope, receive, send)

        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if started:
                raise
            log.error("unexpected error on %s %s", scope["method"], scope["path"], exc_info=exc)
            await error_response(500, "internal_error", "Something went wrong inside Clank. The details are in the backend log.")(scope, receive, send)

    @staticmethod
    def _refusal(security, headers, origin, origin_ok, method):
        host = _hostname(headers.get(b"host", b"").decode("latin-1"))
        if host is None or host not in security.allowed_hosts:
            return 403, "bad_host", "This address is only for the Clank app on this computer."
        if origin is not None and not origin_ok:
            return 403, "bad_origin", "This origin is not allowed."
        if method == "OPTIONS" and origin_ok and b"access-control-request-method" in headers:
            return "preflight"
        supplied = headers.get(TOKEN_HEADER)
        if supplied is None:
            return 401, "missing_token", "A valid token is required."
        if not secrets.compare_digest(supplied, security.token.encode("utf-8")):
            return 401, "invalid_token", "A valid token is required."
        return None


def _with_cors(send, allowed_origin: str | None):
    """Add the CORS headers to every answer for an allowed origin (errors included)."""
    if allowed_origin is None:
        return send

    async def cors_send(message):
        if message["type"] == "http.response.start":
            headers = [h for h in message.get("headers", []) if h[0].lower() not in (b"access-control-allow-origin",)]
            headers.append((b"access-control-allow-origin", allowed_origin.encode("latin-1")))
            if not any(h[0].lower() == b"vary" for h in headers):
                headers.append((b"vary", b"Origin"))
            message = {**message, "headers": headers}
        await send(message)
    return cors_send


def _preflight(origin: str):
    from starlette.responses import Response
    return Response(status_code=204, headers={"access-control-allow-methods": _ALLOWED_METHODS, "access-control-allow-headers": _ALLOWED_HEADERS,
                                              "access-control-max-age": "600", "vary": "Origin"})
