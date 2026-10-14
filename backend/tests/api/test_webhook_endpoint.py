"""The webhook endpoint.

Exercises the HTTP surface: signature enforcement, header validation, payload
handling and response shape. The database-backed behaviour (idempotency,
transactional enqueue) is tested separately in ``tests/integration``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.github.signature import SIGNATURE_HEADER, compute_signature
from fastapi.testclient import TestClient

WEBHOOK_PATH = "/api/v1/webhooks/github"
SAMPLES = Path(__file__).resolve().parents[3] / "infrastructure" / "scripts" / "sample_payloads"


def _body(name: str = "pull_request_opened.json") -> bytes:
    return (SAMPLES / name).read_bytes()


def _headers(
    body: bytes, secret: str, *, event: str = "pull_request", delivery: str = "d-1"
) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        SIGNATURE_HEADER: compute_signature(secret, body),
    }


# -- rejection ------------------------------------------------------------


def test_missing_signature_is_rejected(webhook_client: TestClient) -> None:
    response = webhook_client.post(
        WEBHOOK_PATH,
        content=_body(),
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-1"},
    )
    assert response.status_code == 401


def test_invalid_signature_is_rejected(webhook_client: TestClient, webhook_secret: str) -> None:
    body = _body()
    headers = _headers(body, "wrong-secret")
    assert webhook_client.post(WEBHOOK_PATH, content=body, headers=headers).status_code == 401


def test_malformed_signature_is_rejected(webhook_client: TestClient) -> None:
    body = _body()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "d-1",
        SIGNATURE_HEADER: "garbage",
    }
    assert webhook_client.post(WEBHOOK_PATH, content=body, headers=headers).status_code == 401


def test_tampered_body_is_rejected(webhook_client: TestClient, webhook_secret: str) -> None:
    """A signature valid for one body must not validate a different one."""
    body = _body()
    headers = _headers(body, webhook_secret)
    tampered = body.replace(b'"opened"', b'"closed"')
    assert webhook_client.post(WEBHOOK_PATH, content=tampered, headers=headers).status_code == 401


def test_rejection_reveals_nothing_about_why(
    webhook_client: TestClient, webhook_secret: str
) -> None:
    """Telling a prober why verification failed is free information."""
    body = _body()
    response = webhook_client.post(
        WEBHOOK_PATH, content=body, headers=_headers(body, "wrong-secret")
    )
    text = response.text.lower()
    for leak in ("mismatch", "secret", "expected", "hmac", "digest", "traceback"):
        assert leak not in text


def test_missing_required_headers_are_rejected(
    webhook_client: TestClient, webhook_secret: str
) -> None:
    """A valid signature does not make the headers present."""
    body = _body()
    signature = compute_signature(webhook_secret, body)

    no_event = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers={"X-GitHub-Delivery": "d-1", SIGNATURE_HEADER: signature},
    )
    no_delivery = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers={"X-GitHub-Event": "pull_request", SIGNATURE_HEADER: signature},
    )
    assert no_event.status_code == 422
    assert no_delivery.status_code == 422


def test_malformed_json_with_a_valid_signature_is_rejected(
    webhook_client: TestClient, webhook_secret: str
) -> None:
    """Authenticity and validity are separate questions."""
    body = b"{not valid json"
    response = webhook_client.post(
        WEBHOOK_PATH, content=body, headers=_headers(body, webhook_secret)
    )
    assert response.status_code == 422


def test_json_array_body_is_rejected(webhook_client: TestClient, webhook_secret: str) -> None:
    body = b'["not", "an", "object"]'
    response = webhook_client.post(
        WEBHOOK_PATH, content=body, headers=_headers(body, webhook_secret)
    )
    assert response.status_code == 422


def test_oversized_body_is_rejected_before_verification(
    webhook_client: TestClient, webhook_secret: str
) -> None:
    """The size guard runs before crypto, so a flood costs us nothing."""
    body = b'{"x":"' + b"a" * (2 * 1024 * 1024) + b'"}'
    response = webhook_client.post(
        WEBHOOK_PATH, content=body, headers=_headers(body, webhook_secret)
    )
    assert response.status_code == 413


# -- acceptance -----------------------------------------------------------


@pytest.mark.parametrize("sample", ["pull_request_opened.json", "pull_request_synchronize.json"])
def test_supported_pull_request_events_are_accepted(
    webhook_client: TestClient, webhook_secret: str, sample: str
) -> None:
    body = _body(sample)
    response = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_headers(body, webhook_secret, delivery=f"d-{sample}"),
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_unsupported_event_is_acknowledged_not_errored(
    webhook_client: TestClient, webhook_secret: str
) -> None:
    """An error here would make GitHub's delivery log show red for correct behaviour."""
    body = _body("unsupported_event.json")
    response = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_headers(body, webhook_secret, event="issues", delivery="d-issues"),
    )
    assert response.status_code == 204


def test_draft_pull_request_is_acknowledged_without_work(
    webhook_client: TestClient, webhook_secret: str
) -> None:
    body = _body("pull_request_draft.json")
    response = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_headers(body, webhook_secret, delivery="d-draft"),
    )
    assert response.status_code == 204


def test_ping_is_acknowledged(webhook_client: TestClient, webhook_secret: str) -> None:
    body = json.dumps({"zen": "Design for failure.", "hook_id": 1}).encode()
    response = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_headers(body, webhook_secret, event="ping", delivery="d-ping"),
    )
    assert response.status_code == 204


def test_unicode_payload_is_accepted(webhook_client: TestClient, webhook_secret: str) -> None:
    payload = json.loads(_body())
    payload["pull_request"]["title"] = "修复 🐛 in café — naïve"
    payload["pull_request"]["head"]["ref"] = "feature/日本語"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_headers(body, webhook_secret, delivery="d-unicode"),
    )
    assert response.status_code == 202


def test_response_carries_a_correlation_id(webhook_client: TestClient, webhook_secret: str) -> None:
    body = _body()
    response = webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers=_headers(body, webhook_secret, delivery="d-corr"),
    )
    assert response.headers["X-Request-ID"]


def test_no_secret_configured_means_nothing_is_accepted(
    unconfigured_webhook_client: TestClient,
) -> None:
    """A receiver that accepts everything when unconfigured is an open endpoint."""
    body = _body()
    response = unconfigured_webhook_client.post(
        WEBHOOK_PATH,
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d-1",
            SIGNATURE_HEADER: compute_signature("any-secret", body),
        },
    )
    assert response.status_code == 401
