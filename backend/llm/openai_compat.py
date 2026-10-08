"""A client for the OpenAI-compatible chat protocol: POST <base_url>/chat/completions. Groq, Ollama (/v1), LM Studio, OpenRouter and OpenAI itself speak it,
so one class covers every local and hosted model Clank will ever be pointed at. Standard library only (no new dependency).

Safety decisions:
- the key goes in the Authorization header and nowhere else, is never in an error or a repr, and is refused over plain http to a remote address;
- a redirect is NEVER followed (urllib would resend the Authorization header to the new address);
- the provider's own error text is not passed on (it can hold an organization id, a key fragment or an internal URL): only its status and, for a rate limit,
  the wait are read;
- the client names itself `Clank/<version>`: some edges (Groq's among them) refuse the default Python user agent;
- a streamed answer (`stream`) is read with `http.client` directly, not through `urllib`: the connection must be closable from ANOTHER thread (that is what makes a
  Stop button real: the service is told to stop and stops spending tokens), and it never follows a redirect either. It does not use proxy settings from the
  environment.
"""
import http.client
import json
import re
import socket
import ssl
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .base import Completion, StreamDone, TextPiece, ThinkingPiece, check_cap, check_messages, host_is_local
from .chunks import read_chunk
from .errors import (LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMModelNotFound, LLMRateLimited, LLMTimeout, LLMUnavailable)
from .think_filter import ThinkTagFilter

USER_AGENT = "Clank/0.1"
LIMIT_PARAMS = ("max_tokens", "max_completion_tokens")
MAX_REPLY_BYTES = 4_000_000
MAX_STREAM_LINE_BYTES = 262_144                  # one line of a streamed reply (a normal piece is a few dozen bytes)
MAX_HEADER_VALUE_CHARS = 100
MAX_RETRY_AFTER = 3600.0

_CONTEXT_WORDS = re.compile(r"context.{0,20}(length|window)|reduce the length|too many tokens|maximum context|context_length", re.IGNORECASE)
_WAIT_IN_TEXT = re.compile(r"try again in\s+((?:\d+(?:\.\d+)?(?:ms|h|m|s)\s*)+)", re.IGNORECASE)
_WAIT_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)", re.IGNORECASE)
_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                                     # urllib then raises HTTPError for the 3xx: it is reported, never followed


