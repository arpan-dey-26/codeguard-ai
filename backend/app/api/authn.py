"""Resolving a request to a caller — and refusing when it cannot be done.

This module is the enforcement point. Everything the dashboard shows is scoped
by what happens here, so it is written to be boring and to have exactly one way
through.

**What identity is NOT taken from.** Not a header the client sets, not a query
parameter, not a request body field, not a claim in the JSON, not a value the
frontend stored. The only input is the session cookie, and the only thing that
turns it into an identity is a row in ``user_sessions``. There is no
development bypass, no "trusted" header, no impersonation parameter — those are
the mechanisms that get shipped by accident and then found by somebody else.

**Why the scope is not a parameter.** A caller never supplies an installation
id to widen what they see (architecture.md §17.5, NFR-S8). The installation ids
come out of the same query that validated the session. An endpoint *can* take
``repository_id`` as a filter — but it narrows within the scope, and the SQL
applies the scope regardless, so a repository outside it returns nothing no
matter what is asked for.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import SessionDep, SettingsDep
from app.auth.cookies import CSRF_HEADER
from app.auth.tokens import csrf_token_matches, token_digest
from app.core.config import Settings
from app.core.errors import AuthenticationRequiredError, CsrfValidationError
from app.core.logging import get_logger
from app.db.repositories.auth import AuthRepository, SessionContext

logger = get_logger(__name__)

# Methods that cannot change state, and therefore do not need a CSRF token.
# HEAD and OPTIONS are included because a browser issues them on its own.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def resolve_session(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> SessionContext | None:
    """Return the caller's session, or ``None`` when there is none.

    Does **not** raise. Endpoints that require a session use
    ``require_session``; endpoints that merely behave differently when signed in
    use this. Keeping the two apart means an endpoint's requirement is visible
    in its signature.
    """
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None

    repo = AuthRepository(session)
    context = await repo.load_session(
        token_hash=token_digest(token),
        idle_timeout_seconds=settings.session_idle_timeout_seconds,
    )
    if context is None:
        return None

    # The plaintext never leaves this request; it is carried on the context so
    # the CSRF check can derive the expected token without a second lookup.
    context = _with_token(context, token)

    # Advance the idle clock. Committed here because a read endpoint otherwise
    # leaves the transaction open with nothing to close it, and losing a touch
    # would expire an actively-used session.
    await repo.touch_session(context.session_id)
    await session.commit()
    return context


def _with_token(context: SessionContext, token: str) -> SessionContext:
    return SessionContext(
        session_id=context.session_id,
        user_id=context.user_id,
        github_user_id=context.github_user_id,
        login=context.login,
        name=context.name,
        avatar_url=context.avatar_url,
        session_token=token,
        expires_at=context.expires_at,
        last_seen_at=context.last_seen_at,
        installation_ids=context.installation_ids,
        access_synced_at=context.access_synced_at,
    )


OptionalSessionDep = Annotated[SessionContext | None, Depends(resolve_session)]


async def require_session(
    request: Request,
    context: OptionalSessionDep,
    settings: SettingsDep,
) -> SessionContext:
    """The caller must be signed in. Enforces CSRF on unsafe methods.

    One 401 for every failure mode, with no detail. "No session", "expired
    session" and "revoked session" are the same answer to the caller and three
    different log lines for us — telling them apart out loud would confirm to
    somebody probing which of their guesses had once been a real session.
    """
    if context is None:
        raise AuthenticationRequiredError("Sign in to continue.")

    if request.method not in _SAFE_METHODS:
        _require_csrf(request, context, settings)

    return context


def _require_csrf(request: Request, context: SessionContext, settings: Settings) -> None:
    """Double-submit check, bound to the session by HMAC.

    **CORS is not CSRF protection**, and the two are constantly confused. A
    browser's preflight can stop *JavaScript* from reading a cross-origin
    response, but an HTML form or an image tag can still cause the request to be
    sent with cookies attached, and the server executes it before any CORS
    header is considered. ``SameSite=Lax`` removes most of that, and this check
    removes the rest — including the cases ``Lax`` does not cover, such as a
    same-site subdomain an attacker controls.

    The token is HMAC-derived from the session token, so it cannot be moved
    between sessions: an attacker's own valid token does not verify against the
    victim's session.
    """
    presented = request.headers.get(CSRF_HEADER)
    if not csrf_token_matches(presented, context.session_token, settings.session_signing_key()):
        logger.warning(
            "auth.csrf.rejected",
            path=request.url.path,
            method=request.method,
            user_id=context.user_id,
            header_present=presented is not None,
        )
        raise CsrfValidationError(f"A valid {CSRF_HEADER} header is required for this request.")


SessionContextDep = Annotated[SessionContext, Depends(require_session)]


__all__ = [
    "OptionalSessionDep",
    "SessionContextDep",
    "require_session",
    "resolve_session",
]
