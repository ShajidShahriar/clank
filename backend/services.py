"""The app's services, built by a factory and handed out through FastAPI dependencies (task 8.1).

Nothing here runs at import time: no data folder, no database, no Ollama. `create_app` (main.py) builds the real `Services` when the app starts; a test
builds its own with the fakes, or replaces `get_services` in `app.dependency_overrides`.

The model is warmed in a background thread at startup (a failed warm-up takes about 7 s with the real client because of its retries, and `/health` must
answer at once). A failure is not fatal: the app runs "degraded", and a request that needs the embedder (`get_ready_embedder`) tries again, but not more
often than every `retry_after` seconds, otherwise every request would wait those 7 s while Ollama is off.
"""
import os
import threading
import time

import datadir
import db
from embedding import OllamaEmbedder
from embedding.errors import EmbeddingError
from llm.profiles import llm_from_env
from fastapi import Depends, Request
from vectorstore import open_project_store


class Services:
    def __init__(self, embedder, *, connect=None, store_factory=None, clock=time.monotonic, retry_after: float = 5.0, llm_factory=None):
        self.embedder = embedder
        self._connect = connect                                    # None: db.get_connection (opened for use across threads), looked up at call time so a test can redirect the database
        self._store_factory = store_factory or (lambda project_id: open_project_store(datadir.data_dir(), project_id))
        self._clock = clock
        self._retry_after = retry_after
        self._llm_factory = llm_factory or (lambda: llm_from_env(os.environ))      # (profile, client); reads the environment when first needed
        self._llm = None
        self._llm_lock = threading.Lock()
        self._stores = {}
        self._stores_lock = threading.Lock()
        self._warm_lock = threading.Lock()
        self._warm = False
        self._error: EmbeddingError | None = None
        self._tried_at: float | None = None
        self._thread: threading.Thread | None = None

    # ---- database and stores

    def connect(self):
        """A NEW connection: one per request or worker, never shared between threads."""
        return self._connect() if self._connect else db.get_connection(shared_across_threads=True)

    def store_for(self, project_id: int):
        with self._stores_lock:
            if project_id not in self._stores:
                self._stores[project_id] = self._store_factory(project_id)
            return self._stores[project_id]

    def llm_setup(self):
        """The answer model: (profile, client). Raises LLMNotConfigured (and tries again next time) while it is not set up; a success is kept."""
        with self._llm_lock:
            if self._llm is None:
                self._llm = self._llm_factory()
            return self._llm

    def forget_store(self, project_id: int) -> None:
        """Empty a project's vector store and drop it from the cache (the project is being deleted)."""
        self.store_for(project_id).clear()
        with self._stores_lock:
            self._stores.pop(project_id, None)

    # ---- the embedder

    def ensure_warm(self) -> None:
        """Make sure the embedder has warmed up. Raises the stored error without trying again if the last try was less than `retry_after` ago."""
        with self._warm_lock:
            if self._warm:
                return
            now = self._clock()
            if self._error is not None and self._tried_at is not None and now - self._tried_at < self._retry_after:
                raise self._error
            self._tried_at = now
            try:
                self.embedder.warmup()
            except EmbeddingError as error:
                self._error = error
                raise
            self._warm, self._error = True, None

    def status(self) -> dict:
        if self._warm:
            return {"embedder": "ready", "model": self.embedder.model_name, "detail": None}
        if self._error is not None:
            return {"embedder": "degraded", "model": None, "detail": str(self._error)}
        return {"embedder": "warming", "model": None, "detail": None}

    # ---- startup

    def start(self) -> None:
        """Create the tables, clear the vector stores if the chunk tables were reset, and start warming the model in the background."""
        reset = db.init_db()
        if reset:
            conn = self.connect()
            try:
                project_ids = [row[0] for row in conn.execute("SELECT id FROM projects")]
            finally:
                conn.close()
            for project_id in project_ids:
                self.store_for(project_id).clear()
        self._thread = threading.Thread(target=self._warm_up, name="clank-warmup", daemon=True)
        self._thread.start()

    def _warm_up(self) -> None:
        try:
            self.ensure_warm()
        except EmbeddingError:
            pass                                                   # not fatal: status() says "degraded" and a request tries again

    def wait_for_warmup(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


def default_services() -> Services:
    return Services(OllamaEmbedder())


# ---- FastAPI dependencies

def get_services(request: Request) -> Services:
    return request.app.state.services


def get_conn(services: Services = Depends(get_services)):
    conn = services.connect()
    try:
        yield conn
    finally:
        conn.close()


def get_ready_embedder(services: Services = Depends(get_services)):
    services.ensure_warm()                                         # raises the EmbeddingError if Ollama is still unavailable (8.2 turns it into a 503)
    return services.embedder
