"""A tiny local HTTP server that plays an OpenAI-compatible chat service, so the REAL client class can be tested over a real socket.
`handler(request)` returns (status, headers, body); `request` has .path, .headers (lower-case keys), .json. Every request is recorded in `.requests`."""
import json
import threading
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


class FakeLLMServer:
    def __init__(self, handler=None):
        self.requests = []
        self.handler = handler or (lambda request: good_reply())
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                request = Request(self.path, {k.lower(): v for k, v in self.headers.items()}, self.rfile.read(length))
                owner.requests.append(request)
                status, headers, body = owner.handler(request)
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