class OpenAICompatibleClient:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, *, timeout: float = 60.0, output_limit_param: str = "max_tokens",
                 extra_body: dict | None = None):
        parts = urlsplit(base_url) if isinstance(base_url, str) else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url must be an http or https address, for example https://api.groq.com/openai/v1")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if api_key is not None and (not isinstance(api_key, str) or not api_key):
            raise ValueError("api_key must be a non-empty string or None")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        if output_limit_param not in LIMIT_PARAMS:
            raise ValueError(f"output_limit_param must be one of {LIMIT_PARAMS}")
        if extra_body is not None and (not isinstance(extra_body, dict) or not all(isinstance(k, str) for k in extra_body)):
            raise ValueError("extra_body must be a dict with text keys, or None")
        if api_key and parts.scheme == "http" and not host_is_local(base_url):
            raise ValueError("a key is only sent over https to a remote address (http is allowed for this computer only)")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout = float(timeout)
        self.output_limit_param = output_limit_param
        self.extra_body = dict(extra_body or {})
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(model={self.model!r}, key={'set' if self._api_key else 'none'})"       # never the key, never the address

    def complete(self, messages: list[dict], *, max_output_tokens: int) -> Completion:
        check_messages(messages)
        check_cap(max_output_tokens)
        body = self._body(messages, max_output_tokens, stream=False)
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode("utf-8"), headers=self._headers("application/json"), method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as reply:
                raw = reply.read(MAX_REPLY_BYTES)
                headers = rate_limit_headers(reply.headers.items())
        except urllib.error.HTTPError as error:
            raise self._for_status(error) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise LLMTimeout("the answer service did not answer in time") from None
            raise LLMUnavailable("the answer service could not be reached") from None
        except (TimeoutError, socket.timeout):
            raise LLMTimeout("the answer service did not answer in time") from None
        except (http.client.HTTPException, OSError):
            raise LLMUnavailable("the connection to the answer service failed") from None
        return self._parse(raw, headers)

    def _body(self, messages: list[dict], max_output_tokens: int, *, stream: bool, include_usage: bool = True) -> dict:
        reserved = {"model", "messages", "stream", "stream_options", *LIMIT_PARAMS}
        body = {**{k: v for k, v in self.extra_body.items() if k not in reserved},
                "model": self.model, "messages": messages, self.output_limit_param: max_output_tokens, "stream": stream}
        if stream and include_usage:
            body["stream_options"] = {"include_usage": True}          # so the numbers arrive in a last chunk
        return body

    def _headers(self, accept: str) -> dict:
        headers = {"Content-Type": "application/json", "Accept": accept, "User-Agent": USER_AGENT}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"        # the key goes here and nowhere else
        return headers

    # ---- streaming

    def stream(self, messages: list[dict], *, max_output_tokens: int) -> "OpenAIStream":
        """Ask for the answer piece by piece. The connection is opened HERE: a refusal raises now, as `complete` would. A service that rejects `stream_options` is asked
        again once without it (the numbers then come from the estimate)."""
        check_messages(messages)
        check_cap(max_output_tokens)
        for include_usage in (True, False):
            conn, reply = self._open(self._body(messages, max_output_tokens, stream=True, include_usage=include_usage))
            if reply.status == 200:
                return OpenAIStream(conn, reply, default_model=self.model, parse_whole=self._parse)
            try:
                raw = reply.read(65536)
            except (http.client.HTTPException, OSError):
                raw = b""
            finally:
                conn.close()
            if include_usage and reply.status == 400 and b"stream_options" in raw.lower():
                continue
            raise self._error_for(reply.status, raw, reply.msg)
        raise LLMBadResponse("the answer service rejected the request")        # not reached: the second round always raises

    def _open(self, body: dict):
        parts = urlsplit(self.base_url)
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=self.timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=self.timeout)
        try:
            conn.request("POST", f"{parts.path.rstrip('/')}/chat/completions", body=json.dumps(body).encode("utf-8"), headers=self._headers("text/event-stream"))
            return conn, conn.getresponse()
        except (TimeoutError, socket.timeout):
            conn.close()
            raise LLMTimeout("the answer service did not answer in time") from None
        except (http.client.HTTPException, OSError):
            conn.close()
            raise LLMUnavailable("the answer service could not be reached") from None

    # ---- reading what came back

    def _parse(self, raw: bytes, headers: dict | None = None) -> Completion:
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMBadResponse("the answer service sent something that is not a chat answer") from None
        if finish is not None and not isinstance(finish, str):
            raise LLMBadResponse("the answer service sent something that is not a chat answer")
        if content is None and finish == "length":
            content = ""                                   # a reasoning model can spend the whole cap thinking: no text, and the finish reason says so
        if not isinstance(content, str):
            raise LLMBadResponse("the answer service sent something that is not a chat answer")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        model = data.get("model") if isinstance(data.get("model"), str) and data.get("model") else self.model
        details = usage.get("completion_tokens_details")
        return Completion(text=content, finish_reason=finish, model=model, prompt_tokens=_count(usage.get("prompt_tokens")),
                          completion_tokens=_count(usage.get("completion_tokens")),
                          reasoning_tokens=_count(details.get("reasoning_tokens")) if isinstance(details, dict) else None, rate_limit_headers=headers or {})

    def _for_status(self, error: urllib.error.HTTPError):
        try:
            raw = error.read(65536)
        except OSError:
            raw = b""
        return self._error_for(error.code, raw, error.headers)

    def _error_for(self, status: int, raw: bytes, headers):
        """The error for an HTTP status (shared by `complete` and `stream`, so they cannot drift). The provider's own text is read here and never passed on."""
        try:
            detail = json.loads(raw)
            info = detail.get("error") if isinstance(detail, dict) else None
        except (ValueError, OSError, AttributeError):
            info = None
        message = str(info.get("message", "")) if isinstance(info, dict) else ""
        code = str(info.get("code", "")) if isinstance(info, dict) else ""
        if status in (401, 403):
            return LLMAuthError("the answer service refused the key")
        if status == 404:
            return LLMModelNotFound("the answer service does not know this model or address")
        if status == 408:
            return LLMTimeout("the answer service did not answer in time")
        if status == 413 or (status == 400 and (code == "context_length_exceeded" or _CONTEXT_WORDS.search(message))):
            return LLMContextTooLong("the request is larger than the model accepts")
        if status == 429:
            return LLMRateLimited(retry_after=_retry_after(headers.get("retry-after") if headers else None, message),
                                  rate_limit_headers=rate_limit_headers(headers.items()) if headers else {})
        if status >= 500:
            return LLMUnavailable("the answer service failed on its side")
        if 300 <= status < 400:
            return LLMUnavailable("the answer address redirects somewhere else, which is never followed")
        return LLMBadResponse("the answer service rejected the request")


