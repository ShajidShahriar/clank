"""Background indexing jobs (task 8.3).

One job per project, run on a worker thread that opens its OWN SQLite connection and closes it at the end. Progress comes from the callback `index_project`
already has; cancelling sets a flag that `index_project` asks before every file, so a cancelled run stops cleanly between files (and, like any stopped run,
deletes and sweeps nothing). The registry here refuses a second start for the same project at once; the flock inside `index_project` covers other
processes (that case shows up as a failed job with the code `index_already_running`).

What a status holds never contains a raw exception text: the real reason goes to the log, the caller gets a fixed sentence.
"""
import logging
import threading
from pathlib import Path

import db
from indexing import IndexAlreadyRunning, index_project

log = logging.getLogger("clank.jobs")

STOPPED_MESSAGES = {
    "embedder": "Indexing stopped because Ollama could not be used. Start Ollama (and pull the embedding model) and index again: work already done is kept.",
}


class ProjectNotFound(LookupError):
    """No project with this id."""


class RepoNotFound(RuntimeError):
    """The project's folder no longer exists."""


class NoIndexRunning(RuntimeError):
    """Cancel was asked but no indexing job is running for this project."""


class _Job:
    def __init__(self, project_id):
        self.project_id = project_id
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.state = "running"
        self.files_done = 0
        self.files_total = 0
        self.current_file: str | None = None
        self.message: str | None = None
        self.skipped: list = []
        self.error: dict | None = None
        self.stopped_kind: str | None = None                       # why a "stopped" job stopped: "embedder" or "too_many_skips"


class IndexJobs:
    def __init__(self, services, lock_dir=None):
        self._services = services
        self._lock_dir = lock_dir                                  # None: the data folder's lock folder (looked up when a job runs)
        self._jobs: dict[int, _Job] = {}
        self._guard = threading.Lock()

    # ---- the API

    def start(self, project_id: int) -> dict:
        conn = self._services.connect()
        try:
            project = db.get_project(conn, project_id)
        finally:
            conn.close()
        if project is None:
            raise ProjectNotFound(f"no project {project_id}")
        repo_path = project["repo_path"]
        if not Path(repo_path).is_dir():
            raise RepoNotFound(f"project {project_id}: folder missing")
        with self._guard:
            current = self._jobs.get(project_id)
            if current is not None and current.thread is not None and current.thread.is_alive():
                raise IndexAlreadyRunning(f"project {project_id} is already being indexed")
            job = self._jobs[project_id] = _Job(project_id)
            job.thread = threading.Thread(target=self._run, args=(job, repo_path), name=f"clank-index-{project_id}", daemon=True)
            job.thread.start()
        return self._describe(job)

    def status(self, project_id: int) -> dict:
        with self._guard:
            job = self._jobs.get(project_id)
            return {"state": "idle", "project_id": project_id} if job is None else self._describe(job)

    def cancel(self, project_id: int) -> dict:
        with self._guard:
            job = self._jobs.get(project_id)
            if job is None or job.state not in ("running", "cancelling"):
                raise NoIndexRunning(f"no indexing is running for project {project_id}")
            job.cancel.set()
            job.state = "cancelling"
            return self._describe(job)

    def is_running(self, project_id: int) -> bool:
        with self._guard:
            job = self._jobs.get(project_id)
            return job is not None and job.thread is not None and job.thread.is_alive()

    def wait(self, project_id: int, timeout: float = 10.0) -> None:
        with self._guard:
            job = self._jobs.get(project_id)
        if job is not None and job.thread is not None:
            job.thread.join(timeout)

    def shutdown(self, timeout: float = 10.0) -> None:
        """The app is closing: ask every running job to stop between files and give the workers a moment to finish."""
        with self._guard:
            jobs = list(self._jobs.values())
            for job in jobs:
                job.cancel.set()
        for job in jobs:
            if job.thread is not None:
                job.thread.join(timeout)

    # ---- the worker

    def _describe(self, job: _Job) -> dict:
        return {"state": job.state, "project_id": job.project_id, "files_done": job.files_done, "files_total": job.files_total,
                "current_file": job.current_file, "message": job.message, "skipped": list(job.skipped), "error": job.error,
                "stopped_kind": job.stopped_kind}

    def _progress(self, job: _Job, done: int, total: int, rel: str | None) -> None:
        with self._guard:
            job.files_done, job.files_total, job.current_file = done, total, rel

    def _finish(self, job: _Job, state: str, message: str | None = None, error: dict | None = None, skipped: list | None = None) -> None:
        with self._guard:
            job.state, job.message, job.error, job.current_file = state, message, error, None
            job.skipped = skipped or []

    def _run(self, job: _Job, repo_path: str) -> None:
        conn = None
        try:
            conn = self._services.connect()                        # this thread's OWN connection
            report = index_project(conn, job.project_id, repo_path, self._services.embedder, self._services.store_for(job.project_id),
                                   progress=lambda done, total, rel: self._progress(job, done, total, rel), on_start=lambda total: self._progress(job, 0, total, None), lock_dir=self._lock_dir, cancel=job.cancel.is_set)
            skipped = [{"path": rel, "reason": reason} for rel, reason in report.skipped]
            if report.stopped is None:
                self._finish(job, "done", report.describe(), skipped=skipped)
            elif report.stopped.kind == "cancelled":
                self._finish(job, "cancelled", f"Indexing cancelled after {report.stopped.files_done} of {report.files_seen} files. Work already done is kept.", skipped=skipped)
            else:
                log.info("indexing project %s stopped (%s): %s", job.project_id, report.stopped.kind, report.stopped.reason)
                message = STOPPED_MESSAGES.get(report.stopped.kind) or report.describe()
                job.stopped_kind = report.stopped.kind
                self._finish(job, "stopped", message, skipped=skipped)
        except IndexAlreadyRunning:
            self._finish(job, "failed", "This project is already being indexed.", {"code": "index_already_running", "message": "This project is already being indexed."})
        except Exception:
            log.exception("indexing project %s failed", job.project_id)
            text = "Something went wrong inside Clank while indexing. The details are in the backend log."
            self._finish(job, "failed", text, {"code": "internal_error", "message": text})
        finally:
            if conn is not None:
                conn.close()
