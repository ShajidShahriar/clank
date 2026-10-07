"""Manual script, not a test: ask a chat service two tiny questions with streaming on, and print a SUMMARY of how it streams: where its thinking pieces are,
whether it reports thinking tokens, where the usage numbers appear, and what its rate-limit headers say. It prints no key and no full header list (header NAMES
only, except the content type, retry-after and the x-ratelimit-* headers) and hides request ids.
Run from backend/ with the key in your own terminal (not in a file, not in the chat):
    read -s GROQ_API_KEY; export GROQ_API_KEY; uv run python scripts/capture_llm_stream.py
It makes two calls of at most 400 and 600 answer tokens (about 1,000 tokens in all). Options: --base-url, --model, --key-env.
"""
import argparse
import http.client
import json
import os
import re
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm.base import host_is_local  # noqa: E402

ID = re.compile(r"(chatcmpl-|req_|resp_|gen-)[A-Za-z0-9_-]+")
SHOWN_HEADERS = ("content-type", "retry-after")
CALLS = (
    ("low effort", {"reasoning_effort": "low", "max_completion_tokens": 400}, "Is 17 a prime number? Answer in two short sentences."),
    ("medium effort", {"reasoning_effort": "medium", "max_completion_tokens": 600}, "What is the sum of the first 20 odd numbers? Answer in one sentence."),
)


def hide(text: str) -> str:
    return ID.sub(r"\1<id>", text)


def one_call(base_url: str, model: str, key: str | None, label: str, extra: dict, question: str) -> None:
    parts = urlsplit(base_url)
    conn_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_class(parts.hostname, parts.port, timeout=60)
    body = {"model": model, "stream": True, "stream_options": {"include_usage": True}, **extra,
            "messages": [{"role": "system", "content": "Answer briefly."}, {"role": "user", "content": question}]}
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "Clank/0.1"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    started = time.monotonic()
    conn.request("POST", parts.path.rstrip("/") + "/chat/completions", body=json.dumps(body), headers=headers)
    reply = conn.getresponse()
    print(f"\n===== {label}: status {reply.status}, headers after {time.monotonic() - started:.2f}s")
    names = []
    for name, value in reply.getheaders():
        low = name.lower()
        if low in SHOWN_HEADERS or low.startswith("x-ratelimit-"):
            print(f"  {low}: {value}")
        else:
            names.append(low)
    print("  other header names:", ", ".join(sorted(names)))
    if reply.status != 200:
        print("  (not a stream) body:", hide(reply.read(2000).decode("utf-8", "replace"))[:600])
        return
    raw, delta_keys, first, usage, finishes, extra_keys = [], set(), {}, None, [], set()
    counts = {"reasoning": 0, "content": 0}
    while True:
        line = reply.readline()
        if not line:
            break
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if not text.startswith("data:"):
            continue
        payload = text[5:].strip()
        raw.append(payload)
        if payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        now = time.monotonic() - started
        extra_keys |= {k for k in chunk if k not in ("id", "object", "created", "model", "choices", "system_fingerprint")}
        if chunk.get("usage"):
            usage = chunk["usage"]
            first.setdefault("usage", now)
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            delta_keys |= set(delta)
            if choice.get("finish_reason"):
                finishes.append(choice["finish_reason"])
            for field in ("reasoning", "reasoning_content"):
                if delta.get(field):
                    counts["reasoning"] += 1
                    first.setdefault("reasoning (" + field + ")", now)
            if delta.get("content"):
                counts["content"] += 1
                first.setdefault("content", now)
    print(f"  stream ended after {time.monotonic() - started:.2f}s with {len(raw)} data lines (the last is [DONE]: {raw[-1:] == ['[DONE]']})")
    print("  keys seen in a delta:", sorted(delta_keys))
    print("  other top-level keys in chunks:", sorted(extra_keys))
    print("  pieces with thinking text:", counts["reasoning"], "| pieces with answer text:", counts["content"])
    print("  first seen at:", {k: round(v, 2) for k, v in first.items()})
    print("  finish reasons:", finishes)
    print("  usage object:", json.dumps(usage) if usage else "NONE REPORTED")
    print("  raw lines (first 3, then last 2):")
    for item in raw[:3] + ["..."] + raw[-2:]:
        print("   ", hide(item)[:420])


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a summary of how a chat service streams.")
    parser.add_argument("--base-url", default="https://api.groq.com/openai/v1")
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--key-env", default="GROQ_API_KEY")
    options = parser.parse_args()
    key = (os.environ.get(options.key_env) or "").strip() or None
    if key is None and not host_is_local(options.base_url):
        print(f"Set {options.key_env} in your terminal first (read -s {options.key_env}; export {options.key_env}).", file=sys.stderr)
        return 2
    if key and urlsplit(options.base_url).scheme == "http" and not host_is_local(options.base_url):
        print("A key is only sent over https to a remote address.", file=sys.stderr)
        return 2
    for label, extra, question in CALLS:
        try:
            one_call(options.base_url, options.model, key, label, extra, question)
        except (OSError, http.client.HTTPException) as problem:
            print(f"\n===== {label}: the call failed: {type(problem).__name__}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
