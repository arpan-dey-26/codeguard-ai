"""Webhook signature verification.

The most security-critical function in the ingress path, so it gets the most
adversarial test suite. Every case here corresponds to a real way webhook
receivers have been broken in the wild.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json

import pytest
from app.github import signature as sig_module
from app.github.signature import (
    SignatureFailure,
    body_digest,
    compute_signature,
    verify_signature,
)

SECRET = "test-webhook-secret"
BODY = b'{"action":"opened","number":42}'


def _valid_header(secret: str = SECRET, body: bytes = BODY) -> str:
    return compute_signature(secret, body)


# -- the happy path -------------------------------------------------------


def test_valid_signature_is_accepted() -> None:
    assert verify_signature(secret=SECRET, body=BODY, header_value=_valid_header()) is None


def test_signature_matches_githubs_documented_algorithm() -> None:
    """Independently recompute the HMAC rather than trusting our own helper."""
    expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, BODY) == f"sha256={expected}"


def test_uppercase_hex_is_accepted() -> None:
    """Hex case is not semantically meaningful; rejecting it would be a bug."""
    header = _valid_header().replace("sha256=", "")
    assert (
        verify_signature(secret=SECRET, body=BODY, header_value=f"sha256={header.upper()}") is None
    )


# -- rejection ------------------------------------------------------------


def test_missing_signature_is_rejected() -> None:
    assert verify_signature(secret=SECRET, body=BODY, header_value=None) is SignatureFailure.MISSING


def test_empty_signature_is_rejected() -> None:
    assert verify_signature(secret=SECRET, body=BODY, header_value="") is SignatureFailure.MISSING


@pytest.mark.parametrize(
    "header",
    [
        "not-a-signature",
        "sha1=aaaa",
        "sha256=",
        "sha256=zzzz",
        "sha256=" + "a" * 63,  # too short
        "sha256=" + "a" * 65,  # too long
        "SHA256=" + "a" * 64,  # wrong prefix case
        "sha256 " + "a" * 64,  # missing '='
    ],
)
def test_malformed_signatures_are_rejected(header: str) -> None:
    assert (
        verify_signature(secret=SECRET, body=BODY, header_value=header)
        is SignatureFailure.MALFORMED
    )


def test_wrong_secret_is_rejected() -> None:
    forged = compute_signature("attacker-secret", BODY)
    assert (
        verify_signature(secret=SECRET, body=BODY, header_value=forged) is SignatureFailure.MISMATCH
    )


def test_modified_payload_is_rejected() -> None:
    """The whole point: one flipped byte invalidates the signature."""
    header = _valid_header()
    tampered = BODY.replace(b'"opened"', b'"closed"')
    assert (
        verify_signature(secret=SECRET, body=tampered, header_value=header)
        is SignatureFailure.MISMATCH
    )


def test_single_bit_flip_is_rejected() -> None:
    header = _valid_header()
    tampered = bytearray(BODY)
    tampered[5] ^= 0x01
    assert (
        verify_signature(secret=SECRET, body=bytes(tampered), header_value=header)
        is SignatureFailure.MISMATCH
    )


def test_unconfigured_secret_never_passes() -> None:
    """A receiver that accepts everything when unconfigured is an open endpoint.

    Far worse than a 401, and an easy mistake: `if secret: verify(...)`.
    """
    assert (
        verify_signature(secret=None, body=BODY, header_value=_valid_header())
        is SignatureFailure.NO_SECRET_CONFIGURED
    )
    assert (
        verify_signature(secret="", body=BODY, header_value=_valid_header())
        is SignatureFailure.NO_SECRET_CONFIGURED
    )


# -- payload shapes -------------------------------------------------------


def test_unicode_payload_round_trips() -> None:
    """Non-ASCII content must not break byte-level verification.

    Emoji, CJK and RTL text all appear in PR titles and branch names.
    """
    body = json.dumps(
        {"title": "修复 🐛 in café — naïve résumé", "branch": "feature/日本語"},
        ensure_ascii=False,
    ).encode("utf-8")
    assert (
        verify_signature(secret=SECRET, body=body, header_value=compute_signature(SECRET, body))
        is None
    )


def test_reserialised_body_fails_verification() -> None:
    """Proves the raw-bytes requirement is real, not a style preference.

    Parsing and re-dumping changes whitespace and key order, changing the
    bytes, breaking the HMAC. This is the single most common webhook bug.
    """
    header = compute_signature(SECRET, BODY)
    reserialised = json.dumps(json.loads(BODY)).encode()
    assert reserialised != BODY
    assert (
        verify_signature(secret=SECRET, body=reserialised, header_value=header)
        is SignatureFailure.MISMATCH
    )


def test_empty_body_is_handled() -> None:
    assert (
        verify_signature(secret=SECRET, body=b"", header_value=compute_signature(SECRET, b""))
        is None
    )
    assert (
        verify_signature(secret=SECRET, body=b"", header_value=_valid_header())
        is SignatureFailure.MISMATCH
    )


def test_large_body_round_trips() -> None:
    body = b'{"data":"' + b"x" * 500_000 + b'"}'
    assert (
        verify_signature(secret=SECRET, body=body, header_value=compute_signature(SECRET, body))
        is None
    )


def test_unicode_secret_is_supported() -> None:
    secret = "sécret-🔑"
    assert (
        verify_signature(secret=secret, body=BODY, header_value=compute_signature(secret, BODY))
        is None
    )


# -- implementation invariants -------------------------------------------


def test_comparison_is_constant_time() -> None:
    """Asserted at the source level, because timing cannot be measured reliably.

    `==` on a hex digest short-circuits at the first differing byte, leaking
    the expected value through response timing. This test fails if someone
    replaces compare_digest with an equality check.
    """
    source = inspect.getsource(sig_module.verify_signature)
    assert "compare_digest" in source
    assert "== provided" not in source
    assert "provided_hex ==" not in source


def test_body_digest_is_sha256_of_raw_bytes() -> None:
    assert body_digest(BODY) == hashlib.sha256(BODY).hexdigest()
    assert len(body_digest(b"")) == 64


def test_failure_reasons_are_distinguishable_internally() -> None:
    """We distinguish causes for our logs; the endpoint returns one 401.

    Both properties matter: operators need the reason, attackers must not.
    """
    assert len({f.value for f in SignatureFailure}) == 4
