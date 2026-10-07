"""`POST /projects/{id}/source`: the lines of one indexed file, for the source viewer. All the rules are in source_view.py. A POST with a body (not a GET with a
query) because the window's door to the backend allows no query string and no dots in a path: the file's path travels in the body, never in the address."""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

import source_view
from services import get_conn

router = APIRouter()


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Annotated[str, StringConstraints(min_length=1, max_length=source_view.MAX_PATH_CHARS)]
    start_line: int = Field(strict=True, ge=1)
    end_line: int = Field(strict=True, ge=1)


@router.post("/projects/{project_id}/source")
def project_source(project_id: int, body: SourceRequest, conn=Depends(get_conn)):
    return source_view.read_source(conn, project_id, body.path, body.start_line, body.end_line)
