"""The real embedder: Ollama's local HTTP API (POST /api/embed). Default model qwen3-embedding:0.6b.

Decisions this file carries (docs/indexing-decisions.md): batches of 16-32 (#9), keep the model loaded
(#10), documents as-is but queries wrapped in an instruction (#7), and never let Ollama cut text silently
(#7): every request sends truncate=false, so over-limit text is an error and not a quietly shortened chunk.
"""
import http.client
import json
import math
import time
import urllib.error
import urllib.request

from .base import Vector
from .errors import BadResponse, EmbeddingError, EmbeddingTooLong, ModelNotFound, OllamaUnavailable

DEFAULT_MODEL = "qwen3-embedding:0.6b"
DEFAULT_DIM = 1024
QUERY_TASK = "Given a question about a codebase, retrieve the code that answers it"


class OllamaEmbedder:
    def __init__(self, model: str = DEFAULT_MODEL, dim: int = DEFAULT_DIM, *,
                 base_url: str = "http://localhost:11434", batch_size: int = 16, keep_alive: str = "30m",
                 num_ctx: int = 8192, timeout: float = 60.0, retries: int = 3, backoff: float = 1.0,
                 sleep=time.sleep):
        if batch_size < 1 or retries < 0:
            raise ValueError("batch_size must be at least 1 and retries at least 0")
        self.model = model       # the tag, exactly as Ollama knows it: what every request asks for
        self._digest = None      # learned in warmup(); see model_name
        self.dim = dim
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx  # Ollama's default context is small; ask for the size our chunks need
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep     # a parameter so tests do not really wait

    @property
    def model_name(self) -> str:
        """The identity stored on every chunk row and on the vector store: the tag plus the first 12 characters of the model's digest
        (`qwen3-embedding:0.6b@ac6da0dfba84`, the same short ID `ollama list` shows). A tag can be pulled again and point at different
        weights; the digest makes that look like a different model, so the vectors are rebuilt. Known only after warmup(); until then
        (or if Ollama does not list the model) it is the plain tag."""
        return f"{self.model}@{self._digest}" if self._digest else self.model

    def embed_documents(self, texts: list[str], ids: list[str] | None = None) -> list[Vector]:
        if ids is not None and len(ids) != len(texts):
            raise ValueError(f"{len(texts)} texts but {len(ids)} ids")
        for t in texts:
            if not isinstance(t, str):
                raise TypeError(f"can only embed str, got {type(t).__name__}")
        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            try:
                vectors.extend(self._embed_batch(batch))
            except EmbeddingTooLong as too_long:
                labels = ids[start:start + self.batch_size] if ids else [f"text #{start + i}" for i in range(len(batch))]
                raise self._name_the_offenders(batch, labels, too_long) from None
        return vectors

    def _name_the_offenders(self, batch: list[str], labels: list[str], original: EmbeddingTooLong) -> EmbeddingTooLong:
        """Ollama only says "something in this batch is too long". Ask again one text at a time to find which."""
        bad = []
        for text, label in zip(batch, labels):
            try:
                self._embed_batch([text])
            except EmbeddingTooLong:
                bad.append(label)
        if not bad:  # the batch failed but every text passed alone: do not guess, report what Ollama said
            return original
        return EmbeddingTooLong(
            f"{len(bad)} chunk(s) are too long for '{self.model}' (context {self.num_ctx} tokens): "
            f"{', '.join(bad)}. The chunker caps chunk size, so the cap or the token estimate is wrong.", bad)

    def embed_query(self, text: str) -> Vector:
        return self._embed_batch([f"Instruct: {QUERY_TASK}\nQuery: {text}"])[0]

    def warmup(self) -> None:
        """Load the model into memory now, so the first real question does not wait for it (decision 10), and learn its digest."""
        self._embed_batch(["warmup"])
        self._digest = self._find_digest(self._request("GET", "/api/tags"))

    def _find_digest(self, reply) -> str | None:
        """The short digest of our model in an /api/tags answer; None if Ollama does not list it. A garbage answer is an error:
        silently going without a digest would silently switch off the protection it gives."""
        models = reply.get("models") if isinstance(reply, dict) else None
        if not isinstance(models, list) or not all(isinstance(m, dict) for m in models):
            raise BadResponse(f"Ollama's model list is not a list of models: {str(reply)[:200]}")
        wanted = {self.model} | ({f"{self.model}:latest"} if ":" not in self.model else set())
        for entry in models:
            if entry.get("name") in wanted or entry.get("model") in wanted:
                digest = entry.get("digest")
                if not isinstance(digest, str):
                    raise BadResponse(f"Ollama lists '{self.model}' without a usable digest: {str(entry)[:200]}")
                return digest.removeprefix("sha256:")[:12] or None
        return None

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        reply = self._post({
            "model": self.model,
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
        checked = []
        for v in vectors:
            if not isinstance(v, list):
                raise BadResponse(f"Ollama returned a vector that is not a list: {str(v)[:100]}")
            if len(v) != self.dim:
                raise BadResponse(
                    f"Ollama returned a vector with {len(v)} numbers, but this embedder expects {self.dim} "
                    f"(is '{self.model}' the model the dimension was set for?)")
            # bool is a number to Python (True == 1), so refuse it by name; NaN and infinity would poison the vector store
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v):
                raise BadResponse("Ollama returned a vector that contains something other than finite numbers")
            checked.append([float(x) for x in v])
        return checked

    def _post(self, body: dict) -> dict:
        return self._request("POST", "/api/embed", body)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        """Ask Ollama. Retries cold starts and dropped connections; fails at once on real errors."""
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"}, method=method)
        problem = ""
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff * 2 ** (attempt - 1))
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                try:
                    return json.loads(raw)
                except ValueError as e:  # not JSON, empty, or not even text
                    raise BadResponse(f"Ollama's answer is not valid JSON: {raw[:100]!r}") from e
            except urllib.error.HTTPError as e:
                message = self._error_message(e)
                if e.code == 404 and "model" in message:
                    raise ModelNotFound(
                        f"Ollama does not have the model '{self.model}'. Run: ollama pull {self.model}") from e
                if e.code == 400 and self._says_too_long(message):
                    raise EmbeddingTooLong(f"Ollama says the input is too long for '{self.model}': {message}") from e
                if e.code < 500:
                    raise EmbeddingError(f"Ollama answered {e.code}: {message}") from e
                problem = f"HTTP {e.code}: {message}"  # 5xx: usually the model is still loading, so try again
            except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
                problem = str(getattr(e, "reason", e))
        raise OllamaUnavailable(
            f"Ollama at {self.base_url} did not answer after {self.retries} retries (last problem: {problem}). "
            f"Is it running? Start it with: ollama serve")

    @staticmethod
    def _says_too_long(message: str) -> bool:
        # Real wording, confirmed against Ollama 0.35 (task 3f): "the input length exceeds the context length".
        # Kept exact on purpose: a loose match would blame the chunks for an unrelated 400.
        return "exceeds the context length" in message.lower()

    @staticmethod
    def _error_message(e: urllib.error.HTTPError) -> str:
        try:
            return json.loads(e.read()).get("error", "")
        except (ValueError, AttributeError):
            return ""
