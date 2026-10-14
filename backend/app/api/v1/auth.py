"""Authentication endpoints (architecture.md §17.4).

    GET  /api/v1/auth/github/login      begin OAuth
    GET  /api/v1/auth/github/callback   finish OAuth, issue a session
    POST /api/v1/auth/logout            revoke this session
    POST /api/v1/auth/logout-all        revoke every session for this user
    GET  /api/v1/me                     the current user and their installations

The callback is the interesting one, and the order of its steps is the security
property rather than an implementation detail:

    1. consume the ``state`` — atomically, exactly once, from the database
    2. compare it with the ``state`` cookie — both must match
    3. only then exchange the code
    4. ask GitHub who this is, and what they may see
    5. create the session, then **rotate its token** before responding

Steps 1 and 2 are two independent checks and both are required. The cookie
alone cannot detect replay, because a signed value verifies every time it is
presented. The database row alone would be vulnerable to an attacker who can
start a flow and then feed the victim the resulting callback URL — a login
CSRF, which ends with the victim signed into the *attacker's* account and
typing into it. Requiring the cookie ties the callback to the browser that
began the flow.

Step 5 is session fixation defence. The row is created and its token
immediately replaced, so any identifier that existed before authentication
stops resolving at the moment authentication succeeds.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.authn import OptionalSessionDep, SessionContextDep
from app.api.deps import SessionDep, SettingsDep
from app.auth.cookies import (
    clear_oauth_state_cookie,
    clear_session_cookies,
    oauth_state_cookie_name,
    set_oauth_state_cookie,
    set_session_cookies,
)
from app.auth.oauth import GitHubOAuthClient
from app.auth.tokens import (
    constant_time_equals,
    csrf_token_for,
    hash_ip,
    new_token,
    token_digest,
)
from app.core.config import Settings
from app.core.errors import ConfigurationError, CsrfValidationError
from app.core.logging import get_logger
from app.db.repositories.auth import AuthRepository

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

_STATE_MISMATCH = "The sign-in attempt could not be verified. Please start again."


class UserResponse(BaseModel):
    id: int
    github_user_id: int
    login: str
    name: str | None
    avatar_url: str | None


class InstallationAccessResponse(BaseModel):
    id: int
    account_login: str
    account_type: str
    repository_count: int
    suspended: bool


class MeResponse(BaseModel):
    user: UserResponse
    installations: list[InstallationAccessResponse]
    session_expires_at: str
    access_synced_at: str | None
    csrf_token: str
    """This session's CSRF token, so a freshly-loaded SPA has it without
    parsing cookies. It is also delivered as a readable cookie; both carry the
    same value, and neither is a credential on its own."""


# -- the flow ----------------------------------------------------------------


@router.get(
    "/auth/github/login",
    summary="Begin GitHub OAuth",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
)
async def github_login(
    session: SessionDep,
    settings: SettingsDep,
    redirect_path: Annotated[
        str | None,
        Query(
            alias="next",
            max_length=512,
            description="Where in the SPA to land afterwards. A path, not a URL.",
        ),
    ] = None,
) -> Response:
    """Redirect the browser to GitHub, remembering the ``state`` twice over."""
    if not settings.oauth_configured:
        raise ConfigurationError(
            "Sign-in is not configured for this deployment. Set "
            "GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET and "
            "SESSION_SECRET."
        )

    state = new_token()
    safe_path = _safe_redirect_path(redirect_path)

    repo = AuthRepository(session)
    await repo.purge_expired_oauth_states()
    await repo.create_oauth_state(
        state_hash=token_digest(state),
        redirect_path=safe_path,
        ttl_seconds=settings.oauth_state_ttl_seconds,
    )
    await session.commit()

    async with GitHubOAuthClient(settings) as client:
        target = client.authorization_url(state=state)

    response = RedirectResponse(target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    set_oauth_state_cookie(response, settings=settings, state_token=state)
    logger.info("auth.login.started")
    return response


@router.get(
    "/auth/github/callback",
    summary="Complete GitHub OAuth and start a session",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
)
async def github_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: Annotated[str | None, Query(max_length=512)] = None,
    state: Annotated[str | None, Query(max_length=512)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
) -> Response:
    if error:
        # The user pressed "Cancel", or GitHub declined. Not an error condition
        # for us; send them back to the app with a flag it can render.
        logger.info("auth.login.declined", reason=error[:64])
        return _to_frontend(settings, "/?auth=declined")

    if not code or not state:
        raise CsrfValidationError(_STATE_MISMATCH)

    cookie_state = request.cookies.get(oauth_state_cookie_name(settings))
    if not cookie_state or not constant_time_equals(cookie_state, state):
        # Either the browser that started the flow is not the browser that
        # finished it, or there was no flow. Both are the same refusal.
        logger.warning("auth.login.state_cookie_mismatch")
        raise CsrfValidationError(_STATE_MISMATCH)

    repo = AuthRepository(session)
    consumed, redirect_path = await repo.consume_oauth_state(state_hash=token_digest(state))
    await session.commit()
    if not consumed:
        # Already used, expired, or never existed. Deleting *is* the check, so
        # a replay of the same callback lands here.
        logger.warning("auth.login.state_replayed_or_expired")
        raise CsrfValidationError(_STATE_MISMATCH)

    async with GitHubOAuthClient(settings) as client:
        identity = await client.exchange_and_identify(code=code)

    # Network work is done. Everything below is one short transaction.
    user_id = await repo.upsert_user(
        github_user_id=identity.github_user_id,
        login=identity.login,
        name=identity.name,
        avatar_url=identity.avatar_url,
        email=identity.email,
    )
    granted = await repo.replace_installation_access(
        user_id, github_installation_ids=list(identity.installation_ids)
    )

    provisional = new_token()
    session_id = await repo.create_session(
        user_id=user_id,
        token_hash=token_digest(provisional),
        ttl_seconds=settings.session_ttl_seconds,
        user_agent=request.headers.get("user-agent"),
        ip_hash=hash_ip(_client_ip(request), settings.session_signing_key()),
    )

    # Session fixation defence: the identifier changes at the instant the
    # session becomes authenticated, so nothing minted beforehand still works.
    session_token = new_token()
    await repo.rotate_session_token(session_id, new_token_hash=token_digest(session_token))
    await session.commit()

    logger.info(
        "auth.login.completed",
        user_id=user_id,
        github_user_id=identity.github_user_id,
        installations_reported=len(identity.installation_ids),
        installations_granted=len(granted),
    )

    response = _to_frontend(settings, redirect_path or "/")
    set_session_cookies(
        response,
        settings=settings,
        session_token=session_token,
        csrf_token=csrf_token_for(session_token, settings.session_signing_key()),
        max_age_seconds=settings.session_ttl_seconds,
    )
    clear_oauth_state_cookie(response, settings=settings)
    return response


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate this session",
)
async def logout(
    context: SessionContextDep,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> Response:
    """Revoke the session server-side, then clear the cookies.

    Server-side first, and that order is the point. Clearing a cookie is a
    *request* to a browser; the token would still work for anyone who had
    copied it. Revoking the row is what makes logout mean something.
    """
    await AuthRepository(session).revoke_session(context.session_id, reason="logout")
    await session.commit()
    logger.info("auth.logout", user_id=context.user_id)

    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(out, settings=settings)
    return out


@router.post(
    "/auth/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate every session for this user",
)
async def logout_all(
    context: SessionContextDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    revoked = await AuthRepository(session).revoke_all_sessions(
        context.user_id, reason="logout_all"
    )
    await session.commit()
    logger.info("auth.logout_all", user_id=context.user_id, sessions_revoked=revoked)

    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(out, settings=settings)
    return out


@router.get("/me", response_model=MeResponse, summary="The signed-in user")
async def me(
    context: SessionContextDep,
    session: SessionDep,
    settings: SettingsDep,
) -> MeResponse:
    """The current user and the installations they may read.

    The installation list comes from ``user_installation_access`` — the same
    rows the dashboard's SQL scopes on — so what this endpoint reports and what
    the dashboard actually shows cannot disagree.
    """
    installations = await _installations_for(session, context.installation_ids)
    return MeResponse(
        user=UserResponse(
            id=context.user_id,
            github_user_id=context.github_user_id,
            login=context.login,
            name=context.name,
            avatar_url=context.avatar_url,
        ),
        installations=installations,
        session_expires_at=context.expires_at.isoformat(),
        access_synced_at=(
            context.access_synced_at.isoformat() if context.access_synced_at else None
        ),
        csrf_token=csrf_token_for(context.session_token, settings.session_signing_key()),
    )


@router.get(
    "/auth/session",
    summary="Whether this browser has a session",
    response_model=dict,
)
async def session_state(context: OptionalSessionDep, settings: SettingsDep) -> dict[str, object]:
    """A cheap, always-200 probe for the SPA's boot sequence.

    Exists so the frontend can decide between "render the app" and "show the
    sign-in page" without treating a 401 from ``/me`` as an error to display.
    Returns no user data when unauthenticated.

    ``unauthenticated_reads_enabled`` was added in v0.6.1. It reports a fact
    about this deployment that ``/health`` and ``/api/v1/system/settings``
    already publish, so it discloses nothing new — and without it the SPA could
    not render at all in the one mode built for running the dashboard without
    OAuth. The flag itself grants nothing here: ``resolve_scope`` decides what
    any query may read, and it reads the same setting server-side. This value
    lets the client *describe* the situation honestly instead of showing a
    sign-in page for a deployment that is deliberately not asking anyone to sign
    in. Production refuses to start with the flag on (``Settings`` validation),
    so this can only ever be ``true`` outside production.
    """
    return {
        "authenticated": context is not None,
        "login_url": "/api/v1/auth/github/login",
        "sign_in_available": settings.oauth_configured,
        "unauthenticated_reads_enabled": settings.dashboard_allow_unauthenticated_reads,
    }


# -- helpers -----------------------------------------------------------------


async def _installations_for(
    session: SessionDep, installation_ids: tuple[int, ...]
) -> list[InstallationAccessResponse]:
    if not installation_ids:
        return []
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            SELECT i.id,
                   i.account_login,
                   i.account_type,
                   i.suspended_at,
                   COUNT(r.id) FILTER (WHERE r.is_active) AS repository_count
              FROM installations i
              LEFT JOIN repositories r ON r.installation_id = i.id
             WHERE i.id = ANY(CAST(:ids AS BIGINT[]))
             GROUP BY i.id, i.account_login, i.account_type, i.suspended_at
             ORDER BY i.account_login
            """
        ),
        {"ids": list(installation_ids)},
    )
    return [
        InstallationAccessResponse(
            id=int(row["id"]),
            account_login=str(row["account_login"]),
            account_type=str(row["account_type"]),
            repository_count=int(row["repository_count"] or 0),
            suspended=row["suspended_at"] is not None,
        )
        for row in result.mappings()
    ]


