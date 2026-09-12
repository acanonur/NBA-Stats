"""The error envelope, the error codes, and the request id that ties them to the log.

``contracts/CONTRACT.md`` §7 fixes both the body and the HTTP status of every failure::

    {"error": {"code": "player_not_found", "message": "No player with id 99999999.",
               "recoverable": false, "field": null, "requestId": "0f1c…"}}

:class:`ApiError` is the one exception type routes raise; :func:`install_error_handlers`
makes sure *every* other failure — a Starlette ``HTTPException``, a request-validation
failure, or an unhandled exception — leaves through the same envelope, because a client that
meets a bare FastAPI ``{"detail": …}`` body has no code to branch on.

:class:`RequestIdMiddleware` is pure ASGI rather than ``BaseHTTPMiddleware`` on purpose: the
SSE endpoint in ``routes_sync`` needs client-disconnect detection to keep working, and
``BaseHTTPMiddleware`` hides disconnects behind its own request/response plumbing.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from .schemas import ErrorBody, ErrorEnvelope

__all__ = [
    "REQUEST_ID_HEADER",
    "ERROR_STATUS",
    "RECOVERABLE_BY_DEFAULT",
    "ApiError",
    "RequestIdMiddleware",
    "current_request_id",
    "request_id_of",
    "error_envelope",
    "error_response",
    "install_error_handlers",
    "bad_request",
    "invalid_config",
    "too_many_widgets",
    "unauthorized",
    "player_not_found",
    "team_not_found",
    "game_not_found",
    "metric_unavailable",
    "season_not_loaded",
    "rate_limited",
    "upstream_unavailable",
    "internal_error",
]

logger = logging.getLogger("nbastats.api")

REQUEST_ID_HEADER = "X-Request-Id"

#: Every code in ``contracts/CONTRACT.md`` §7, with the status it must be served as.
ERROR_STATUS: dict[str, int] = {
    "bad_request": 400,
    "invalid_config": 400,
    "too_many_widgets": 400,
    "unauthorized": 401,
    "player_not_found": 404,
    "team_not_found": 404,
    "game_not_found": 404,
    "metric_unavailable": 422,
    "season_not_loaded": 422,
    "rate_limited": 429,
    "upstream_unavailable": 503,
    "internal_error": 500,
    # Transport-level codes for requests the contract does not describe: an unknown path or
    # a wrong verb is not one of the subject-not-found cases above.
    "not_found": 404,
    "method_not_allowed": 405,
}

#: Whether retrying the same request could plausibly succeed. The client shows a retry
#: button for these and a plain explanation for the rest.
RECOVERABLE_BY_DEFAULT: dict[str, bool] = {
    "bad_request": False,
    "invalid_config": True,
    "too_many_widgets": False,
    "unauthorized": False,
    "player_not_found": False,
    "team_not_found": False,
    "game_not_found": False,
    "metric_unavailable": True,
    "season_not_loaded": True,
    "rate_limited": True,
    "upstream_unavailable": True,
    "internal_error": True,
    "not_found": False,
    "method_not_allowed": False,
}

_request_id: ContextVar[Optional[str]] = ContextVar("hardwood_request_id", default=None)


def current_request_id() -> str | None:
    """The id of the request being served on this task, if any."""
    return _request_id.get()


def request_id_of(request: Request | None) -> str | None:
    """Request id from the request scope, falling back to the context variable."""
    if request is not None:
        value = getattr(request.state, "request_id", None)
        if value:
            return str(value)
    return current_request_id()


class ApiError(Exception):
    """A failure that maps onto one row of the contract's error table.

    ``http_status`` and ``recoverable`` default to the row's values, so a route only ever
    has to name the code and write a message a person can read.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        recoverable: bool | None = None,
        field: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status if http_status is not None else ERROR_STATUS.get(code, 500)
        self.recoverable = (
            recoverable if recoverable is not None else RECOVERABLE_BY_DEFAULT.get(code, False)
        )
        self.field = field
        self.headers = dict(headers or {})

    def to_error_body(self, request_id: str | None = None) -> ErrorBody:
        """The inner ``error`` object — also what a per-widget resolve failure carries."""
        return ErrorBody(
            code=self.code,
            message=self.message,
            recoverable=self.recoverable,
            field=self.field,
            request_id=request_id,
        )

    def to_response(self, request_id: str | None = None) -> JSONResponse:
        """The whole HTTP response, headers included."""
        headers = dict(self.headers)
        if request_id:
            headers.setdefault(REQUEST_ID_HEADER, request_id)
        return JSONResponse(
            status_code=self.http_status,
            content=ErrorEnvelope(error=self.to_error_body(request_id)).model_dump(
                mode="json", by_alias=True
            ),
            headers=headers,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ApiError({self.code!r}, {self.message!r}, status={self.http_status})"


# --------------------------------------------------------------------------- constructors


def bad_request(message: str, field: str | None = None) -> ApiError:
    """400 — a malformed query or body."""
    return ApiError("bad_request", message, field=field)


def invalid_config(message: str, field: str | None = None) -> ApiError:
    """400 — a widget config failed validation; ``field`` names the offending key."""
    return ApiError("invalid_config", message, field=field)


def too_many_widgets(count: int, maximum: int = 24) -> ApiError:
    """400 — more than ``maximum`` widgets in one resolve."""
    return ApiError(
        "too_many_widgets",
        f"{count} widgets in one request exceeds the maximum of {maximum}.",
        field="widgets",
    )


def unauthorized(message: str = "A valid X-API-Key header is required.") -> ApiError:
    """401 — missing or wrong ``X-API-Key``."""
    return ApiError("unauthorized", message, headers={"WWW-Authenticate": "X-API-Key"})


def player_not_found(player_id: Any) -> ApiError:
    """404 — unknown player id."""
    return ApiError("player_not_found", f"No player with id {player_id}.")


def team_not_found(team_id: Any) -> ApiError:
    """404 — unknown team id."""
    return ApiError("team_not_found", f"No team with id {team_id}.")


def game_not_found(game_id: Any) -> ApiError:
    """404 — unknown game id."""
    return ApiError("game_not_found", f"No game with id {game_id}.")


def metric_unavailable(message: str, field: str | None = "metric") -> ApiError:
    """422 — the metric does not exist for the requested era or subject."""
    return ApiError("metric_unavailable", message, field=field)


def season_not_loaded(season: str, field: str | None = "season") -> ApiError:
    """422 — a valid season that has not been ingested yet."""
    return ApiError(
        "season_not_loaded", f"The {season} season is not loaded on this server.", field=field
    )


def rate_limited(retry_after: int) -> ApiError:
    """429 — the caller exceeded this service's own limiter."""
    return ApiError(
        "rate_limited",
        f"Too many requests. Retry after {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


def upstream_unavailable(message: str = "The ingest source is unreachable.") -> ApiError:
    """503 — the upstream source is down; cached data may be stale."""
    return ApiError("upstream_unavailable", message)


def internal_error(message: str = "An unexpected error occurred.") -> ApiError:
    """500 — anything else. The request id is in the server log."""
    return ApiError("internal_error", message)


# --------------------------------------------------------------------------- envelope


def error_envelope(
    code: str,
    message: str,
    *,
    recoverable: bool | None = None,
    field: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """The error body as a plain JSON-ready dict."""
    body = ErrorBody(
        code=code,
        message=message,
        recoverable=(
            recoverable if recoverable is not None else RECOVERABLE_BY_DEFAULT.get(code, False)
        ),
        field=field,
        request_id=request_id,
    )
    return ErrorEnvelope(error=body).model_dump(mode="json", by_alias=True)


def error_response(
    code: str,
    message: str,
    *,
    status_code: int | None = None,
    recoverable: bool | None = None,
    field: str | None = None,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """A complete error response, for callers that hold no :class:`ApiError`."""
    return ApiError(
        code,
        message,
        http_status=status_code,
        recoverable=recoverable,
        field=field,
        headers=headers,
    ).to_response(request_id)


# --------------------------------------------------------------------------- middleware


class RequestIdMiddleware:
    """Stamp every request with an id, echo it in the header and in every error body.

    An inbound ``X-Request-Id`` is honoured — that is how a client correlates a retry with
    the original attempt — otherwise a short uuid is minted.
    """

    def __init__(self, app: Any, header_name: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for key, value in scope.get("headers", ()):
            if key.decode("latin-1").lower() == self.header_name.lower():
                incoming = value.decode("latin-1").strip()[:64]
                break
        request_id = incoming or uuid.uuid4().hex[:16]

        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)

        async def send_with_header(message: Any) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[self.header_name] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _request_id.reset(token)


# --------------------------------------------------------------------------- handlers


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Serve an :class:`ApiError` as the contract's envelope."""
    assert isinstance(exc, ApiError)
    request_id = request_id_of(request)
    if exc.http_status >= 500:
        logger.error("request %s failed: %s %s", request_id, exc.code, exc.message)
    return exc.to_response(request_id)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate Starlette's ``HTTPException`` (404, 405, …) into the envelope."""
    assert isinstance(exc, StarletteHTTPException)
    status = exc.status_code
    code = {
        400: "bad_request",
        401: "unauthorized",
        404: "not_found",
        405: "method_not_allowed",
        422: "bad_request",
        429: "rate_limited",
        503: "upstream_unavailable",
    }.get(status, "bad_request" if status < 500 else "internal_error")
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return ApiError(
        code, detail, http_status=status, headers=dict(getattr(exc, "headers", None) or {})
    ).to_response(request_id_of(request))


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """A malformed query or body is ``400 bad_request``, never FastAPI's bare 422."""
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()
    field: str | None = None
    message = "The request could not be understood."
    if errors:
        first = errors[0]
        location = [str(part) for part in first.get("loc", ()) if part not in ("body", "query")]
        field = location[-1] if location else None
        message = f"{'.'.join(location) or 'request'}: {first.get('msg', 'is invalid')}"
    return ApiError("bad_request", message, field=field).to_response(request_id_of(request))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log with the request id, tell the client nothing sensitive."""
    request_id = request_id_of(request)
    logger.exception("request %s raised %s", request_id, type(exc).__name__)
    return internal_error(
        "An unexpected error occurred. Quote the request id when reporting it."
    ).to_response(request_id)


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler so no failure can escape without the envelope."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
