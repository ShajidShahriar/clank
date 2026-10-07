"""Download the embedding model through Ollama (`POST /api/pull`), in the background, so the first-run screen can offer a button instead of a terminal command.

Safety: the puller is made for ONE model and never takes a name from a caller, so the endpoint cannot be used to make Ollama download something else. It talks only
to the address it was given (the embedder's own, this computer's Ollama). Every failure becomes a sentence for people; a URL, an address or a traceback never reaches
the status. Plain stdlib, like the embedder's client.
"""
import http.client
import json
import threading
import urllib.error
import urllib.request

MAX_ERROR_CHARS = 400
READ_TIMEOUT_SECONDS = 120.0            # Ollama sends a progress line every moment while it downloads: two minutes of silence means a dead connection

NOT_REACHABLE = "Ollama could not be reached. Make sure Ollama is running, then try again."
STOPPED_EARLY = "Ollama stopped before the download finished. Try again."


class PullNotAvailable(RuntimeError):
    """This setup has no Ollama behind it, so there is nothing to download from."""


def _idle(model: str) -> dict:
    return {"state": "idle", "model": model, "message": None, "percent": None, "completed": None, "total": None, "error": None}


class ModelPuller:
    def __init__(self, model: str, base_url: str = "http://localhost:11434", *, opener=urllib.request.urlopen, on_done=None, read_timeout: float = READ_TIMEOUT_SECONDS):
        self._model = model
        self._url = f"{base_url.rstrip('/')}/api/pull"
        self._opener = opener
        self._on_done = on_done
        self._read_timeout = read_timeout
        self._lock = threading.Lock()
        self._state = _idle(model)
        self._layers: dict[str, tuple[int, int]] = {}          # digest -> (completed, total): the model is several files, each with its own numbers
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def start(self) -> dict:
        """Begin the download unless one is already running. Returns the status right away."""
        with self._lock:
            if self._state["state"] == "pulling":
                return dict(self._state)
            self._state = {**_idle(self._model), "state": "pulling"}
            self._layers = {}
            self._thread = threading.Thread(target=self._run, name="clank-model-pull", daemon=True)
            self._thread.start()
            return dict(self._state)

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # ---- the background thread

    def _run(self) -> None:
        request = urllib.request.Request(self._url, data=json.dumps({"model": self._model, "stream": True}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener(request, timeout=self._read_timeout) as reply:
                for raw in reply:
                    try:
                        item = json.loads(raw)
                    except ValueError:
                        continue                                # a blank or damaged line says nothing: skip it
                    if isinstance(item, dict) and self._apply(item):
                        break
        except urllib.error.HTTPError as problem:
            self._fail(self._http_message(problem))
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            self._fail(NOT_REACHABLE)
        with self._lock:
            finished = self._state["state"]
        if finished == "pulling":
            self._fail(STOPPED_EARLY)                           # the stream ended without `success`
        elif finished == "done" and self._on_done is not None:
            try:
                self._on_done()
            except Exception:
                pass                                            # the model IS downloaded: a problem afterwards must not look like a failed download

    def _apply(self, item: dict) -> bool:
        """Take one line from Ollama. True means the stream is finished (success or error)."""
        with self._lock:
            if "error" in item:
                self._state.update(state="failed", error=str(item["error"])[:MAX_ERROR_CHARS])
                return True
            status = item.get("status")
            if isinstance(status, str):
                self._state["message"] = status
            digest, total, completed = item.get("digest"), item.get("total"), item.get("completed")
            if isinstance(digest, str) and isinstance(total, int) and total > 0:
                done_bytes = completed if isinstance(completed, int) and 0 <= completed <= total else 0
                self._layers[digest] = (done_bytes, total)
                got, size = sum(c for c, _ in self._layers.values()), sum(t for _, t in self._layers.values())
                percent = round(100 * got / size)                   # never above 100: a layer's completed bytes are kept between 0 and its total
                self._state.update(completed=got, total=size, percent=max(percent, self._state["percent"] or 0))     # a new layer must not move the bar back
            if status == "success":
                self._state.update(state="done", percent=100, error=None)
                return True
            return False

    def _fail(self, message: str) -> None:
        with self._lock:
            self._state.update(state="failed", error=message[:MAX_ERROR_CHARS])

    @staticmethod
    def _http_message(problem: urllib.error.HTTPError) -> str:
        try:
            said = json.loads(problem.read()).get("error")
        except (ValueError, AttributeError, OSError):
            said = None
        return said if isinstance(said, str) and said else f"Ollama answered with an error (HTTP {problem.code}). Try again."
