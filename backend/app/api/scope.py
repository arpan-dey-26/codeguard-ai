"""Who is allowed to see what.

Phase 4 built this module as a seam with a fail-closed guard, because the
alternative — a placeholder session — would have been a mechanism that *looked*
like security. **Phase 5 fills the seam in.** ``resolve_scope`` now derives the
scope from a real server-side session, and the shape of everything around it is
unchanged: every query in ``app.db.repositories.dashboard`` still takes a
``ViewerScope`` and still applies it in SQL. That was the point of writing the
seam first.

Three states, and keeping them distinct is the whole design:

``ViewerScope.for_installations((1, 7))``
    A signed-in user. Sees exactly those installations.

``ViewerScope.for_installations(())``
    A signed-in user with **no** access — their App access was revoked, or they
    have never installed it. Sees *nothing*. This is a real state and it must
    return empty lists; the bug it guards against is an empty filter list being
    optimised away into no filter at all.

``ViewerScope.unrestricted()``
    No restriction. Reachable **only** in development, or when an operator has
    explicitly set ``DASHBOARD_ALLOW_UNAUTHENTICATED_READS``. Outside those,
    the absence of a session is a 401 and never an unrestricted scope.

The ordering in ``resolve_scope`` matters and is the security property: a
session is consulted **first**, and the unauthenticated fallback is reached only
when there is no session *and* the deployment has opted into that. A
misconfigured production deployment gets an error, not everybody's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.core.errors import AuthenticationRequiredError, CodeGuardError

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


class _HasInstallationAccess(Protocol):
    """What this module needs from a session, and nothing more.

    A structural type rather than an import of ``SessionContext``. Importing
    the concrete class would drag ``app.db.repositories.auth`` — and therefore
    SQLAlchemy — into every consumer of ``ViewerScope``, including the pure
    query builders and the tests that exercise them without a database. The
    dependency would point the wrong way: the authorization boundary should not
    know which persistence library stores sessions.
    """

    @property
    def installation_ids(self) -> tuple[int, ...]: ...

    @property
    def user_id(self) -> int: ...


_HTTP_UNAVAILABLE = 503


class DashboardAccessNotConfiguredError(CodeGuardError):
    """The deployment can neither authenticate nor serve openly.

    Rendered as 503, not 403: nothing is wrong with the *request*. Reachable
    only when authentication is unconfigured — from Phase 5 that means a
    development or test environment with no OAuth credentials — so it says the
    deployment is incomplete rather than implying the caller lacks a permission
    nobody has.
    """

    problem_type = "dashboard-access-not-configured"
    title = "Dashboard API is not available in this deployment"
    status_code = _HTTP_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class ViewerScope:
    """The set of installations a caller may read.

    ``installation_ids is None`` means "no restriction". That is a distinct
    state from an empty tuple, which means "this caller can see nothing" — a
    real outcome for a signed-in user whose App access was revoked, and one
    that must return empty lists rather than everything.
    """

    installation_ids: tuple[int, ...] | None = None
    user_id: int | None = None

    @staticmethod
    def unrestricted() -> ViewerScope:
        return ViewerScope(installation_ids=None)

    @staticmethod
    def for_installations(
        installation_ids: tuple[int, ...], *, user_id: int | None = None
    ) -> ViewerScope:
        return ViewerScope(installation_ids=tuple(sorted(set(installation_ids))), user_id=user_id)

    @staticmethod
    def for_session(context: _HasInstallationAccess) -> ViewerScope:
        """Build the scope a session grants. The only authenticated path in."""
        return ViewerScope.for_installations(context.installation_ids, user_id=context.user_id)

    @property
    def is_unrestricted(self) -> bool:
        return self.installation_ids is None

    @property
    def sees_nothing(self) -> bool:
        return self.installation_ids is not None and len(self.installation_ids) == 0

    def describe(self) -> dict[str, object]:
        return {
            "unrestricted": self.is_unrestricted,
            "installation_count": (
                None if self.is_unrestricted else len(self.installation_ids or ())
            ),
            "user_id": self.user_id,
        }


def resolve_scope(settings: object, context: _HasInstallationAccess | None = None) -> ViewerScope:
    """Build the scope for this request.

    A session always wins. The unauthenticated branch below is reached only
    when there is no session, and it refuses unless the deployment has said in
    so many words that it wants to serve without authentication.
    """
    if context is not None:
        return ViewerScope.for_session(context)

    if _explicitly_open(settings):
        return ViewerScope.unrestricted()

    if _auth_is_available(settings):
        # Sign-in is possible here; the caller simply has not done it. 401 is
        # the accurate answer, and it is what tells the SPA to send the user to
        # the login route rather than showing an outage page.
        raise AuthenticationRequiredError("Sign in to view this dashboard.")

    raise DashboardAccessNotConfiguredError(
        "This deployment has no sign-in configured and unauthenticated reads are not enabled."
    )


def _explicitly_open(settings: object) -> bool:
    """Whether the operator opted into serving with no authentication at all.

    Note what is *not* here any more. Until Phase 5 this also returned ``True``
    for any development or test environment, because there was no way to sign
    in. Now there is, so a development environment with OAuth configured
    authenticates like any other — and a developer who wants the old behaviour
    has to ask for it by name.
    """
    return bool(getattr(settings, "dashboard_allow_unauthenticated_reads", False))


def _auth_is_available(settings: object) -> bool:
    return bool(getattr(settings, "oauth_configured", False))


__all__ = [
    "DashboardAccessNotConfiguredError",
    "ViewerScope",
    "resolve_scope",
]
