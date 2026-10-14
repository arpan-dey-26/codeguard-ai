"""GitHub webhook signature verification.

The single most security-critical function in the ingress path, and
deliberately the smallest: stdlib only, no I/O, no framework, no logging.
That makes it exhaustively testable and trivially auditable.

Three invariants, each of which has been a real CVE in someone's webhook
receiver:

1.  **Verify the raw bytes.**  The HMAC covers exactly the bytes GitHub sent.
    Any framework that hands you a parsed object and re-serialises it produces
    different bytes — key order, whitespace, unicode escaping — and the
    signature will not match.  Callers must pass ``Request.body()``.

2.  **Compare in constant time.**  ``==`` on a hex digest short-circuits at
    the first differing byte, leaking the secret through response timing one
    character at a time.  ``hmac.compare_digest`` does not.

3.  **Never reveal why verification failed.**  Missing, malformed and wrong
    are all just "invalid" to the caller.  Distinguishing them is free
    information for someone probing the secret.  The distinction is returned
    to *us* as a ``SignatureFailure`` for logging, never to the client.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from enum import StrEnum

SIGNATURE_HEADER = "X-Hub-Signature-256"
_PREFIX = "sha256="
# GitHub sends lowercase hex; accept either case but nothing else.
_HEX_64 = re.compile(r"\A[0-9a-fA-F]{64}\Z")


class SignatureFailure(StrEnum):
    """Why verification failed. For our logs only — never sent to the client."""

    MISSING = "missing"
    MALFORMED = "malformed"
    MISMATCH = "mismatch"
    NO_SECRET_CONFIGURED = "no_secret_configured"  # noqa: S105 - a failure reason, not a secret


def compute_signature(secret: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` signature GitHub would send for ``body``.

    Shared with the offline replay harness, so that a replayed delivery is
    signed by exactly the code that verifies it — a harness with its own
    signing implementation can drift and mask a real bug.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def verify_signature(
    *, secret: str | None, body: bytes, header_value: str | None
) -> SignatureFailure | None:
    """Verify a GitHub webhook signature.

    Returns ``None`` when the signature is valid, otherwise the reason.

    Args:
        secret: the configured webhook secret. ``None`` means the deployment
            is not configured — treated as a failure, never as a pass. A
            receiver that accepts everything when its secret is unset is an
            open endpoint, and that is a far worse outcome than a 401.
        body: the exact raw request bytes. Never a re-serialised object.
        header_value: the ``X-Hub-Signature-256`` header, if present.
    """
    if not secret:
        return SignatureFailure.NO_SECRET_CONFIGURED
    if not header_value:
        return SignatureFailure.MISSING

    if not header_value.startswith(_PREFIX):
        return SignatureFailure.MALFORMED
    provided_hex = header_value[len(_PREFIX) :]
    if not _HEX_64.match(provided_hex):
        # Rejecting the shape before comparing means a garbage header costs one
        # regex rather than an HMAC computation, and keeps compare_digest fed
        # with equal-length inputs.
        return SignatureFailure.MALFORMED

    expected_hex = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hex, provided_hex.lower()):
        return SignatureFailure.MISMATCH
    return None


def body_digest(body: bytes) -> str:
    """SHA-256 of the raw body, for the audit trail.

    Stored on ``webhook_events`` instead of the payload itself: it proves "we
    saw exactly these bytes" without retaining third-party content
    (architecture.md §16.10).
    """
    return hashlib.sha256(body).hexdigest()
