"""`POST /projects/{id}/answer` (the answer step) and `POST /projects/{id}/answer/stream` (the same, written live): retrieve the code like /context does, ask the answer
model, return the answer with its sources.

Order of checks (the SAME code for both routes, `_prepare`): the project (404), the conversation (404), the model is set up (503: before anything is embedded), consent
(403: a remote model needs `allow_remote: true`, because code excerpts leave the machine), Ollama (503), the index (409), the search, then the model. The code budget and
the answer cap are the PROFILE's: the caller cannot raise them. When nothing matches, the model is not called. Plain `def` routes: the blocking calls run on FastAPI's
thread pool.

The stream route opens the model's connection BEFORE it answers, so every refusal is an ordinary error response, exactly as in the normal route. Only then does it send
NDJSON (one JSON object per line): `start`, then `stage` / `thinking` / `delta` events as the model produces them, then exactly ONE last event, `done` (the whole answer, in
the shape of the normal reply plus timings, thinking and tokens) or `error` (a failure in the middle, described like the normal route would). It saves the answer only
when the model finished it. The generator is ASYNC and reads the model in worker threads that can be abandoned: when the client leaves, the generator is cancelled and its
`finally` closes the model's connection, which wakes the blocked read and stops the model (a plain generator would leave the connection open and the model paid to finish).
"""
import json
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

import conversations
import db
import usage_recording
from answer import NO_MATCH_TEXT, ConsentRequired, build_messages, history_messages
from answer_stats import build_timings, token_summary
from api_errors import llm_error_info
from jobs import ProjectNotFound
from llm import LLMError, StreamDone, TextPiece, ThinkingPiece
from routes_context import DEFAULT_K, MAX_K, MAX_QUESTION_CHARS, _dropped, _passage
from search import DEFAULT_DEMOTION, build_context
from services import Services, get_conn, get_services

router = APIRouter()
log = logging.getLogger("clank.api")
_clock = time.monotonic             # the tests move this by hand to make every stage time exact
_END = object()


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")          # no max_tokens, no model, no address: those belong to the profile
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)]
    k: int = Field(default=DEFAULT_K, ge=1, le=MAX_K, strict=True)
    allow_remote: bool = Field(default=False, strict=True)
    conversation_id: int | None = Field(default=None, strict=True, ge=1)


@dataclass
class Prepared:
    """Everything both routes have after the checks and the search, before the model is asked."""
    profile: Any
    llm: Any
    ctx: Any
    history: list
    begin: float
    searched: float


def _prepare(project_id: int, body: AnswerRequest, conn, services: Services) -> Prepared:
    begin = _clock()
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
    searched = _clock()
    history = [] if body.conversation_id is None else history_messages(conversations.recent_turns(conn, body.conversation_id))
    return Prepared(profile, llm, ctx, history, begin, searched)


def _messages(prep: Prepared, body: AnswerRequest) -> list[dict]:
    return build_messages(body.question, prep.ctx.text, prep.history)


def _context_fields(prep: Prepared, body: AnswerRequest) -> dict:
    """What is known once the search is done: the same fields the normal reply has about the code that was found."""
    ctx = prep.ctx
    return {
        "sources": [_passage(p) for p in ctx.passages],
        "dropped": [_dropped(p) for p in ctx.dropped],
        "hidden_files": [{"path": path, "reason": reason} for path, reason in sorted(ctx.hidden_files.items())],
        "stale_files": ctx.stale_files,
        "deleted_files": ctx.deleted_files,
        "context_tokens_used": ctx.tokens_used,
        "context_budget": prep.profile.context_tokens,
        "over_budget": ctx.over_budget,
        "best_score": round(max(p.score for p in ctx.passages), 4) if ctx.passages else None,       # the highest score returned (the first passage can score less: tests are moved down)
        "k": body.k,
        "ranking_note": ctx.ranking_note,
        "calibration_note": ctx.calibration_note,
    }


