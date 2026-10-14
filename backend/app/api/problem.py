"""RFC 9457 problem-details rendering.

Moved out of ``app.core.errors`` in Phase 1 so that the exception hierarchy is
importable by the worker, which has no web framework (see that module's
docstring).  Behaviour is unchanged from Phase 0.

The invariant: a client receives a problem *type*, a title, a status and a
``correlation_id``.  It never receives a stack trace, a SQL fragment, a
connection string, an internal message or — for a failed signature check — any
hint about why verification failed.  The detail goes to the log, keyed by the
same correlation id.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.correlation import get_correlation_id
from app.core.errors import CodeGuardError, ThrottledError, WebhookSignatureError
from app.core.logging import get_logger

logger = get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
_PROBLEM_BASE = "https://codeguard.ai/problems"


def _problem(
    *,
    status_code: int,
    problem_type: str,
    title: str,
    detail: str | None,
    instance: str,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{_PROBLEM_BASE}/{problem_type}",
        "title": title,
        "status": status_code,
        "instance": instance,
        "correlation_id": get_correlation_id(),
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_CONTENT_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the problem-details handlers to the application."""

    @app.exception_handler(CodeGuardError)
    async def _handle_codeguard(request: Request, exc: CodeGuardError) -> JSONResponse:
        # A rejected signature is a security event, logged at warning with the
        # source address so that a sustained stream is visible as an attack
        # rather than buried among validation noise (architecture.md §24.5).
        if isinstance(exc, WebhookSignatureError):
            logger.warning(
                "webhook.signature.rejected",
                path=request.url.path,
                client_host=request.client.host if request.client else None,
            )
            # No detail: explaining *why* verification failed is free
            # information for someone probing the secret.
            return _problem(
                status_code=exc.status_code,
                problem_type=exc.problem_type,
                title=exc.title,
                detail=None,
                instance=request.url.path,
            )

        logger.warning(
            "request.failed",
            problem_type=exc.problem_type,
            status_code=exc.status_code,
            path=request.url.path,
            retryable=exc.retryable,
        )
        extra: dict[str, Any] = {}
        if isinstance(exc, ThrottledError) and exc.retry_after is not None:
            extra["retry_after"] = exc.retry_after
        return _problem(
            status_code=exc.status_code,
            problem_type=exc.problem_type,
            title=exc.title,
            detail=exc.detail,
            instance=request.url.path,
            extra=extra,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Location and message only — never the offending value, which may be
        # attacker-supplied and is echoed straight back to the client.
        errors = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg", "")} for err in exc.errors()
        ]
        return _problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            problem_type="validation-error",
            title="Request validation failed",
            detail=None,
            instance=request.url.path,
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            status_code=exc.status_code,
            problem_type="http-error",
            title=str(exc.detail) if exc.detail else "HTTP error",
            detail=None,
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "request.unhandled_exception",
            path=request.url.path,
            exc_type=type(exc).__name__,
        )
        return _problem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            problem_type="internal-error",
            title="Internal server error",
            detail=None,
            instance=request.url.path,
        )
