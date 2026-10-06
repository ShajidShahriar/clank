"""`POST /projects/{id}/context` (task 8.5): the text to give the LLM, plus what a person needs to judge it.

Defaults (the user's, 2026-10-04): k = 10, max_tokens = 4000, the demotion of tests and changelogs ON (search's default policy), NO relevance floor (decision 13:
the best score is returned, nothing is cut silently). Values outside the ranges are refused with a 422, never clamped. Order of checks: the project (404),
then the model (503), then the index (409). Plain `def`: the embedding of the question blocks, and FastAPI runs it on its thread pool.
The response holds only paths relative to the repo. `ranking_note` and `calibration_note` are for the person; they are never in `text`.
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

import db
from jobs import ProjectNotFound
from search import DEFAULT_DEMOTION, build_context
from services import Services, get_conn, get_services

router = APIRouter()

DEFAULT_K, MAX_K = 10, 30
DEFAULT_MAX_TOKENS, MIN_MAX_TOKENS, MAX_MAX_TOKENS = 4000, 500, 16000
MAX_QUESTION_CHARS = 2000        # far below what the model can read; a longer question is a mistake, and the cap keeps a 502 from ever happening for length


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")          # a misspelled field is an error, not a silently ignored setting
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)]
    k: int = Field(default=DEFAULT_K, ge=1, le=MAX_K, strict=True)
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, ge=MIN_MAX_TOKENS, le=MAX_MAX_TOKENS, strict=True)


def _passage(p) -> dict:
    return {"path": p.rel_path, "symbol": p.symbol, "parent": p.parent, "kind": p.kind, "start_line": p.start_line, "end_line": p.end_line,
            "score": round(p.score, 4), "stale": p.stale, "complete": p.complete, "narrowed": p.narrowed}


def _dropped(p) -> dict:
    return {"path": p.rel_path, "start_line": p.start_line, "end_line": p.end_line, "score": round(p.score, 4)}


@router.post("/projects/{project_id}/context")
def project_context(project_id: int, body: ContextRequest, conn=Depends(get_conn), services: Services = Depends(get_services)):
    project = db.get_project(conn, project_id)
    if project is None:
        raise ProjectNotFound(f"no project {project_id}")
    services.ensure_warm()                                         # 503 if Ollama is still unavailable
    ctx = build_context(conn, project_id, project["repo_path"], services.embedder, services.store_for(project_id), body.question, body.k, body.max_tokens,
                        test_policy=DEFAULT_DEMOTION, cutoff=None)
    return {
        "text": ctx.text,
        "passages": [_passage(p) for p in ctx.passages],
        "dropped": [_dropped(p) for p in ctx.dropped],
        "hidden_files": [{"path": path, "reason": reason} for path, reason in sorted(ctx.hidden_files.items())],
        "stale_files": ctx.stale_files,
        "deleted_files": ctx.deleted_files,
        "tokens_used": ctx.tokens_used,
        "over_budget": ctx.over_budget,
        "best_score": round(max(p.score for p in ctx.passages), 4) if ctx.passages else None,       # the highest score returned (the first passage can score less: tests are moved down)
        "k": body.k,
        "max_tokens": body.max_tokens,
        "ranking_note": ctx.ranking_note,
        "calibration_note": ctx.calibration_note,
    }