def _result(prep: Prepared, body: AnswerRequest, *, called: bool, text: str, finish_reason, model, prompt_tokens, completion_tokens, reasoning_tokens,
            requested, first_piece, first_thinking, first_text, finished, thinking_pieces: int) -> dict:
    """The whole answer, in the shape of the normal reply (plus timings, thinking and tokens). Used for both routes, so they cannot drift."""
    truncated = called and finish_reason == "length"
    notice = None
    if truncated:
        notice = ("The model used its whole answer allowance before writing anything. Try again or ask a narrower question." if not text
                  else "The answer was cut off by the length limit.")
    profile = prep.profile
    return {
        "answer": text if called else NO_MATCH_TEXT,
        "truncated": truncated,
        "finish_reason": finish_reason,
        "notice": notice,
        "llm_called": called,
        "model": model if called else None,
        "profile": profile.name,
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens} if called else None,
        "sent_off_machine": called and not profile.is_local,
        **_context_fields(prep, body),
        "timings": build_timings(begin=prep.begin, searched=prep.searched, requested=requested, first_piece=first_piece,
                                 first_thinking=first_thinking, first_text=first_text, finished=finished),
        "thinking": {"seen": thinking_pieces > 0, "pieces": thinking_pieces},
        "tokens": (token_summary(reported_thinking=reasoning_tokens, reported_completion=completion_tokens, thinking_pieces=thinking_pieces, text=text)
                   if called else {"thinking": None, "answer": None, "estimated": False}),
    }


def _extras(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "answer"}          # what is saved with the answer's text: its sources, notes, timings, tokens...


@router.post("/projects/{project_id}/answer")
def answer_question(project_id: int, body: AnswerRequest, conn=Depends(get_conn), services: Services = Depends(get_services)):
    prep = _prepare(project_id, body, conn, services)
    completion, requested = None, None
    if prep.ctx.passages:
        requested = _clock()
        try:
            completion = prep.llm.complete(_messages(prep, body), max_output_tokens=prep.profile.max_output_tokens)
        except LLMError as failure:
            usage_recording.record_headers(services, prep.profile.name, getattr(failure, "rate_limit_headers", None))      # a 429's numbers matter most
            raise
        usage_recording.record_call(services, prep.profile, messages=_messages(prep, body), kind="answer", outcome="done", model=completion.model,
                                    prompt_tokens=completion.prompt_tokens, completion_tokens=completion.completion_tokens, reasoning_tokens=completion.reasoning_tokens,
                                    thinking_pieces=0, text=completion.text, headers=completion.rate_limit_headers)
    finished = _clock()
    result = _result(prep, body, called=completion is not None, text="" if completion is None else completion.text,
                     finish_reason=None if completion is None else completion.finish_reason, model=None if completion is None else completion.model,
                     prompt_tokens=None if completion is None else completion.prompt_tokens, completion_tokens=None if completion is None else completion.completion_tokens,
                     reasoning_tokens=None if completion is None else completion.reasoning_tokens, requested=requested, first_piece=None, first_thinking=None,
                     first_text=None, finished=finished, thinking_pieces=0)
    if body.conversation_id is not None:
        conversations.add_exchange(conn, body.conversation_id, body.question, result["answer"], _extras(result))
    return {**result, "conversation_id": body.conversation_id}


# ---- the same answer, written live

