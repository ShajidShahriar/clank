"""The project endpoints. Plain `def`: FastAPI runs them on its thread pool, so nothing blocks the event loop."""
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, StringConstraints

import projects
from jobs import IndexJobs, ProjectNotFound
from routes_index import get_jobs
from services import Services, get_conn, get_services

router = APIRouter()


class NewProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Annotated[str, StringConstraints(max_length=projects.MAX_PATH_CHARS)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None


def _state(jobs: IndexJobs):
    return lambda project_id: jobs.status(project_id)["state"]


@router.post("/projects", status_code=201)
def create_project(body: NewProject, conn=Depends(get_conn), jobs: IndexJobs = Depends(get_jobs)):
    project_id = projects.add_project(conn, body.path, body.name)
    return projects.get_project_summary(conn, project_id, _state(jobs))


@router.get("/projects")
def list_projects(conn=Depends(get_conn), jobs: IndexJobs = Depends(get_jobs)):
    return projects.list_projects(conn, _state(jobs))


@router.get("/projects/{project_id}")
def get_project(project_id: int, conn=Depends(get_conn), jobs: IndexJobs = Depends(get_jobs)):
    summary = projects.get_project_summary(conn, project_id, _state(jobs))
    if summary is None:
        raise ProjectNotFound(f"no project {project_id}")
    return summary


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, conn=Depends(get_conn), jobs: IndexJobs = Depends(get_jobs), services: Services = Depends(get_services)):
    if projects.get_project_summary(conn, project_id, _state(jobs)) is None:
        raise ProjectNotFound(f"no project {project_id}")
    if jobs.is_running(project_id):
        raise projects.ProjectBusy(f"project {project_id} is being indexed")
    services.forget_store(project_id)          # vectors first: if the rows then fail to go, the next index run simply re-embeds (it heals a lost vector)
    projects.remove_project(conn, project_id)
    jobs.forget(project_id)
    return Response(status_code=204)
