"""One JSON shape for every error the API returns: {"error": {"code": "...", "message": "..."}} (task 8.2).

The code is a stable word the frontend can switch on; the message is for a person and says what to do. The status and code come from the exception's CLASS,
never from its text. Messages for Ollama errors are fixed sentences: the real exception text can hold a URL, a path or a piece of Ollama's reply, so it goes
to the log only. An unexpected error is a 500 `internal_error` with a fixed message and its traceback in the log.
"""
import logging
import math

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from answer import ConsentRequired
from embedding.errors import EmbeddingError, ModelNotFound, OllamaUnavailable
from embedding.ollama import DEFAULT_MODEL
from indexing import IndexAlreadyRunning
from llm import (LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMError, LLMModelNotFound, LLMNotConfigured, LLMRateLimited, LLMTimeout,
                 LLMUnavailable)
from jobs import NoIndexRunning, ProjectNotFound, RepoNotFound
from projects import InvalidProjectPath, ProjectBusy, ProjectExists
from search import IndexOutOfDate, NotIndexed

log = logging.getLogger("clank.api")


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _fixed(status: int, code: str, message: str):
    async def handler(request: Request, exc: Exception):
        return error_response(status, code, message)
    return handler


async def _index_out_of_date(request: Request, exc: IndexOutOfDate):
    return error_response(409, "index_out_of_date", str(exc))      # written for people (it names the models and says "re-index this project")


async def _invalid_path(request: Request, exc: InvalidProjectPath):
    return error_response(422, "invalid_path", str(exc))               # written for people, and never contains the path


async def _llm_not_configured(request: Request, exc: LLMNotConfigured):
    return error_response(503, "llm_not_configured", str(exc))         # authored, and it names the variable to set, never a value


async def _llm_rate_limited(request: Request, exc: LLMRateLimited):
    if exc.retry_after is None:
        return error_response(429, "llm_rate_limited", "The answer service's rate limit was reached. Try again in a minute.")
    seconds = max(1, math.ceil(exc.retry_after))
    response = error_response(429, "llm_rate_limited", f"The answer service's rate limit was reached. Try again in about {seconds} second{'s' if seconds != 1 else ''}.")
    response.headers["Retry-After"] = str(seconds)
    return response


async def _validation(request: Request, exc: RequestValidationError):
    fields = []
    for problem in exc.errors():
        where = ".".join(str(part) for part in problem["loc"] if part != "body")
        fields.append(f"{where or 'request'}: {problem['msg']}")      # the problem's own words; the INPUT is never echoed
    return error_response(422, "invalid_request", "The request is not valid. " + "; ".join(fields))


_HTTP_CODES = {404: ("not_found", "There is nothing at this address."), 405: ("method_not_allowed", "This address does not accept that method.")}


async def _http(request: Request, exc: StarletteHTTPException):
    code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "The request could not be completed."))
    return error_response(exc.status_code, code, message)


async def _unexpected(request: Request, exc: Exception):
    log.error("unexpected error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "Something went wrong inside Clank. The details are in the backend log.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NotIndexed, _fixed(409, "not_indexed", "This project has not been indexed yet. Index it first."))
    app.add_exception_handler(IndexOutOfDate, _index_out_of_date)
    app.add_exception_handler(ProjectNotFound, _fixed(404, "project_not_found", "There is no project with this id."))
    app.add_exception_handler(RepoNotFound, _fixed(409, "repo_not_found", "The project's folder no longer exists. Check that it was not moved or deleted."))
    app.add_exception_handler(InvalidProjectPath, _invalid_path)
    app.add_exception_handler(ProjectExists, _fixed(409, "project_exists", "This folder is already a project."))
    app.add_exception_handler(ProjectBusy, _fixed(409, "project_busy", "Indexing is running for this project. Wait for it to finish or cancel it, then try again."))
    app.add_exception_handler(ConsentRequired, _fixed(403, "consent_required", "The answer model is a remote service. Sending code excerpts to it needs your "
                                                                              "permission: allow it and ask again."))
    app.add_exception_handler(LLMNotConfigured, _llm_not_configured)
    app.add_exception_handler(LLMRateLimited, _llm_rate_limited)
    app.add_exception_handler(LLMAuthError, _fixed(502, "llm_auth_failed", "The answer service refused the API key. Check the key and try again."))
    app.add_exception_handler(LLMModelNotFound, _fixed(502, "llm_model_not_found", "The answer service does not know the configured model. Check the model name."))
    app.add_exception_handler(LLMContextTooLong, _fixed(413, "llm_context_too_long", "The code excerpts are larger than the model accepts right now. "
                                                                                    "Ask a narrower question, or wait a minute if you asked several in a row."))
    app.add_exception_handler(LLMUnavailable, _fixed(503, "llm_unavailable", "The answer service could not be reached. Check the connection and try again."))
    app.add_exception_handler(LLMTimeout, _fixed(504, "llm_timeout", "The answer service took too long to answer. Try again."))
    app.add_exception_handler(LLMBadResponse, _fixed(502, "llm_bad_response", "The answer service sent a reply Clank could not read."))
    app.add_exception_handler(LLMError, _fixed(502, "llm_error", "The answer service failed."))
    app.add_exception_handler(NoIndexRunning, _fixed(409, "no_index_running", "No indexing is running for this project."))
    app.add_exception_handler(IndexAlreadyRunning, _fixed(409, "index_already_running", "This project is already being indexed."))
    app.add_exception_handler(OllamaUnavailable, _fixed(503, "ollama_unavailable", "Ollama is not reachable. Start Ollama and try again."))
    app.add_exception_handler(ModelNotFound, _fixed(503, "model_not_found", f"Ollama does not have the embedding model. Run: ollama pull {DEFAULT_MODEL}"))
    app.add_exception_handler(EmbeddingError, _fixed(502, "embedding_error", "The embedding model gave an unusable answer. Try again; if it keeps failing, check Ollama."))
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(Exception, _unexpected)
