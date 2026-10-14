"""The dashboard read API.

One router per resource group, all mounted under ``/api/v1`` by
``app.api.v1.router``. Every route in this package is **read-only**: Phase 4
adds no mutation, because the only mutations architecture.md defines for the
dashboard (`PATCH /findings/{id}`, `POST /re-review`, settings writes) are
authenticated actions attributed to a user, and this build has no users yet.
Shipping an unauthenticated write would be a worse answer than shipping none.

Every route follows the same three steps, in the same order:

1. ``resolve_scope`` — fails closed outside development (see ``app.api.scope``);
2. validate filters against an allow-list (``app.api.filters``);
3. run a query built by ``app.db.repositories.dashboard`` and map each row,
   field by field, into an explicit response model.

Nothing is aggregated in Python over a page of rows, and no row goes out the
door without passing through a schema that names every field.
"""

from app.api.v1.dashboard.analytics import router as analytics_router
from app.api.v1.dashboard.findings import router as findings_router
from app.api.v1.dashboard.repositories import router as repositories_router
from app.api.v1.dashboard.reviews import router as reviews_router
from app.api.v1.dashboard.system import router as system_router

ROUTERS = (
    analytics_router,
    repositories_router,
    reviews_router,
    findings_router,
    system_router,
)

__all__ = ["ROUTERS"]
