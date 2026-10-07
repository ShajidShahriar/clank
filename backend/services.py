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
from embedding.errors import EmbeddingError, ModelNotFound, OllamaUnavailable
from llm.profiles import make_llm
from model_pull import ModelPuller
from llm.settings import clean_key, load_selection, save_selection, selection_to_profile
from fastapi import Depends, Request
from vectorstore import open_project_store


class Services:
    def __init__(self, embedder, *, connect=None, store_factory=None, clock=time.monotonic, retry_after: float = 5.0, llm_factory=None, environ=None, puller=None):
        self.embedder = embedder
        self.puller = puller                                        # downloads the embedding model through Ollama; None when there is no Ollama behind this setup
        self._connect = connect                                    # None: db.get_connection (opened for use across threads), looked up at call time so a test can redirect the database
        self._store_factory = store_factory or (lambda project_id: open_project_store(datadir.data_dir(), project_id))
        self._clock = clock
        self._retry_after = retry_after
        self._llm_factory = llm_factory                                # tests: (profile, client) made by hand; None: from the settings and the key held here
        self._environ = os.environ if environ is None else environ
        self._llm = None
        self._llm_key = None                                           # in memory only: never written to disk, never logged
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
        """The answer model: (profile, client), from the saved choice and the key held here (else the environment). Raises LLMNotConfigured (and tries again next
        time) while it is not set up; a success is kept until the choice or the key changes."""
        with self._llm_lock:
            if self._llm is None:
                if self._llm_factory is not None:
                    self._llm = self._llm_factory()
                else:
                    profile = selection_to_profile(self.llm_selection())
                    self._llm = (profile, make_llm(profile, self._environ, key=self._llm_key))
            return self._llm

    def llm_selection(self):
        conn = self.connect()
        try:
            return load_selection(conn)
        finally:
            conn.close()

    def set_llm_selection(self, selection) -> None:
        conn = self.connect()
        try:
            save_selection(conn, selection)
        finally:
            conn.close()
        with self._llm_lock:
            self._llm = None                                           # the next answer is made with the new choice

    def set_llm_key(self, key) -> None:
        """Hold a key in memory (None clears it). Raises InvalidKey for a malformed one, leaving the old key in place."""
        cleaned = None if key is None else clean_key(key)
        with self._llm_lock:
            self._llm_key = cleaned
            self._llm = None

    def llm_key_state(self, profile) -> tuple[bool, str | None]:
        """(is a key available, where from: "memory" or "env") for a profile. A profile that takes no key has none."""
        if not profile.api_key_env:
            return False, None
        if self._llm_key:
            return True, "memory"
        return (True, "env") if (self._environ.get(profile.api_key_env) or "").strip() else (False, None)

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

    def retry_warmup(self) -> None:
        """Try the warm-up again NOW, without waiting out the retry gap: the model was just downloaded. A problem that is still there stays in the status."""
        with self._warm_lock:
            self._tried_at = None
        try:
            self.ensure_warm()
        except EmbeddingError:
            pass

    def status(self) -> dict:
        """`problem` says WHY the embedder is not ready, for the first-run screen: ollama_unavailable, model_not_found or embedding_error (None when ready or warming)."""
        if self._warm:
            return {"embedder": "ready", "model": self.embedder.model_name, "detail": None, "problem": None}
        if self._error is not None:
            problem = "ollama_unavailable" if isinstance(self._error, OllamaUnavailable) else "model_not_found" if isinstance(self._error, ModelNotFound) else "embedding_error"
            return {"embedder": "degraded", "model": None, "detail": str(self._error), "problem": problem}
        return {"embedder": "warming", "model": None, "detail": None, "problem": None}

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
    embedder = OllamaEmbedder()
    services = Services(embedder)
    services.puller = ModelPuller(embedder.model, embedder.base_url, on_done=services.retry_warmup)      # the embedder's own model and address: nothing a caller can change
    return services


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
