"""The indexing endpoints (task 8.3). Plain `def`: FastAPI runs them on its thread pool, so nothing blocks the event loop."""
from fastapi import APIRouter, Depends, Request

from jobs import IndexJobs

router = APIRouter()


def get_jobs(request: Request) -> IndexJobs:
    return request.app.state.jobs


@router.post("/projects/{project_id}/index", status_code=202)
def start_index(project_id: int, jobs: IndexJobs = Depends(get_jobs)):
    return jobs.start(project_id)


@router.get("/projects/{project_id}/index")
def index_status(project_id: int, jobs: IndexJobs = Depends(get_jobs)):
    return jobs.status(project_id)


@router.post("/projects/{project_id}/index/cancel", status_code=202)
def cancel_index(project_id: int, jobs: IndexJobs = Depends(get_jobs)):
    return jobs.cancel(project_id)


@router.get("/startup-sync")
def startup_sync_status(request: Request):
    """What the sweep at app start is doing: off, running, done, skipped (no model), stopped, or failed."""
    return request.app.state.startup_sync.status()
