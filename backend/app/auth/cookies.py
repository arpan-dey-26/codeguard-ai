"""Cookie construction. One place, so the attributes cannot drift.

Two cookies, deliberately different:

``codeguard_session``   HttpOnly. The credential. JavaScript must never be able
                        to read it — that is what turns an XSS bug from "steal
                        the session" into "act within the session while the
                        page is open".

``codeguard_csrf``      **Not** HttpOnly, on purpose. The frontend has to read
                        it to echo it in a header, which is the whole mechanism
                        (see ``app.auth.tokens.csrf_token_for``). It is not a
                        credential: on its own it authenticates nothing.

The invariant this module exists to enforce: a cookie is never cleared with
different attributes than it was set with. A browser matches a deletion by
name, path and domain, so a ``Path=/`` cookie cleared at ``Path=/api`` is not
cleared at all — the user clicks "sign out", sees a success message, and stays
signed in. Both the set and the clear go through the same helper here.
"""

from __future__ import annotations

from typing import Literal

from starlette.responses import Response

from app.core.config import Settings

SESSION_COOKIE_PATH = "/"
CSRF_COOKIE_SUFFIX = "_csrf"

# Header the frontend echoes the CSRF token in. Must match the frontend's
# constant and the CORS allow-list.
CSRF_HEADER = "X-CSRF-Token"


def csrf_cookie_name(settings: Settings) -> str:
    return f"{settings.session_cookie_name}{CSRF_COOKIE_SUFFIX}"


def _same_site(settings: Settings) -> Literal["lax", "strict", "none"]:
    return settings.session_cookie_samesite


def set_session_cookies(
    response: Response,
    *,
    settings: Settings,
    session_token: str,
    csrf_token: str,
    max_age_seconds: int,
) -> None:
    """Attach both cookies to a response.

    ``max_age`` is set from the session's absolute expiry rather than left as a
    browser-session cookie, so closing the tab is not the only thing that ends
    it and a stale cookie stops being sent once it can no longer work.
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=max_age_seconds,
        path=SESSION_COOKIE_PATH,
        domain=settings.session_cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=_same_site(settings),
    )
    response.set_cookie(
        key=csrf_cookie_name(settings),
        value=csrf_token,
        max_age=max_age_seconds,
        path=SESSION_COOKIE_PATH,
        domain=settings.session_cookie_domain,
        secure=settings.cookie_secure,
        # Readable by design. See this module's docstring.
        httponly=False,
        samesite=_same_site(settings),
    )


def clear_session_cookies(response: Response, *, settings: Settings) -> None:
    """Remove both cookies, with exactly the attributes they were set with."""
    for name in (settings.session_cookie_name, csrf_cookie_name(settings)):
        response.delete_cookie(
            key=name,
            path=SESSION_COOKIE_PATH,
            domain=settings.session_cookie_domain,
            secure=settings.cookie_secure,
            httponly=name == settings.session_cookie_name,
            samesite=_same_site(settings),
        )


def set_oauth_state_cookie(response: Response, *, settings: Settings, state_token: str) -> None:
    """Hold the OAuth ``state`` for the duration of the redirect round trip.

    ``SameSite=Lax`` is required here and is not a default carried over by
    accident: the browser arrives back at our callback as a **cross-site
    top-level navigation** from github.com. ``Strict`` would withhold the
    cookie on exactly that request, and the flow would fail every time with an
    error that looks like a state mismatch.
    """
    response.set_cookie(
        key=oauth_state_cookie_name(settings),
        value=state_token,
        max_age=settings.oauth_state_ttl_seconds,
        path=SESSION_COOKIE_PATH,
        domain=settings.session_cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_oauth_state_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        key=oauth_state_cookie_name(settings),
        path=SESSION_COOKIE_PATH,
        domain=settings.session_cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def oauth_state_cookie_name(settings: Settings) -> str:
    return f"{settings.session_cookie_name}_oauth_state"


__all__ = [
    "CSRF_HEADER",
    "SESSION_COOKIE_PATH",
    "clear_oauth_state_cookie",
    "clear_session_cookies",
    "csrf_cookie_name",
    "oauth_state_cookie_name",
    "set_oauth_state_cookie",
    "set_session_cookies",
]
