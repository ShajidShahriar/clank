"""An incremental index at app start, for projects that already have an index (task 8.4).

A project nobody ever indexed is NOT started on its own: that is the user's choice. The sweep goes through the projects one at a time, in id order, with the
same job machinery as the Index button (so progress, cancel and the one-job-per-project rule all apply). It is skipped when the model is not available at
startup (it would fail the same way for every project), and it stops when a project stops on an Ollama problem. A project whose folder is gone is skipped.
A bug in the sweep is logged and never reaches the app.
"""
import logging
import threading

import chunk_store
from indexing import IndexAlreadyRunning
from jobs import RepoNotFound

log = logging.getLogger("clank.sync")


class StartupSync:
    def __init__(self, services, jobs):
        self._services = services
        self._jobs = jobs
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state, self._projects, self._reason = "off", [], None
        self._guard = threading.Lock()

    def start(self) -> None:
        self._set("running")
        self._thread = threading.Thread(target=self._run, name="clank-startup-sync", daemon=True)
        self._thread.start()

    def status(self) -> dict:
        with self._guard:
            return {"state": self._state, "projects": list(self._projects), "reason": self._reason}

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float = 10.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _set(self, state: str, reason: str | None = None, projects: list | None = None) -> None:
        with self._guard:
            self._state, self._reason = state, reason
            if projects is not None:
                self._projects = projects

    def _run(self) -> None:
        try:
            self._sweep()
        except Exception:
            log.exception("the startup sync failed")
            self._set("failed", "Something went wrong inside Clank while updating the indexes. The details are in the backend log.")

    def _sweep(self) -> None:
        self._services.wait_for_warmup()
        if self._services.status()["embedder"] != "ready":
            self._set("skipped", "Ollama is not available, so the indexes were not updated. Start Ollama and press Index to update them.")
            return
        conn = self._services.connect()
        try:
            project_ids = chunk_store.indexed_project_ids(conn)
        finally:
            conn.close()
        self._set("running", projects=project_ids)
        for project_id in project_ids:
            if self._stop.is_set():
                self._set("stopped", "The app was closed before every project was updated.")
                return
            try:
                self._jobs.start(project_id)
            except IndexAlreadyRunning:
                pass                                               # the user started it first: wait for that job, then go on
            except RepoNotFound:
                log.warning("project %s: its folder is gone; not updated", project_id)
                continue
            while self._jobs.is_running(project_id):
                self._jobs.wait(project_id, 0.5)
            after = self._jobs.status(project_id)
            if after["state"] == "stopped" and after.get("stopped_kind") == "embedder":
                self._set("stopped", "Ollama stopped working, so the remaining projects were not updated. Start Ollama and press Index to update them.")
                return
        self._set("stopped" if self._stop.is_set() else "done", "The app was closed before every project was updated." if self._stop.is_set() else None)
