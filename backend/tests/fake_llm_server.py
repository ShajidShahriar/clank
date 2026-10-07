"""A tiny local HTTP server that plays an OpenAI-compatible chat service, so the REAL client class can be tested over a real socket.
`handler(request)` returns (status, headers, body); `request` has .path, .headers (lower-case keys), .json. Every request is recorded in `.requests`."""
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Request:
    def __init__(self, path, headers, raw):
        self.path, self.headers, self.raw = path, headers, raw
        try:
            self.json = json.loads(raw) if raw else None
        except ValueError:
            self.json = None


def good_reply(text="the answer", finish="stop", prompt=11, completion=7, model="test-model"):
    return 200, {}, {"id": "x", "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
                     "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}}


class Stream:
    """A streamed reply, played step by step: data(obj or text), raw(bytes), pause(seconds), drop() (cut the connection), hang(seconds) (say nothing until the client
    closes or the time is up). `chunked` False sends a close-delimited body (no framing at all)."""

    def __init__(self, *steps, chunked=True):
        self.steps, self.chunked = steps, chunked


def data(item):
    return ("data", item)


def raw(payload: bytes):
    return ("raw", payload)


def pause(seconds):
    return ("pause", seconds)


def drop():
    return ("drop",)


def hang(seconds=30):
    return ("hang", seconds)


def groq_stream(pieces=("Yes", ", 17 is prime."), thinking=("Need", " check"), finish="stop", usage=(90, 38, 7), delay=0.0, model="openai/gpt-oss-120b"):
    """The chunks Groq's gpt-oss really sends (see tests/test_llm_chunks.py): an empty first chunk, thinking pieces, answer pieces, a finish chunk, a usage chunk with
    no choices, then [DONE]. `usage` is (prompt, all output, thinking) or None."""
    def chunk(delta=None, finish_reason=None, **more):
        return {"id": "chatcmpl-x", "object": "chat.completion.chunk", "model": model, "system_fingerprint": "fp",
                "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **more}
    steps = [data(chunk({"role": "assistant", "content": ""})), pause(delay)]
    for text in thinking:
        steps += [data(chunk({"reasoning": text, "channel": "analysis"})), pause(delay)]
    for text in pieces:
        steps += [data(chunk({"content": text})), pause(delay)]
    steps.append(data(chunk({}, finish)))
    if usage is not None:
        steps.append(data(chunk(None, usage={"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": usage[0] + usage[1],
                                             "completion_tokens_details": {"reasoning_tokens": usage[2]}}, service_tier="on_demand")))
    steps.append(data("[DONE]"))
    return Stream(*steps)


class FakeLLMServer:
    def __init__(self, handler=None):
        self.requests = []
        self.client_closed = threading.Event()               # set when a streaming client was seen to close its end of the connection
        self.handler = handler or (lambda request: good_reply())
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                request = Request(self.path, {k.lower(): v for k, v in self.headers.items()}, self.rfile.read(length))
                owner.requests.append(request)
                status, headers, body = owner.handler(request)
                if isinstance(body, Stream):
                    return self.play(status, headers, body)
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if "content-type" not in {h.lower() for h in headers}:
                    self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST                                   # a followed redirect arrives as a GET: it must be seen and recorded too

            def play(self, status, headers, stream):
                self.protocol_version = "HTTP/1.1" if stream.chunked else "HTTP/1.0"
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if "content-type" not in {h.lower() for h in headers}:
                    self.send_header("content-type", "text/event-stream")
                if stream.chunked:
                    self.send_header("transfer-encoding", "chunked")
                else:
                    self.close_connection = True
                self.end_headers()

                def write(payload: bytes):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(payload), payload) if stream.chunked else payload)
                    self.wfile.flush()

                def gone() -> bool:
                    ready, _, _ = select.select([self.connection], [], [], 0)
                    if not ready:
                        return False
                    try:
                        return self.connection.recv(1, socket.MSG_PEEK) == b""
                    except OSError:
                        return True
                try:
                    for step in stream.steps:
                        kind = step[0]
                        if kind == "data":
                            item = step[1]
                            write(("data: " + (item if isinstance(item, str) else json.dumps(item)) + "\n\n").encode())
                        elif kind == "raw":
                            write(step[1])
                        elif kind == "pause":
                            if step[1]:
                                time.sleep(step[1])
                        elif kind == "drop":
                            self.connection.shutdown(socket.SHUT_RDWR)
                            owner.client_closed.set()
                            return
                        elif kind == "hang":
                            end = time.monotonic() + step[1]
                            while time.monotonic() < end and not gone():
                                time.sleep(0.01)
                        if gone():
                            owner.client_closed.set()
                            return
                    if stream.chunked:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    owner.client_closed.set()

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
