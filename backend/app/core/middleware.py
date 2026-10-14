"""HTTP middleware: correlation IDs, security headers, body limits, rate limits.

Ordering is a correctness property, not a style choice, and it is decided in
``app.main.create_app`` rather than here. What matters at this layer is that
each middleware does one thing and that the one thing is cheap enough to run on
every request, including the ones it is going to reject.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.correlation import (
    CORRELATION_HEADER,
    get_correlation_id,
    new_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from app.core.errors import RateLimitedError
from app.core.logging import get_logger

logger = get_logger(__name__)

_RequestHandler = Callable[[Request], Awaitable[Response]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assign (or adopt) a correlation id for every request.

    An inbound ``X-Request-ID`` is honoured so a trace can start at a load
    balancer or a calling service, but it is length-capped: the header is
    client-controlled and ends up in log records, so an unbounded value is a
    log-injection vector.
    """

    MAX_INBOUND_LENGTH = 200

    async def dispatch(self, request: Request, call_next: _RequestHandler) -> Response:
        inbound = request.headers.get(CORRELATION_HEADER)
        correlation_id = (
            inbound
            if inbound and len(inbound) <= self.MAX_INBOUND_LENGTH and inbound.isprintable()
            else new_correlation_id()
        )
        token = set_correlation_id(correlation_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            reset_correlation_id(token)
        response.headers[CORRELATION_HEADER] = correlation_id
        logger.info(
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative response headers for a JSON-only API.

    The API never renders HTML, so the policy can be maximally restrictive:
    deny framing entirely, forbid content sniffing, and permit no resources.
    The full page CSP belongs to the frontend host, not here.

    Phase 5 adds ``Strict-Transport-Security``, and adds it **conditionally**.
    HSTS is a promise the browser holds for a year: send it from a deployment
    that is not actually reachable over HTTPS and the site becomes unreachable
    in that browser, with no way to undo it from the server side. So it is
    emitted only when the deployment says it is HTTPS-only, which
    ``cookie_secure`` already encodes.
    """

    def __init__(self, app: Any, *, hsts: bool = False, hsts_max_age: int = 31_536_000) -> None:
        super().__init__(app)
        self._hsts = hsts
        self._hsts_max_age = hsts_max_age

    async def dispatch(self, request: Request, call_next: _RequestHandler) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )
        # An authenticated API response must never be cached by a proxy or
        # written to a browser's disk cache: the next person at the machine
        # would read it. Cheap to state globally — nothing this API returns is
        # worth caching.
        response.headers.setdefault("Cache-Control", "no-store")
        # ``preload`` is deliberately omitted: submitting to the preload list is
        # an operator's decision with a slow, manual reversal, not a default a
        # framework should make on their behalf.
        if self._hsts:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={self._hsts_max_age}; includeSubDomains",
            )
        return response


class BodySizeLimitMiddleware:
    """Reject an oversized request body **before** it is buffered.

    Pure ASGI rather than ``BaseHTTPMiddleware``, because the whole point is to
    act on the message stream: by the time a ``BaseHTTPMiddleware`` sees a
    ``Request``, ``await request.body()`` has already had the chance to read the
    lot into memory.

    **This is the Phase 5 fix for a real defect.** The webhook endpoint's own
    size guard ran *after* ``await request.body()``, so its comment — "an
    oversized body should cost us nothing" — described a property the code did
    not have. An unauthenticated POST of arbitrary size was fully resident in
    the API process before the check rejected it.

    Two checks, because either alone is insufficient: ``Content-Length`` when it
    is present and honest, and a running total while the body streams, which
    catches a chunked body that declares no length at all.

    The webhook path keeps its own, much larger ceiling — GitHub sends large
    payloads legitimately — so the limit is chosen per path here rather than
    applied uniformly.
    """

    def __init__(
        self,
        app: Any,
        *,
        default_max_bytes: int,
        path_overrides: dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self._default = default_max_bytes
        self._overrides = path_overrides or {}

    def _limit_for(self, path: str) -> int:
        for prefix, limit in self._overrides.items():
            if path.startswith(prefix):
                return limit
        return self._default

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await _reject_too_large(send, limit)
            return

        total = 0

        async def counting_receive() -> Any:
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    # Raising here would surface as a 500. Returning a
                    # disconnect instead makes the application see a truncated
                    # body, which its own validation rejects — but the response
                    # below is what the client actually gets, because it is
                    # sent before the application can produce one.
                    raise _BodyTooLarge(limit)
            return message

        try:
            await self.app(scope, counting_receive, send)
        except _BodyTooLarge as exc:
            await _reject_too_large(send, exc.limit)


class _BodyTooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Request body exceeded {limit} bytes.")
        self.limit = limit


def _content_length(scope: Any) -> int | None:
    for key, value in scope.get("headers", ()):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _reject_too_large(send: Any, limit: int) -> None:
    body = (
        b'{"type":"https://codeguard.ai/problems/payload-too-large",'
        b'"title":"Payload too large","status":413,'
        b'"detail":"The request body exceeds the configured limit."}'
    )
    try:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
    except RuntimeError:
        # The response had already started; nothing more can be sent. The
        # connection closes and the client sees a truncated response, which is
        # the correct outcome for a body we refused to read.
        logger.warning("http.body_limit.response_already_started", limit=limit)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client request budgets, applied per route class.

    The key is the session id when there is one and the client address
    otherwise. Session-first matters: many users legitimately share one address
    (an office, a VPN, a mobile carrier's NAT), and keying only on the address
    would make one busy user throttle everybody behind it.

    The address is only meaningful when the proxy configuration says it is —
    see ``TRUSTED_PROXY_COUNT``. With no trusted proxy, ``request.client.host``
    is the real peer; with one, the proxy middleware has already rewritten it.
    Reading ``X-Forwarded-For`` here would key on a header the client writes,
    which makes the limit trivially bypassable.
    """

    def __init__(
        self,
        app: Any,
        *,
        auth_limiter: Any,
        api_limiter: Any,
        webhook_limiter: Any,
        session_cookie_name: str,
    ) -> None:
        super().__init__(app)
        self._auth = auth_limiter
        self._api = api_limiter
        self._webhook = webhook_limiter
        self._cookie = session_cookie_name

    async def dispatch(self, request: Request, call_next: _RequestHandler) -> Response:
        path = request.url.path
        if path in ("/healthz", "/readyz", "/health"):
            # Probes are issued by the orchestrator on a fixed schedule and
            # must never be throttled: a rate-limited readiness probe reads as
            # an unhealthy instance and removes a working one from rotation.
            return await call_next(request)

        limiter, bucket = self._select(path)
        key = self._key(request, bucket)
        allowed, retry_after = limiter.check(key)
        if not allowed:
            logger.warning("ratelimit.rejected", path=path, bucket=bucket)
            return self._too_many_requests(request, retry_after)
        return await call_next(request)

    def _too_many_requests(self, request: Request, retry_after: int) -> Response:
        """Render the 429 here, rather than raising.

        **Phase 6 fix, found by running the stack over real HTTP.** This used to
        ``raise RateLimitedError`` on the assumption that the RFC 9457 handler
        in ``app.api.problem`` would render it. It does not, and cannot:
        Starlette builds the stack as

            ServerErrorMiddleware → …user middleware… → ExceptionMiddleware → routes

        and the registered per-exception handlers live in ``ExceptionMiddleware``,
        which is *inside* this middleware. An exception raised here sails past it
        and lands in ``ServerErrorMiddleware`` — so every throttled request was
        answered with **500 internal-error and no Retry-After**, which tells a
        client to report a bug rather than to slow down, and buries real
        server faults in throttling noise.

        Middleware that wants a specific status must return a response. The body
        is the same problem+json shape the handler would have produced, so a
        client parses one format either way.
        """
        body = {
            "type": f"https://codeguard.ai/problems/{RateLimitedError.problem_type}",
            "title": RateLimitedError.title,
            "status": RateLimitedError.status_code,
            "detail": "Too many requests. Please wait and try again.",
            "instance": request.url.path,
            "correlation_id": get_correlation_id(),
            "retry_after": retry_after,
        }
        return JSONResponse(
            body,
            status_code=RateLimitedError.status_code,
            media_type="application/problem+json",
            headers={"Retry-After": str(retry_after)},
        )

    def _select(self, path: str) -> tuple[Any, str]:
        if path.startswith("/api/v1/auth/"):
            return self._auth, "auth"
        if path.startswith("/api/v1/webhooks/"):
            return self._webhook, "webhook"
        return self._api, "api"

    def _key(self, request: Request, bucket: str) -> str:
        token = request.cookies.get(self._cookie)
        if token:
            # A digest, not the token: the key lives in an in-memory dict that
            # a traceback could render.
            return f"{bucket}:s:{hashlib.sha256(token.encode()).hexdigest()[:32]}"
        host = request.client.host if request.client else "unknown"
        return f"{bucket}:a:{host}"
