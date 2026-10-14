"""Opaque tokens, their hashes, and the CSRF token derived from a session.

Three primitives, each doing one thing.

**Opaque tokens** (`new_token`). 256 bits from ``secrets.token_urlsafe``. Not a
JWT and not signed, because there is nothing in them to read: the value is a
lookup key for a server-side row, so forging one means guessing 256 bits.

**Hashing** (`token_digest`). Plain SHA-256, hex. A password hash would be the
wrong tool: the input is already full-entropy random, so there is no dictionary
to slow down, and a KDF would add its cost to *every authenticated request*.
The property being bought is that a database dump contains no usable session —
and SHA-256 over 256 random bits gives exactly that.

**CSRF tokens** (`csrf_token_for`, `csrf_token_matches`). Derived from the
session by HMAC, so they need no second table and cannot be transplanted
between sessions: a token minted for session A does not verify for session B.

Everything here is pure and synchronous, so it is testable without a database,
an event loop or a web framework.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 32 bytes → 43 URL-safe characters. Well above the 128-bit floor for a value
# whose only defence is being unguessable.
_TOKEN_BYTES = 32

_CSRF_LABEL = b"codeguard.csrf.v1"
_IP_LABEL = b"codeguard.ip.v1"


def new_token() -> str:
    """A fresh opaque token, safe to place in a cookie or a URL."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(token: str) -> str:
    """The stored form of a token: SHA-256, hex, lower case."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking their common prefix length.

    ``==`` on a token returns as soon as two characters differ, and the timing
    difference is measurable across enough samples. Every token comparison in
    the application goes through here.
    """
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def csrf_token_for(session_token: str, key: bytes) -> str:
    """Derive this session's CSRF token.

    HMAC(key, label ‖ session_token). Three properties follow, and all three
    matter:

    * **Bound to the session.** A token from another session — including an
      attacker's own valid session — does not verify against this one, which is
      what defeats the "log the victim into my account, then submit" variant.
    * **Stateless.** No second table, no expiry to manage separately: the token
      dies exactly when its session does.
    * **Not derivable by the attacker.** They cannot compute it without the
      server key, and they cannot read it from the cookie because the session
      cookie is ``HttpOnly``. It is delivered in a *readable* companion cookie
      and echoed in a header — the double-submit pattern — so same-origin
      JavaScript can send it and cross-origin JavaScript cannot read it.
    """
    mac = hmac.new(key, _CSRF_LABEL + session_token.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def csrf_token_matches(presented: str | None, session_token: str, key: bytes) -> bool:
    """Whether ``presented`` is the CSRF token for this session."""
    if not presented:
        return False
    return constant_time_equals(presented, csrf_token_for(session_token, key))


def hash_ip(ip: str | None, key: bytes) -> str | None:
    """Keyed hash of a client address, for session records.

    Keyed rather than plain, so the small IPv4 space cannot simply be
    enumerated against the stored digests.
    """
    if not ip:
        return None
    return hmac.new(key, _IP_LABEL + ip.encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = [
    "constant_time_equals",
    "csrf_token_for",
    "csrf_token_matches",
    "hash_ip",
    "new_token",
    "token_digest",
]
