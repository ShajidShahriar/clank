"""`POST /projects/{id}/answer` (the answer step): retrieve the code like /context does, ask the answer model, return the answer with its sources.

Order of checks: the project (404), the model is set up (503: before anything is embedded), consent (403: a remote model needs `allow_remote: true`, because
code excerpts leave the machine), Ollama (503), the index (409), then the model. The code budget and the answer cap are the PROFILE's: the caller cannot raise them.
When nothing matches, the model is not called. Plain `def`: the blocking calls run on FastAPI's thread pool.
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

import conversations
import db
from answer import NO_MATCH_TEXT, ConsentRequired, build_messages, history_messages
from jobs import ProjectNotFound
from routes_context import DEFAULT_K, MAX_K, MAX_QUESTION_CHARS, _dropped, _passage
from search import DEFAULT_DEMOTION, build_context
from services import Services, get_conn, get_services

router = APIRouter()


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")          # no max_tokens, no model, no address: those belong to the profile
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)]
    k: int = Field(default=DEFAULT_K, ge=1, le=MAX_K, strict=True)
    allow_remote: bool = Field(default=False, strict=True)
    conversation_id: int | None = Field(default=None, strict=True, ge=1)


@router.post("/projects/{project_id}/answer")
def answer_question(project_id: int, body: AnswerRequest, conn=Depends(get_conn), services: Services = Depends(get_services)):
    project = db.get_project(conn, project_id)
    if project is None:
        raise ProjectNotFound(f"no project {project_id}")
    if body.conversation_id is not None and conversations.get_conversation(conn, project_id, body.conversation_id) is None:
        raise conversations.ConversationNotFound(f"no conversation {body.conversation_id}")      # before anything is embedded or sent
    profile, llm = services.llm_setup()                # 503 if the model is not set up
    if not profile.is_local and not body.allow_remote:
        raise ConsentRequired("the answer model is a remote service")
    services.ensure_warm()                             # 503 if Ollama is still unavailable
    ctx = build_context(conn, project_id, project["repo_path"], services.embedder, services.store_for(project_id), body.question, body.k, profile.context_tokens,
                        test_policy=DEFAULT_DEMOTION, cutoff=None)
    completion = None
    if ctx.passages:
        history = [] if body.conversation_id is None else history_messages(conversations.recent_turns(conn, body.conversation_id))
        completion = llm.complete(build_messages(body.question, ctx.text, history), max_output_tokens=profile.max_output_tokens)
    truncated = completion is not None and completion.finish_reason == "length"
    notice = None
    if truncated:
        notice = ("The model used its whole answer allowance before writing anything. Try again or ask a narrower question." if not completion.text
                  else "The answer was cut off by the length limit.")
    result = {
        "answer": NO_MATCH_TEXT if completion is None else completion.text,
        "truncated": truncated,
        "finish_reason": None if completion is None else completion.finish_reason,
        "notice": notice,
        "llm_called": completion is not None,
        "model": None if completion is None else completion.model,
        "profile": profile.name,
        "usage": None if completion is None else {"prompt_tokens": completion.prompt_tokens, "completion_tokens": completion.completion_tokens},
        "sent_off_machine": completion is not None and not profile.is_local,
        "sources": [_passage(p) for p in ctx.passages],
        "dropped": [_dropped(p) for p in ctx.dropped],
        "hidden_files": [{"path": path, "reason": reason} for path, reason in sorted(ctx.hidden_files.items())],
        "stale_files": ctx.stale_files,
        "deleted_files": ctx.deleted_files,
        "context_tokens_used": ctx.tokens_used,
        "context_budget": profile.context_tokens,
        "over_budget": ctx.over_budget,
        "best_score": round(max(p.score for p in ctx.passages), 4) if ctx.passages else None,       # the highest score returned (the first passage can score less: tests are moved down)
        "k": body.k,
        "ranking_note": ctx.ranking_note,
        "calibration_note": ctx.calibration_note,
    }
    if body.conversation_id is not None:
        conversations.add_exchange(conn, body.conversation_id, body.question, result["answer"], {k: v for k, v in result.items() if k != "answer"})
    return {**result, "conversation_id": body.conversation_id}
