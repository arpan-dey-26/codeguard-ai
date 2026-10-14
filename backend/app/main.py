"""FastAPI application factory.

A factory, not a module-level ``app = FastAPI()``, so that tests can build an
application with different settings without re-importing the module or
mutating global state.

**Middleware order is the security model of this file.** Starlette runs
middleware in *reverse* registration order — the last registered is outermost —
so reading the ``add_middleware`` calls bottom-to-top gives the order a request
actually travels:

    1. CorrelationIdMiddleware   outermost: every response gets an id
    2. SecurityHeadersMiddleware outermost-but-one: every response gets headers
    3. BodySizeLimitMiddleware   refuse an oversized body before it is buffered
    4. ProxyHeadersMiddleware    correct client address and scheme, if trusted
    5. TrustedHostMiddleware     refuse an unrecognised Host outright
    6. CORSMiddleware            answer preflights, attach CORS headers
    7. RateLimitMiddleware       reject a flood before it reaches any handler
       …the application

Every placement is load-bearing, and **the first two were wrong until Phase 6**:

* **The correlation id is outermost.** It used to be innermost, and the
  docstring claimed that meant "a rejection emitted by the rate limiter still
  carries an id". The opposite is true: an inner middleware that returns early
  never unwinds through an outer one, so every throttled request came back with
  no ``X-Request-ID`` and a null ``correlation_id`` — untraceable, which is the
  one property an error response most needs. Found by driving the real stack
  over HTTP; `tests/security/test_middleware_over_http.py` now holds it.
* **Security headers are next.** Same reasoning: a 413 from the body limit and
  a 429 from the rate limiter are responses an attacker can provoke at will,
  and they need the headers as much as a 200 does.
* **The body limit is above everything that reads a body.** Its whole purpose
  is to act on the raw ASGI stream; anything above it that touches the body has
  already spent the memory.
* **Proxy headers are above the rate limiter**, so the limiter keys on the
  corrected client address rather than the proxy's.
* **Trusted host is above CORS**, so a request with a forged ``Host`` is
  rejected before any CORS header is computed from it.
* **The rate limiter is innermost**, so a 429 still carries CORS headers and
  the browser is allowed to read it.

A second lesson from the same exercise, recorded because it is not obvious:
**middleware cannot raise a handled exception.** Starlette's stack is
``ServerErrorMiddleware → …user middleware… → ExceptionMiddleware → routes``,
and the registered per-exception handlers live in ``ExceptionMiddleware`` —
*inside* every middleware here. Anything raised at this layer bypasses them and
becomes a 500. Middleware that wants a specific status must **return** a
response; see ``RateLimitMiddleware._too_many_requests``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.health import router as health_router
from app.api.problem import register_exception_handlers
from app.api.v1.router import router as v1_router
from app.auth.cookies import CSRF_HEADER
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    BodySizeLimitMiddleware,
    CorrelationIdMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.proxy import ProxyHeadersMiddleware
from app.core.ratelimit import RateLimiter
from app.db.session import dispose_engine

logger = get_logger(__name__)

WEBHOOK_PATH_PREFIX = "/api/v1/webhooks/"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown.

    Startup deliberately does **not** connect to the database or run
    migrations.  Connecting eagerly means a database blip prevents the process
    from starting at all, when it could have started and reported itself
    not-ready.  Migrations run as a release command, never on startup, because
    two instances booting simultaneously would race (architecture.md §26.4).
    """
    settings: Settings = app.state.settings
    logger.info("app.startup.completed", **settings.safe_summary())
    _warn_about_soft_misconfiguration(settings)
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("app.shutdown.completed")


def _warn_about_soft_misconfiguration(settings: Settings) -> None:
    """Say out loud what a development deployment is missing.

    Production refuses to start when any of this is wrong (see
    ``Settings._fail_fast_on_production_gaps``). Development starts anyway,
    because a developer needs to be able to run the thing — but silence would
    let "sign-in does not work" look like a bug rather than a setting.
    """
    if not settings.oauth_configured:
        logger.warning(
            "app.auth.unconfigured",
            detail=(
                "Sign-in is unavailable: set GITHUB_OAUTH_CLIENT_ID, "
                "GITHUB_OAUTH_CLIENT_SECRET and SESSION_SECRET."
            ),
        )
    if settings.dashboard_allow_unauthenticated_reads:
        logger.warning(
            "app.dashboard.unauthenticated_reads_enabled",
            detail=(
                "Every monitored repository's review data is being served without authentication."
            ),
        )
    if settings.session_secret is None:
        logger.warning(
            "app.session.ephemeral_key",
            detail=(
                "No SESSION_SECRET: sessions are signed with a key generated "
                "for this process and will not survive a restart."
            ),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the application."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=("AI-powered GitHub code review and code quality monitoring platform."),
        lifespan=lifespan,
        # Interactive docs are a development convenience, not a production
        # surface: they advertise the whole API shape to anyone who finds it.
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )
    app.state.settings = settings

    # Registration order is the REVERSE of execution order, so this block
    # reads bottom-up as the request travels. See the module docstring for why
    # each position is what it is.

    # innermost of the cross-cutting layers
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            auth_limiter=RateLimiter(settings.rate_limit_auth_per_minute),
            api_limiter=RateLimiter(settings.rate_limit_api_per_minute),
            webhook_limiter=RateLimiter(settings.rate_limit_webhook_per_minute),
            session_cookie_name=settings.session_cookie_name,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # ``Authorization`` is gone: Phase 5 authenticates by cookie, and an
        # allow-listed header that nothing sends is a header somebody will
        # eventually start sending. ``X-CSRF-Token`` replaces it and is the
        # header the double-submit check reads.
        allow_headers=["Content-Type", "X-Request-ID", CSRF_HEADER],
        # Without this the browser can attach the correlation id to a request
        # and then not let the SPA read it back off the response — which makes
        # the id useless for exactly the cross-origin case it exists for.
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_host_list,
    )

    app.add_middleware(
        ProxyHeadersMiddleware,
        trusted_proxy_count=settings.trusted_proxy_count,
    )

    app.add_middleware(
        BodySizeLimitMiddleware,
        default_max_bytes=settings.max_request_body_bytes,
        # The webhook receiver legitimately takes far larger bodies than any
        # dashboard request, so it keeps its own ceiling rather than forcing the
        # general one up to match.
        path_overrides={
            WEBHOOK_PATH_PREFIX: settings.github_webhook_max_body_bytes,
        },
    )

    app.add_middleware(
        SecurityHeadersMiddleware,
        # Only promise HSTS where the deployment is actually HTTPS-only. The
        # promise lasts a year in the browser and cannot be withdrawn from the
        # server side, so sending it from a plain-HTTP environment bricks that
        # origin for whoever visited it.
        hsts=settings.cookie_secure,
    )

    # outermost: nothing leaves without an id, including an early rejection
    # from any middleware below.
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(v1_router)

    return app


app = create_app()