class OpenAIStream:
    """A streamed answer being read. Iterate it for `ThinkingPiece` / `TextPiece` events and a last `StreamDone`; `close()` stops it from any thread.

    The connection belongs to whoever iterates: it is closed when the iteration ends, fails or is closed. `close()` first shuts the socket (that ends a read that is
    waiting for the service at once, and tells the service to stop), then lets the reader close the connection (or closes it here when nobody is reading).
    """

    def __init__(self, conn, reply, *, default_model: str, parse_whole):
        self._conn, self._reply = conn, reply
        self._default_model, self._parse_whole = default_model, parse_whole
        self._closed = False
        self._iterating = False
        self._lock = threading.Lock()
        self.rate_limit_headers = rate_limit_headers(reply.getheaders())

    def __repr__(self) -> str:
        return f"OpenAIStream(model={self._default_model!r})"

    def __iter__(self):
        return self._events()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sock = self._conn.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)                  # ends a blocked read at once; the service sees the connection close
                except OSError:
                    pass
            nobody_reading = not self._iterating
        if nobody_reading:
            self._conn.close()

    def _events(self):
        with self._lock:
            self._iterating = True
        try:
            if self._closed:
                return
            if "json" in (self._reply.getheader("content-type") or "").lower() and "event-stream" not in (self._reply.getheader("content-type") or "").lower():
                yield from self._whole_answer()
            else:
                yield from self._pieces()
        finally:
            self._conn.close()

    def _whole_answer(self):
        """A service that ignored `stream` and sent one JSON answer: the same events, all at once."""
        try:
            raw = self._reply.read(MAX_REPLY_BYTES)
        except (TimeoutError, socket.timeout):
            raise LLMTimeout("the answer service did not answer in time") from None
        except (http.client.HTTPException, OSError):
            raise LLMUnavailable("the connection to the answer service failed") from None
        completion = self._parse_whole(raw)
        filt = ThinkTagFilter()
        for kind, text in filt.feed(completion.text) + filt.finish():
            yield ThinkingPiece() if kind == "thinking" else TextPiece(text)
        yield StreamDone(finish_reason=completion.finish_reason, model=completion.model, prompt_tokens=completion.prompt_tokens,
                         completion_tokens=completion.completion_tokens)

    def _pieces(self):
        filt, saw_thinking_field = ThinkTagFilter(), False
        finish, usage, model, total, done_marker = None, None, None, 0, False
        while True:
            try:
                line = self._reply.readline(MAX_STREAM_LINE_BYTES + 1)
            except (TimeoutError, socket.timeout):
                if self._closed:
                    return
                raise LLMTimeout("the answer service stopped answering") from None
            except (http.client.HTTPException, OSError, ValueError):
                if self._closed:
                    return                                           # we stopped it ourselves
                raise LLMUnavailable("the connection to the answer service ended before the answer was complete") from None
            if self._closed:
                return
            if not line:
                break
            if len(line) > MAX_STREAM_LINE_BYTES:
                raise LLMBadResponse("the answer service sent a line that is far too long")
            total += len(line)
            if total > MAX_REPLY_BYTES:
                raise LLMBadResponse("the answer service sent far too much")
            try:
                text = line.decode("utf-8").strip()
            except UnicodeDecodeError:
                raise LLMBadResponse("the answer service sent something that is not text") from None
            if not text.startswith("data:"):
                continue                                             # blank lines, ": keep-alive" comments and event / id / retry fields say nothing we need
            payload = text[5:].strip()
            if payload == "[DONE]":
                done_marker = True
                break
            try:
                chunk = read_chunk(json.loads(payload))
            except ValueError:
                raise LLMBadResponse("the answer service sent something that is not a chat answer") from None
            if chunk.error:
                raise LLMUnavailable("the answer service reported an error while answering")
            model = model or chunk.model
            finish = chunk.finish_reason or finish
            usage = chunk.usage or usage
            if chunk.thinking:
                saw_thinking_field = True
                yield ThinkingPiece()
            if chunk.text:
                if saw_thinking_field:
                    yield TextPiece(chunk.text)                      # this model has its own thinking field: an inline tag in its answer is just text
                else:
                    for kind, piece in filt.feed(chunk.text):
                        yield ThinkingPiece() if kind == "thinking" else TextPiece(piece)
        if self._closed:
            return
        if not done_marker and finish is None:
            raise LLMUnavailable("the answer service ended the answer before it was complete")
        if not saw_thinking_field:
            for kind, piece in filt.finish():
                yield ThinkingPiece() if kind == "thinking" else TextPiece(piece)
        yield StreamDone(finish_reason=finish, model=model or self._default_model,
                         prompt_tokens=usage.prompt_tokens if usage else None, completion_tokens=usage.completion_tokens if usage else None,
                         reasoning_tokens=usage.reasoning_tokens if usage else None)


def rate_limit_headers(pairs) -> dict:
    """Only the service's `x-ratelimit-*` and `retry-after` headers, lower case names, values cut short: nothing else a service sends is kept."""
    return {name.lower(): value[:MAX_HEADER_VALUE_CHARS] for name, value in pairs if name.lower().startswith("x-ratelimit-") or name.lower() == "retry-after"}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _retry_after(header: str | None, message: str) -> float | None:
    """The wait the service asked for, in seconds: the Retry-After header (a number), else "try again in 7.66s" in its message. Capped at an hour."""
    if header is not None and re.fullmatch(r"\d+(\.\d+)?", header.strip()):
        return min(float(header), MAX_RETRY_AFTER)
    found = _WAIT_IN_TEXT.search(message or "")
    if not found:
        return None
    total = sum(float(number) * _SECONDS[unit.lower()] for number, unit in _WAIT_PART.findall(found.group(1)))
    return min(total, MAX_RETRY_AFTER) if total > 0 else None
