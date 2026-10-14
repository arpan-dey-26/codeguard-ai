"""Shared plumbing for the dashboard routes.

Small on purpose. The one thing worth centralising is the *order* of the three
steps every route takes — scope, validate, query — because a route that skips
the first is an unscoped route and nothing else would catch it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from fastapi import Query as QueryParam

from app.api.authn import OptionalSessionDep
from app.api.deps import SettingsDep
from app.api.filters import TimeRange, parse_time_range
from app.api.pagination import clamp_limit, decode_cursor
from app.api.scope import ViewerScope, resolve_scope
from app.core.config import Settings


def provide_scope(settings: SettingsDep, context: OptionalSessionDep) -> ViewerScope:
    """Resolve the caller's scope, or refuse to serve.

    A dependency rather than a call inside each handler: a handler that forgets
    to call it would silently serve unscoped data, while a handler that forgets
    to declare the dependency has no ``ViewerScope`` to pass to a query and does
    not compile against the query signatures.

    Phase 5 adds the session to this signature and changes nothing else. Every
    route already depends on ``ScopeDep``, so wiring authentication in here
    authenticates all of them at once — there is no per-route opt-in that a new
    endpoint could forget.
    """
    return resolve_scope(settings, context)


ScopeDep = Annotated[ViewerScope, Depends(provide_scope)]

LimitQuery = Annotated[
    int | None,
    QueryParam(ge=1, le=500, description="Page size. Clamped server-side."),
]
CursorQuery = Annotated[str | None, QueryParam(description="Opaque keyset cursor.")]
SinceQuery = Annotated[str | None, QueryParam(alias="from", description="ISO-8601 UTC.")]
UntilQuery = Annotated[str | None, QueryParam(alias="to", description="ISO-8601 UTC.")]


def page_args(
    limit: int | None, cursor: str | None, settings: Settings
) -> tuple[int, dict[str, Any]]:
    """Clamp the page size and decode the cursor.

    The clamp uses the deployment's ceiling *and* the module's own, so a
    misconfigured ``DASHBOARD_MAX_PAGE_SIZE`` cannot raise the limit above what
    the pagination module considers serviceable.
    """
    effective = clamp_limit(limit)
    return min(effective, settings.dashboard_max_page_size), decode_cursor(cursor)


def time_range(since: str | None, until: str | None, *, default_days: int = 30) -> TimeRange:
    return parse_time_range(since, until, default_days=default_days)


def redact_error(message: str | None, *, limit: int = 300) -> str | None:
    """Trim an operational error before it reaches a browser.

    ``review_runs.error_message`` is written by the worker and rendered on the
    review page. It is already redacted at write time; this is the second pass,
    because a value that crosses a trust boundary should be cleaned at both
    ends rather than at whichever end someone remembers.
    """
    if not message:
        return None
    collapsed = " ".join(message.split())
    return collapsed[:limit]


__all__ = [
    "CursorQuery",
    "LimitQuery",
    "ScopeDep",
    "SinceQuery",
    "UntilQuery",
    "page_args",
    "provide_scope",
    "redact_error",
    "time_range",
]
