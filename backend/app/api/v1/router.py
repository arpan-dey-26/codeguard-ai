"""Versioned dashboard API.

Empty of resources in Phase 0 by design — repositories, pull requests, review
runs, findings and analytics arrive in Phase 4 (architecture.md §17.5).

The ``/api/v1`` prefix exists from the first commit for one reason: the SPA is
deployed independently of the API, so without a version prefix any breaking
change requires a lockstep deploy of both.  The prefix costs nothing now and
buys the ability to run two versions during a transition.

Phase 1 adds ``POST /api/v1/webhooks/github`` — the GitHub ingress endpoint.
It authenticates by HMAC signature rather than by session, which is why it is
mounted explicitly rather than sharing the dashboard's dependency chain.

Phase 4 added the read-only dashboard resources and Phase 5 authenticates
them. Every dashboard route depends on ``ScopeDep``, which now resolves a real
server-side session and scopes the query to the installations in the caller's
``user_installation_access``. A route that forgot to authenticate would have no
``ViewerScope`` to give its query, so the requirement is structural rather than
a convention each handler has to remember.

``GET /api/v1/meta`` returns no user data and requires no authentication.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import SettingsDep
from app.api.v1.auth import router as auth_router
from app.api.v1.dashboard import ROUTERS as DASHBOARD_ROUTERS
from app.api.v1.webhooks import router as webhooks_router

router = APIRouter(prefix="/api/v1")

# The webhook receiver authenticates by HMAC signature rather than by session,
# which is why it is mounted separately from the dashboard routers below.
router.include_router(webhooks_router)

# Phase 5: the sign-in flow. Unauthenticated by necessity — these are the
# endpoints that *create* a session — and protected instead by a single-use
# ``state`` value checked in two independent places.
router.include_router(auth_router)

# Phase 4: the read-only dashboard API. Each of these routers resolves a
# ``ViewerScope`` per request, and ``app.api.scope`` refuses to produce one
# outside development unless the operator has explicitly opted in — so this
# block fails closed rather than exposing review data by default.
for _dashboard_router in DASHBOARD_ROUTERS:
    router.include_router(_dashboard_router)


class ApiMeta(BaseModel):
    """Non-sensitive API metadata."""

    api_version: str
    app_version: str
    environment: str


@router.get("/meta", response_model=ApiMeta, tags=["meta"], summary="API metadata")
async def meta(settings: SettingsDep) -> ApiMeta:
    return ApiMeta(
        api_version="v1",
        app_version=settings.app_version,
        environment=settings.app_env.value,
    )
