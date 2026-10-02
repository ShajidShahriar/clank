"""A tiny fake of Ollama's POST /api/embed, run on a local port, so the real client can be tested
without Ollama or a model. It can misbehave on purpose (cold start, wrong counts, unknown model).

It is built from the documented API: request {model, input: str | [str], truncate, keep_alive, options},
answer {model, embeddings: [[...], ...]} in input order, or {error: "..."} with a 4xx/5xx status.
Vectors come from FakeEmbedder, so the same text always gives the same vector.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from embedding import FakeEmbedder


class FakeOllama:
    def __init__(self, dim=8, models=("test-model",), fail_first=0, fail_status=503,
                 max_chars=None, wrong_count=False, wrong_dim=False):
        self.dim = dim
        self.models = models
        self.fail_first = fail_first      # answer the first N requests with fail_status (0 = drop the connection)
        self.fail_status = fail_status
        self.max_chars = max_chars        # simulated context limit, in characters
        self.wrong_count = wrong_count    # return one vector too few
        self.wrong_dim = wrong_dim        # return vectors one number too long
        self.requests = []                # every parsed request body, in order
        self._fake = FakeEmbedder(dim=dim)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                status, payload = outer.answer(body)
                if status is None:
                    self.connection.close()  # no HTTP answer at all
                    return
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.01), daemon=True)  # default 0.5s makes every shutdown slow

    def answer(self, body):
        if self.fail_first > 0:
            self.fail_first -= 1
            return (self.fail_status or None), {"error": "model is loading"}
        if body.get("model") not in self.models:
            return 404, {"error": f"model \"{body.get('model')}\" not found, try pulling it first"}
        inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
        if self.max_chars is not None:
            too_long = any(len(t) > self.max_chars for t in inputs)
            if too_long and body.get("truncate") is False:
                return 400, {"error": "the input length exceeds the context length"}
            inputs = [t[: self.max_chars] for t in inputs]  # what real Ollama does when truncate is true: silent cut
        vectors = [self._fake._vector(t) for t in inputs]
        if self.wrong_dim:
            vectors = [v + [0.0] for v in vectors]
        if self.wrong_count:
            vectors = vectors[:-1]
        return 200, {"model": body["model"], "embeddings": vectors}

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