def _line(event: dict) -> bytes:
    return (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")


def _next_event(iterator):
    """Runs in a worker thread: the next event of the model's stream, or _END."""
    return next(iterator, _END)


def _save(services: Services, body: AnswerRequest, result: dict) -> None:
    conn = services.connect()                         # its own connection: the request's one may already be closed while the response is still being sent
    try:
        conversations.add_exchange(conn, body.conversation_id, body.question, result["answer"], _extras(result))
    finally:
        conn.close()


@router.post("/projects/{project_id}/answer/stream")
def answer_stream(project_id: int, body: AnswerRequest, conn=Depends(get_conn), services: Services = Depends(get_services)):
    prep = _prepare(project_id, body, conn, services)
    stream, requested = None, None
    if prep.ctx.passages:
        requested = _clock()
        try:
            stream = prep.llm.stream(_messages(prep, body), max_output_tokens=prep.profile.max_output_tokens)         # a refusal raises HERE: an ordinary error response
        except LLMError as failure:
            usage_recording.record_headers(services, prep.profile.name, getattr(failure, "rate_limit_headers", None))
            raise
    return StreamingResponse(_events(prep, body, services, stream, requested), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                             background=BackgroundTask(stream.close) if stream is not None else None)         # (closing twice is fine: the generator's `finally` is the main way)


async def _events(prep: Prepared, body: AnswerRequest, services: Services, stream, requested):
    clock = _clock
    first_piece = first_thinking = first_text = None
    thinking_pieces = 0
    parts: list[str] = []
    finish: StreamDone | None = None
    outcome = "stopped"                                      # what it is if the generator is cancelled before the end: the person (or the window) left
    try:
        yield _line({"type": "start", "stage": "waiting", "search_ms": _at(prep, prep.searched), "llm_called": stream is not None, "profile": prep.profile.name, "sent_off_machine": stream is not None and not prep.profile.is_local,
                     "conversation_id": body.conversation_id, **_context_fields(prep, body)})
        if stream is not None:
            iterator = iter(stream)
            while True:
                event = await anyio.to_thread.run_sync(_next_event, iterator, abandon_on_cancel=True)     # abandoned on cancel; the `finally` below wakes the thread
                if event is _END:
                    break
                now = clock()
                if first_piece is None:
                    first_piece = now
                if isinstance(event, ThinkingPiece):
                    thinking_pieces += 1
                    if first_thinking is None:
                        first_thinking = now
                        yield _line({"type": "stage", "stage": "thinking", "at_ms": _at(prep, now)})
                    yield _line({"type": "thinking", "pieces": thinking_pieces})
                elif isinstance(event, TextPiece):
                    if event.text == "":
                        continue
                    if first_text is None:
                        first_text = now
                        yield _line({"type": "stage", "stage": "writing", "at_ms": _at(prep, now)})
                    parts.append(event.text)
                    yield _line({"type": "delta", "text": event.text})
                elif isinstance(event, StreamDone):
                    finish = event
                    break
        outcome = "done"
        finished = clock()
        text = "".join(parts)
        called = stream is not None
        result = _result(prep, body, called=called, text=text, finish_reason=finish.finish_reason if finish else None,
                         model=finish.model if finish else prep.profile.model, prompt_tokens=finish.prompt_tokens if finish else None,
                         completion_tokens=finish.completion_tokens if finish else None, reasoning_tokens=finish.reasoning_tokens if finish else None,
                         requested=requested, first_piece=first_piece, first_thinking=first_thinking, first_text=first_text, finished=finished,
                         thinking_pieces=thinking_pieces)
        saved = False
        if body.conversation_id is not None:
            try:
                await anyio.to_thread.run_sync(_save, services, body, result)
                saved = True
            except Exception as problem:                     # noqa: BLE001 - the answer was shown; say it was not kept
                log.error("could not save the streamed answer", exc_info=problem)
                result["notice"] = ((result["notice"] + " ") if result["notice"] else "") + "This answer could not be saved to the conversation."
        yield _line({"type": "done", **result, "conversation_id": body.conversation_id, "saved": saved})
    except Exception as failure:                              # noqa: BLE001 - becomes the one last event; cancellation is not an Exception and passes through
        outcome = "failed"
        info = llm_error_info(failure)
        if info is None:
            log.error("unexpected error while streaming an answer", exc_info=failure)
            info = {"status": 500, "code": "internal_error", "message": "Something went wrong inside Clank. The details are in the backend log."}
        event = {"type": "error", "error": {"code": info["code"], "message": info["message"], "status": info["status"]}}
        if info.get("retry_after") is not None:
            event["retry_after"] = info["retry_after"]
        yield _line(event)
    finally:
        if stream is not None:
            stream.close()                                    # on every end, and when the client has left: this is what stops the model
            usage_recording.record_call(services, prep.profile, messages=_messages(prep, body), kind="answer", outcome=outcome,       # tokens were spent whatever the end was
                                        model=finish.model if finish else None, prompt_tokens=finish.prompt_tokens if finish else None,
                                        completion_tokens=finish.completion_tokens if finish else None, reasoning_tokens=finish.reasoning_tokens if finish else None,
                                        thinking_pieces=thinking_pieces, text="".join(parts), headers=stream.rate_limit_headers)


def _at(prep: Prepared, moment: float) -> int:
    return max(0, round((moment - prep.begin) * 1000))
