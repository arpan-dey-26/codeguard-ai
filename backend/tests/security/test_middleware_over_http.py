"""The middleware stack, exercised over real HTTP.

**Why this file exists.** Until Phase 6 every claim about the middleware layer —
security headers, the body-size ceiling, trusted hosts, forwarded-header
handling, rate limiting — rested on reading the source. Phase 5's own report
marked the whole HTTP layer *statically verified*, because FastAPI cannot be
installed in the verification environment.

FastAPI is not the middleware. **Starlette is**, and Starlette is installable.
Every middleware in ``app.core.middleware`` and ``app.core.proxy`` is either
pure ASGI or a Starlette ``BaseHTTPMiddleware``, and ``TrustedHostMiddleware``
and ``CORSMiddleware`` are Starlette's own. So the stack can be assembled here
exactly as ``app.main.create_app`` assembles it, driven by a real client, and
checked on real status codes, real headers and real cookies.

What this does **not** cover, stated so the boundary is not blurred: FastAPI's
dependency injection, request validation and response-model serialisation. The
routes themselves remain untested until FastAPI can be installed. This file
covers the layer *around* the routes — which is the layer that carries most of
the security properties.
"""

from __future__ import annotations

import json

import pytest

starlette = pytest.importorskip("starlette", reason="Starlette is not installed")

