"""The real embedder: Ollama's local HTTP API (POST /api/embed). Default model qwen3-embedding:0.6b.

Decisions this file carries (docs/indexing-decisions.md): batches of 16-32 (#9), keep the model loaded
(#10), documents as-is but queries wrapped in an instruction (#7), and never let Ollama cut text silently
(#7): every request sends truncate=false, so over-limit text is an error and not a quietly shortened chunk.
"""
import http.client
import json
import time
import urllib.error
import urllib.request

from .base import Vector
from .errors import BadResponse, EmbeddingError, ModelNotFound, OllamaUnavailable

DEFAULT_MODEL = "qwen3-embedding:0.6b"
DEFAULT_DIM = 1024
QUERY_TASK = "Given a question about a codebase, retrieve the code that answers it"


class OllamaEmbedder:
    def __init__(self, model: str = DEFAULT_MODEL, dim: int = DEFAULT_DIM, *,
                 base_url: str = "http://localhost:11434", batch_size: int = 16, keep_alive: str = "30m",
                 num_ctx: int = 8192, timeout: float = 120.0, retries: int = 3, backoff: float = 1.0,
                 sleep=time.sleep):
        if batch_size < 1 or retries < 0:
            raise ValueError("batch_size must be at least 1 and retries at least 0")
        self.model_name = model
        self.dim = dim
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx  # Ollama's default context is small; ask for the size our chunks need
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep     # a parameter so tests do not really wait

    def embed_documents(self, texts: list[str], ids: list[str] | None = None) -> list[Vector]:
        if ids is not None and len(ids) != len(texts):
            raise ValueError(f"{len(texts)} texts but {len(ids)} ids")
        for t in texts:
            if not isinstance(t, str):
                raise TypeError(f"can only embed str, got {type(t).__name__}")
        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(texts[start:start + self.batch_size]))
        return vectors

    def embed_query(self, text: str) -> Vector:
        return self._embed_batch([f"Instruct: {QUERY_TASK}\nQuery: {text}"])[0]

    def warmup(self) -> None:
        """Load the model into memory now, so the first real question does not wait for it (decision 10)."""
        self._embed_batch(["warmup"])

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        reply = self._post({
            "model": self.model_name,
            "input": texts,
            "truncate": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx},
        })
        vectors = reply.get("embeddings") if isinstance(reply, dict) else None
        if not isinstance(vectors, list):
            raise BadResponse(f"Ollama's answer has no 'embeddings' list: {str(reply)[:200]}")
        if len(vectors) != len(texts):  # a short or long answer would pair vectors with the wrong texts
            raise BadResponse(f"Ollama returned {len(vectors)} vectors for {len(texts)} texts")
        for v in vectors:
            if len(v) != self.dim:
                raise BadResponse(
                    f"Ollama returned a vector with {len(v)} numbers, but this embedder expects {self.dim} "
                    f"(is '{self.model_name}' the model the dimension was set for?)")
        return vectors

    def _post(self, body: dict) -> dict:
        """POST to /api/embed. Retries cold starts and dropped connections; fails at once on real errors."""
        request = urllib.request.Request(
            f"{self.base_url}/api/embed", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        problem = ""
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff * 2 ** (attempt - 1))
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as e:
                message = self._error_message(e)
                if e.code == 404 and "model" in message:
                    raise ModelNotFound(
                        f"Ollama does not have the model '{self.model_name}'. Run: ollama pull {self.model_name}") from e
                if e.code < 500:
                    raise EmbeddingError(f"Ollama answered {e.code}: {message}") from e
                problem = f"HTTP {e.code}: {message}"  # 5xx: usually the model is still loading, so try again
            except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
                problem = str(getattr(e, "reason", e))
        raise OllamaUnavailable(
            f"Ollama at {self.base_url} did not answer after {self.retries} retries (last problem: {problem}). "
            f"Is it running? Start it with: ollama serve")

    @staticmethod
    def _error_message(e: urllib.error.HTTPError) -> str:
        try:
            return json.loads(e.read()).get("error", "")
        except (ValueError, AttributeError):
            return ""
