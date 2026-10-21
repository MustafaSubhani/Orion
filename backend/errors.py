"""One error envelope for every failure: {"error": {"code", "message", "request_id"}}."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from harness.errors import HarnessError

log = logging.getLogger("orion.api")

_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    422: "invalid_request",
    429: "rate_limited",
}


class ApiError(Exception):
    """Raise from any route. The message is shown to the client, so keep it free of internals."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def envelope(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id_of(request)}},
        headers={"X-Request-ID": request_id_of(request)},
    )


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return envelope(request, exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return envelope(request, exc.status_code, _CODES.get(exc.status_code, "error"), message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        first = (exc.errors() or [{}])[0]
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = f"{where}: {first.get('msg', 'invalid value')}" if where else str(first.get("msg", "invalid request"))
        return envelope(request, 422, "invalid_request", message)

    @app.exception_handler(HarnessError)
    async def _harness_error(request: Request, exc: HarnessError):
        return envelope(request, 502, "harness_error", exc.safe_message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error request_id=%s", request_id_of(request))
        return envelope(request, 500, "internal_error", "Internal error. Quote the request id when reporting it.")