def _safe_redirect_path(value: str | None) -> str | None:
    """Accept an in-app path; reject anything that could leave the origin.

    Open redirect is the standard flaw in a "return to where I was" feature,
    and the standard near-miss fix is to check for ``http://``. That is not
    enough: ``//evil.example`` is a protocol-relative URL the browser treats as
    absolute, and ``/\\evil.example`` is normalised to the same thing by some
    browsers. The rule here is positive rather than a blacklist — one leading
    slash, no second slash, no backslash, no scheme — because a blacklist of
    the shapes people have thought of is not a defence against the next one.
    """
    if not value:
        return None
    # No control character, anywhere. This value is interpolated into a
    # ``Location`` header, and a bare CR or LF there is response-splitting —
    # the attacker appends their own headers, or a whole second response.
    #
    # The previous version caught ``\r\n`` only by accident: the ``:`` check
    # happened to match the injected ``Set-Cookie:``. A payload with a newline
    # and no colon went through. Phase 6 makes the rejection deliberate rather
    # than incidental, and rejects tabs and other control characters with it,
    # since none of them belongs in a path this application generates.
    if any(ch < " " or ch == "\x7f" for ch in value):
        return None
    if not value.startswith("/"):
        return None
    if value.startswith("//") or value.startswith("/\\"):
        return None
    if "\\" in value or ":" in value.split("/", 2)[1][:16]:
        return None
    return value[:512]


def _to_frontend(settings: Settings, path: str) -> RedirectResponse:
    base = settings.frontend_url.rstrip("/")
    safe = path if path.startswith("/") else f"/{path}"
    return RedirectResponse(f"{base}{safe}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


def _client_ip(request: Request) -> str | None:
    """The client address as the proxy configuration allows us to believe it.

    ``request.client.host`` is already the corrected value when
    ``TRUSTED_PROXY_COUNT`` is set, because the proxy middleware rewrites it
    before routing. Reading ``X-Forwarded-For`` here instead would trust a
    header any client can send.
    """
    return request.client.host if request.client else None


__all__ = ["router"]