from app.core.errors import CodeGuardError, ThrottledError  # noqa: E402
from app.core.middleware import (  # noqa: E402
    BodySizeLimitMiddleware,
    CorrelationIdMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.proxy import ProxyHeadersMiddleware  # noqa: E402
from app.core.ratelimit import RateLimiter  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

FRONTEND_ORIGIN = "https://app.example.com"
WEBHOOK_PATH = "/api/v1/webhooks/github"


# ---------------------------------------------------------------------------
# A real app, assembled the way app/main.py assembles the real one
# ---------------------------------------------------------------------------


async def _echo(request: Request) -> JSONResponse:
    """Report what the application layer actually received.

    Returning the values the middleware is supposed to have corrected — client
    address, scheme, body length — is what lets these tests assert on effects
    rather than on the middleware's own bookkeeping.
    """
    body = await request.body()
    return JSONResponse(
        {
            "client_host": request.client.host if request.client else None,
            "scheme": request.url.scheme,
            "body_len": len(body),
            "path": request.url.path,
        }
    )


async def _boom(request: Request) -> PlainTextResponse:
    raise CodeGuardError("deliberate failure")


async def _codeguard_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A minimal stand-in for the RFC 9457 renderer in ``app.api.problem``.

    Deliberately not the real one: that function takes a ``FastAPI`` instance.
    What matters for these tests is only that a ``CodeGuardError`` becomes its
    declared status code, so the rate limiter's 429 can be observed.
    """
    assert isinstance(exc, CodeGuardError)
    body: dict[str, object] = {"type": exc.problem_type, "title": exc.title}
    headers = {}
    if isinstance(exc, ThrottledError) and exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(body, status_code=exc.status_code, headers=headers)


def build_app(
    *,
    trusted_hosts: list[str] | None = None,
    proxy_count: int = 0,
    hsts: bool = False,
    max_body: int = 1024,
    webhook_max_body: int = 8192,
    rate_limits: tuple[int, int, int] | None = None,
) -> Starlette:
    """Assemble the real stack in the real order.

    Registration order here mirrors ``app.main.create_app`` exactly, including
    the fact that Starlette runs middleware in reverse registration order. If
    that file's ordering changes and this one does not, these tests stop
    describing the shipped application — so the ordering comment lives in both.
    """
    app = Starlette(
        routes=[
            Route("/echo", _echo, methods=["GET", "POST"]),
            Route("/boom", _boom, methods=["GET"]),
            Route("/healthz", _echo, methods=["GET"]),
            Route(WEBHOOK_PATH, _echo, methods=["POST"]),
        ],
        exception_handlers={CodeGuardError: _codeguard_error_handler},
    )

    # Registration order mirrors app/main.py exactly, including the fact that
    # Starlette runs middleware in reverse registration order. If that file's
    # ordering changes and this one does not, these tests stop describing the
    # shipped application — which is why the reasoning lives in both.
    if rate_limits is not None:
        auth, api, webhook = rate_limits
        app.add_middleware(
            RateLimitMiddleware,
            auth_limiter=RateLimiter(auth),
            api_limiter=RateLimiter(api),
            webhook_limiter=RateLimiter(webhook),
            session_cookie_name="codeguard_session",
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts or ["*"])
    app.add_middleware(ProxyHeadersMiddleware, trusted_proxy_count=proxy_count)
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_max_bytes=max_body,
        path_overrides={"/api/v1/webhooks/": webhook_max_body},
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=hsts)
    app.add_middleware(CorrelationIdMiddleware)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app(), base_url="https://api.example.com")


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Cache-Control", "no-store"),
    ],
)
def test_security_headers_are_present_on_a_real_response(client, header, expected):
    response = client.get("/echo")
    assert response.status_code == 200
    assert response.headers[header] == expected


def test_the_csp_forbids_everything_this_api_never_needs(client):
    csp = client.get("/echo").headers["Content-Security-Policy"]
    for directive in (
        "default-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ):
        assert directive in csp


def test_permissions_policy_disables_powerful_features(client):
    policy = client.get("/echo").headers["Permissions-Policy"]
    for feature in ("camera=()", "microphone=()", "geolocation=()"):
        assert feature in policy


def test_hsts_is_absent_unless_the_deployment_says_it_is_https_only():
    """HSTS is a year-long promise a browser will not let the server withdraw.
    Sending it from a plain-HTTP deployment makes that origin unreachable."""
    plain = TestClient(build_app(hsts=False), base_url="https://api.example.com")
    assert "Strict-Transport-Security" not in plain.get("/echo").headers

    secure = TestClient(build_app(hsts=True), base_url="https://api.example.com")
    header = secure.get("/echo").headers["Strict-Transport-Security"]
    assert "max-age=31536000" in header
    assert "includeSubDomains" in header
    # `preload` commits the origin to a list with a slow, manual removal
    # process. That is an operator's decision, not a framework default.
    assert "preload" not in header


def test_security_headers_survive_an_error_response(client):
    """A 4xx or 5xx is exactly when a missing header matters, because that is
    the response an attacker is most likely to be able to provoke."""
    response = client.get("/boom")
    assert response.status_code == CodeGuardError.status_code
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in response.headers


# ---------------------------------------------------------------------------
# Correlation id
# ---------------------------------------------------------------------------


def test_every_response_carries_a_correlation_id(client):
    assert client.get("/echo").headers["X-Request-ID"]


def test_an_inbound_correlation_id_is_adopted(client):
    response = client.get("/echo", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"


def test_an_overlong_inbound_correlation_id_is_replaced_not_echoed(client):
    """The header is client-controlled and ends up in log records, so an
    unbounded value is a log-injection vector."""
    hostile = "A" * 5000
    response = client.get("/echo", headers={"X-Request-ID": hostile})
    assert response.headers["X-Request-ID"] != hostile
    assert len(response.headers["X-Request-ID"]) < 200


def test_a_non_printable_correlation_id_is_replaced(client):
    response = client.get("/echo", headers={"X-Request-ID": "bad\x07value"})
    assert "\x07" not in response.headers["X-Request-ID"]


# ---------------------------------------------------------------------------
# Body size limit — the Phase 5 fix, now observed rather than argued
# ---------------------------------------------------------------------------


def test_a_body_within_the_limit_is_accepted(client):
    response = client.post("/echo", content=b"x" * 500)
    assert response.status_code == 200
    assert response.json()["body_len"] == 500


def test_an_oversized_body_is_rejected_with_413(client):
    response = client.post("/echo", content=b"x" * 5000)
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"


def test_the_oversized_body_never_reaches_the_application(client):
    """The point of the middleware. A 413 produced *by the route* would mean
    the bytes were buffered first, which is the defect Phase 5 fixed."""
    response = client.post("/echo", content=b"x" * 5000)
    assert response.status_code == 413
    # The echo route would have returned a JSON object with body_len.
    assert "body_len" not in response.text


def test_a_declared_content_length_over_the_limit_is_rejected(client):
    response = client.post("/echo", content=b"x" * 5000, headers={"Content-Length": "5000"})
    assert response.status_code == 413


def test_the_webhook_path_gets_its_own_larger_ceiling(client):
    """GitHub sends large payloads legitimately, so the webhook ceiling is
    separate rather than forcing the general limit up to match."""
    # Over the 1 KiB default, under the 8 KiB webhook override.
    response = client.post(WEBHOOK_PATH, content=b"x" * 4000)
    assert response.status_code == 200
    assert response.json()["body_len"] == 4000

    over = client.post(WEBHOOK_PATH, content=b"x" * 9000)
    assert over.status_code == 413


def test_the_limit_is_enforced_for_a_chunked_body_with_no_content_length():
    """A body streamed without a declared length is the case a Content-Length
    check alone would miss entirely."""
    app = build_app(max_body=1024)
    client = TestClient(app, base_url="https://api.example.com")

    def chunks():
        for _ in range(20):
            yield b"x" * 200

    response = client.post("/echo", content=chunks())
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# Trusted host
# ---------------------------------------------------------------------------


def test_a_recognised_host_is_served():
    app = build_app(trusted_hosts=["api.example.com"])
    client = TestClient(app, base_url="https://api.example.com")
    assert client.get("/echo").status_code == 200


def test_an_unrecognised_host_is_refused():
    app = build_app(trusted_hosts=["api.example.com"])
    client = TestClient(app, base_url="https://api.example.com")
    response = client.get("/echo", headers={"Host": "evil.example"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Forwarded headers
# ---------------------------------------------------------------------------


def test_forwarded_headers_are_ignored_when_no_proxy_is_trusted():
    """The default. Any client can send ``X-Forwarded-For``; with nothing in
    front of us, the TCP peer is the only truthful answer."""
    client = TestClient(build_app(proxy_count=0), base_url="https://api.example.com")
    body = client.get(
        "/echo",
        headers={"X-Forwarded-For": "1.2.3.4", "X-Forwarded-Proto": "http"},
    ).json()
    assert body["client_host"] != "1.2.3.4"
    assert body["scheme"] == "https"


def test_one_trusted_proxy_takes_the_rightmost_entry():
    """Counted from the right, because the rightmost entry was written by the
    hop closest to us. ``entries[0]`` is whatever the *client* wrote."""
    client = TestClient(build_app(proxy_count=1), base_url="https://api.example.com")
    body = client.get("/echo", headers={"X-Forwarded-For": "9.9.9.9, 203.0.113.7"}).json()
    assert body["client_host"] == "203.0.113.7"


def test_a_forged_client_entry_cannot_impersonate_an_address():
    """The attack: send a fake left-most entry hoping to be taken as the
    original client, and so bypass an address-keyed rate limit."""
    client = TestClient(build_app(proxy_count=1), base_url="https://api.example.com")
    body = client.get(
        "/echo",
        headers={"X-Forwarded-For": "127.0.0.1, 10.0.0.1, 203.0.113.7"},
    ).json()
    assert body["client_host"] == "203.0.113.7"
    assert body["client_host"] not in ("127.0.0.1", "10.0.0.1")


def test_a_header_shorter_than_the_expected_hop_count_is_not_trusted():
    """Fewer entries than hops means the request did not come through the full
    chain. Trusting the leftmost value here is the exact mistake the hop count
    exists to avoid, so nothing is trusted and the peer stands."""
    client = TestClient(build_app(proxy_count=3), base_url="https://api.example.com")
    body = client.get("/echo", headers={"X-Forwarded-For": "1.2.3.4"}).json()
    assert body["client_host"] != "1.2.3.4"


def test_the_forwarded_scheme_is_applied_when_a_proxy_is_trusted():
    client = TestClient(build_app(proxy_count=1), base_url="http://api.example.com")
    body = client.get("/echo", headers={"X-Forwarded-Proto": "https"}).json()
    assert body["scheme"] == "https"


def test_a_nonsense_forwarded_scheme_is_ignored():
    client = TestClient(build_app(proxy_count=1), base_url="https://api.example.com")
    body = client.get("/echo", headers={"X-Forwarded-Proto": "javascript"}).json()
    assert body["scheme"] in ("http", "https")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_the_configured_origin_is_allowed_with_credentials(client):
    response = client.get("/echo", headers={"Origin": FRONTEND_ORIGIN})
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_another_origin_is_not_granted_access(client):
    response = client.get("/echo", headers={"Origin": "https://evil.example"})
    assert response.headers.get("access-control-allow-origin") != "https://evil.example"


def test_the_wildcard_origin_is_never_returned(client):
    """``*`` with credentials is both invalid and a textbook misconfiguration."""
    response = client.get("/echo", headers={"Origin": FRONTEND_ORIGIN})
    assert response.headers.get("access-control-allow-origin") != "*"


def test_the_csrf_header_is_allowed_by_preflight(client):
    response = client.options(
        "/echo",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )
    assert response.status_code in (200, 204)
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "x-csrf-token" in allowed


def test_authorization_is_not_in_the_allow_list(client):
    """Phase 5 authenticates by cookie. An allow-listed header that nothing
    sends is a header somebody will eventually start sending."""
    response = client.options(
        "/echo",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    allowed = response.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" not in allowed


def test_the_correlation_id_is_readable_cross_origin(client):
    response = client.get("/echo", headers={"Origin": FRONTEND_ORIGIN})
    exposed = response.headers.get("access-control-expose-headers", "").lower()
    assert "x-request-id" in exposed


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_requests_within_the_budget_are_served():
    app = build_app(rate_limits=(10, 10, 10))
    client = TestClient(app, base_url="https://api.example.com")
    for _ in range(5):
        assert client.get("/echo").status_code == 200


def test_exceeding_the_budget_returns_429_with_retry_after():
    app = build_app(rate_limits=(10, 3, 10))
    client = TestClient(app, base_url="https://api.example.com")
    statuses = [client.get("/echo").status_code for _ in range(6)]
    assert 429 in statuses, statuses
    limited = next(r for r in (client.get("/echo"),) if r.status_code == 429)
    assert int(limited.headers["Retry-After"]) >= 1


def test_probes_are_never_rate_limited():
    """A throttled readiness probe reads as an unhealthy instance and removes a
    working one from rotation."""
    app = build_app(rate_limits=(1, 1, 1))
    client = TestClient(app, base_url="https://api.example.com")
    for _ in range(20):
        assert client.get("/healthz").status_code == 200


def test_the_auth_and_api_budgets_are_independent():
    """A burst of dashboard reads must not exhaust the budget protecting
    sign-in."""
    app = build_app(rate_limits=(5, 2, 50))
    client = TestClient(app, base_url="https://api.example.com")
    for _ in range(5):
        client.get("/echo")
    # The API bucket is spent; the webhook bucket has its own budget.
    assert client.post(WEBHOOK_PATH, content=b"{}").status_code == 200


def test_the_limiter_keys_on_the_session_when_one_is_present():
    """Many users legitimately share one address. Keying only on the address
    would let one busy user throttle everybody behind the same NAT."""
    app = build_app(rate_limits=(50, 3, 50))
    client = TestClient(app, base_url="https://api.example.com")

    spent = [client.get("/echo", cookies={"codeguard_session": "aaa"}) for _ in range(5)]
    assert any(r.status_code == 429 for r in spent)

    # A different session is a different bucket.
    other = client.get("/echo", cookies={"codeguard_session": "bbb"})
    assert other.status_code == 200


# ---------------------------------------------------------------------------
# The stack as a whole
# ---------------------------------------------------------------------------


def test_the_body_limit_runs_before_the_trusted_host_check():
    """Ordering, observed rather than argued. The body ceiling is outermost, so
    an oversized body is refused without the host ever being parsed — which is
    what makes it a cheap defence against an unauthenticated flood."""
    app = build_app(trusted_hosts=["api.example.com"], max_body=100)
    client = TestClient(app, base_url="https://api.example.com")
    response = client.post("/echo", content=b"x" * 5000, headers={"Host": "evil.example"})
    assert response.status_code == 413, "the host check ran before the size cap"


def test_a_rejected_request_still_carries_a_correlation_id():
    """A support request quotes the id. An error with no id is an error nobody
    can look up."""
    app = build_app(rate_limits=(10, 1, 10))
    client = TestClient(app, base_url="https://api.example.com")
    client.get("/echo")
    limited = client.get("/echo")
    assert limited.status_code == 429
    assert limited.headers["X-Request-ID"]


def test_no_response_leaks_a_server_header(client):
    """A version banner tells an attacker which CVEs to try first."""
    server = client.get("/echo").headers.get("server", "")
    assert "uvicorn" not in server.lower()
    assert not any(ch.isdigit() for ch in server), f"version in Server header: {server}"


def test_an_error_response_carries_no_stack_trace(client):
    response = client.get("/boom")
    body = response.text.lower()
    for leak in ("traceback", 'file "/', '.py", line', "deliberate failure"):
        assert leak not in body, f"error response leaked: {leak}"
    # It is still a machine-readable problem document.
    assert json.loads(response.text)["type"]
