"""One JSON shape for every error the API returns: {"error": {"code": "...", "message": "..."}} (task 8.2).

The code is a stable word the frontend can switch on; the message is for a person and says what to do. The status and code come from the exception's CLASS,
never from its text. Messages for Ollama errors are fixed sentences: the real exception text can hold a URL, a path or a piece of Ollama's reply, so it goes
to the log only. An unexpected error is a 500 `internal_error` with a fixed message and its traceback in the log.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from embedding.errors import EmbeddingError, ModelNotFound, OllamaUnavailable
from embedding.ollama import DEFAULT_MODEL
from indexing import IndexAlreadyRunning
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
    app.add_exception_handler(IndexAlreadyRunning, _fixed(409, "index_already_running", "This project is already being indexed."))
    app.add_exception_handler(OllamaUnavailable, _fixed(503, "ollama_unavailable", "Ollama is not reachable. Start Ollama and try again."))
    app.add_exception_handler(ModelNotFound, _fixed(503, "model_not_found", f"Ollama does not have the embedding model. Run: ollama pull {DEFAULT_MODEL}"))
    app.add_exception_handler(EmbeddingError, _fixed(502, "embedding_error", "The embedding model gave an unusable answer. Try again; if it keeps failing, check Ollama."))
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(Exception, _unexpected)
