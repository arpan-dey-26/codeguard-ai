"""Application startup and the health endpoints."""

from __future__ import annotations

from app.core.correlation import CORRELATION_HEADER
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_application_starts(app: FastAPI) -> None:
    """The factory builds an app and mounts the expected routes."""
    paths = set(app.openapi()["paths"])
    assert {"/health", "/healthz", "/readyz", "/api/v1/meta"} <= paths


def test_liveness_never_touches_a_dependency(client: TestClient) -> None:
    """Returns 200 even with no database — that is the whole point."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_a_meaningful_report(client: TestClient) -> None:
    response = client.get("/health")
    # 200 when the database is up, 503 when it is not; both are valid answers
    # and both must carry the full report.
    assert response.status_code in (200, 503)
    body = response.json()
    assert body["app"] == "CodeGuard AI"
    assert body["environment"] == "test"
    assert body["status"] in {"ok", "degraded", "error"}
    assert body["uptime_seconds"] >= 0
    assert any(dep["name"] == "postgresql" for dep in body["dependencies"])


def test_health_reports_the_ai_provider_without_leaking_credentials(
    client: TestClient,
) -> None:
    body = client.get("/health").json()["ai_provider"]
    assert body["name"] == "gemini"
    assert body["configured"] is False
    assert body["implemented"] is True
    assert set(body["available"]) == {"gemini", "claude"}


def test_health_response_contains_no_connection_string(client: TestClient) -> None:
    raw = client.get("/health").text
    for forbidden in ("postgresql+asyncpg", "password", "@localhost:5432"):
        assert forbidden not in raw


def test_readiness_reports_database_and_migration_state(client: TestClient) -> None:
    response = client.get("/readyz")
    body = response.json()
    assert set(body) == {"status", "database", "migration_revision"}
    if body["database"] is False:
        assert response.status_code == 503


def test_every_response_carries_a_correlation_id(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers[CORRELATION_HEADER]


def test_an_inbound_correlation_id_is_adopted(client: TestClient) -> None:
    response = client.get("/healthz", headers={CORRELATION_HEADER: "trace-abc-123"})
    assert response.headers[CORRELATION_HEADER] == "trace-abc-123"


def test_an_absurd_inbound_correlation_id_is_replaced(client: TestClient) -> None:
    """Client-controlled values reach log records, so they are bounded."""
    response = client.get("/healthz", headers={CORRELATION_HEADER: "x" * 5000})
    assert response.headers[CORRELATION_HEADER] != "x" * 5000


def test_security_headers_are_present(client: TestClient) -> None:
    headers = client.get("/healthz").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_api_meta_endpoint(client: TestClient) -> None:
    body = client.get("/api/v1/meta").json()
    assert body["api_version"] == "v1"
    assert body["environment"] == "test"


def test_unknown_route_returns_a_problem_document(client: TestClient) -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["correlation_id"]
    # No stack trace, no internals.
    assert "Traceback" not in response.text
