"""Session tokens, their storage form, and the CSRF derivation.

Pure functions, so these tests need no database, no event loop and no web
framework — which is the reason the primitives were separated from the routes
in the first place. Everything asserted here is a property somebody could break
with a one-line "simplification", and each test says what breaking it would
cost.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from app.auth.tokens import (
    constant_time_equals,
    csrf_token_for,
    csrf_token_matches,
    hash_ip,
    new_token,
    token_digest,
)

KEY = b"a" * 64
OTHER_KEY = b"b" * 64


# -- opaque tokens -----------------------------------------------------------


def test_tokens_are_unique_across_many_draws():
    """Not a test of randomness — a test that somebody has not replaced
    ``secrets.token_urlsafe`` with something seeded or counted."""
    tokens = {new_token() for _ in range(2000)}
    assert len(tokens) == 2000


def test_tokens_carry_at_least_128_bits_of_entropy():
    """43 URL-safe characters ≈ 256 bits. The floor asserted here is 128, which
    is the point below which offline guessing stops being hopeless."""
    token = new_token()
    assert len(token) >= 22, "token is too short to be unguessable"
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", token), "token must be URL-safe"


def test_tokens_are_url_safe_so_a_cookie_never_needs_escaping():
    # A token containing '=' or ';' would need quoting in a Set-Cookie header,
    # and a mis-quoted cookie is truncated rather than rejected — producing an
    # invalid session that looks like an expired one.
    for _ in range(200):
        token = new_token()
        assert "=" not in token and ";" not in token and "," not in token


# -- the stored form ---------------------------------------------------------


def test_the_digest_is_sha256_hex():
    token = "known-value"
    assert token_digest(token) == hashlib.sha256(token.encode()).hexdigest()
    assert len(token_digest(token)) == 64


def test_the_digest_is_deterministic_and_the_plaintext_is_not_recoverable():
    token = new_token()
    assert token_digest(token) == token_digest(token)
    assert token not in token_digest(token)


def test_different_tokens_produce_different_digests():
    assert token_digest(new_token()) != token_digest(new_token())


# -- CSRF --------------------------------------------------------------------


def test_a_csrf_token_verifies_for_its_own_session():
    session = new_token()
    assert csrf_token_matches(csrf_token_for(session, KEY), session, KEY)


def test_a_csrf_token_does_not_verify_for_a_different_session():
    """The property that defeats the 'log the victim into my account, then get
    them to submit' variant: an attacker's own valid CSRF token is useless
    against somebody else's session."""
    victim = new_token()
    attacker = new_token()
    attacker_csrf = csrf_token_for(attacker, KEY)
    assert not csrf_token_matches(attacker_csrf, victim, KEY)


def test_a_csrf_token_does_not_verify_under_a_different_key():
    """Rotating SESSION_SECRET must invalidate outstanding CSRF tokens; if it
    did not, a leaked key would stay useful after the rotation meant to
    contain it."""
    session = new_token()
    assert not csrf_token_matches(csrf_token_for(session, KEY), session, OTHER_KEY)


@pytest.mark.parametrize("presented", [None, "", "not-a-token", "0" * 64])
def test_a_missing_or_wrong_csrf_token_is_rejected(presented):
    session = new_token()
    assert not csrf_token_matches(presented, session, KEY)


def test_the_csrf_token_is_not_the_session_token():
    """If it were, a readable CSRF cookie would hand out the session — which is
    exactly what HttpOnly on the session cookie exists to prevent."""
    session = new_token()
    assert csrf_token_for(session, KEY) != session


def test_the_csrf_token_reveals_nothing_about_the_session_token():
    session = new_token()
    derived = csrf_token_for(session, KEY)
    # No shared substring of meaningful length. A naive "session + salt"
    # implementation would fail this.
    for size in range(8, min(len(session), len(derived)) + 1):
        assert session[:size] not in derived


# -- comparison --------------------------------------------------------------


def test_constant_time_equals_agrees_with_equality():
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")
    assert not constant_time_equals("abc", "ab")
    assert not constant_time_equals("", "x")


def test_comparison_handles_non_ascii_without_raising():
    # A token never contains non-ASCII, but this function also compares
    # attacker-supplied values, and an exception here would be a 500 that
    # distinguishes "malformed" from "wrong".
    assert not constant_time_equals("token", "tökén")


# -- IP hashing --------------------------------------------------------------


def test_ip_hashing_is_keyed_so_the_ipv4_space_cannot_be_enumerated():
    assert hash_ip("203.0.113.4", KEY) != hash_ip("203.0.113.4", OTHER_KEY)


def test_ip_hashing_is_stable_for_the_same_address():
    assert hash_ip("203.0.113.4", KEY) == hash_ip("203.0.113.4", KEY)


def test_a_missing_address_hashes_to_nothing():
    assert hash_ip(None, KEY) is None
    assert hash_ip("", KEY) is None


def test_the_raw_address_never_appears_in_the_hash():
    assert "203.0.113.4" not in (hash_ip("203.0.113.4", KEY) or "")
