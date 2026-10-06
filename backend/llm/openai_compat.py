"""A client for the OpenAI-compatible chat protocol: POST <base_url>/chat/completions. Groq, Ollama (/v1), LM Studio, OpenRouter and OpenAI itself speak it,
so one class covers every local and hosted model Clank will ever be pointed at. Standard library only (no new dependency).

Safety decisions:
- the key goes in the Authorization header and nowhere else, is never in an error or a repr, and is refused over plain http to a remote address;
- a redirect is NEVER followed (urllib would resend the Authorization header to the new address);
- the provider's own error text is not passed on (it can hold an organization id, a key fragment or an internal URL): only its status and, for a rate limit,
  the wait are read;
- the client names itself `Clank/<version>`: some edges (Groq's among them) refuse the default Python user agent.
"""
import http.client
import json
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .base import Completion, check_cap, check_messages, host_is_local
from .errors import (LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMModelNotFound, LLMRateLimited, LLMTimeout, LLMUnavailable)

USER_AGENT = "Clank/0.1"
LIMIT_PARAMS = ("max_tokens", "max_completion_tokens")
MAX_REPLY_BYTES = 4_000_000
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
        reserved = {"model", "messages", "stream", *LIMIT_PARAMS}
        body = {**{k: v for k, v in self.extra_body.items() if k not in reserved},
                "model": self.model, "messages": messages, self.output_limit_param: max_output_tokens, "stream": False}
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as reply:
                raw = reply.read(MAX_REPLY_BYTES)
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
        return self._parse(raw)

    # ---- reading what came back

    def _parse(self, raw: bytes) -> Completion:
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
        return Completion(text=content, finish_reason=finish, model=model, prompt_tokens=_count(usage.get("prompt_tokens")),
                          completion_tokens=_count(usage.get("completion_tokens")))

    def _for_status(self, error: urllib.error.HTTPError):
        status = error.code
        try:
            detail = json.loads(error.read(65536))
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
            return LLMRateLimited(retry_after=_retry_after(error.headers.get("retry-after") if error.headers else None, message))
        if status >= 500:
            return LLMUnavailable("the answer service failed on its side")
        if 300 <= status < 400:
            return LLMUnavailable("the answer address redirects somewhere else, which is never followed")
        return LLMBadResponse("the answer service rejected the request")


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
