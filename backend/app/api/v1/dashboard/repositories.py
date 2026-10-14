"""Repository endpoints (architecture.md A2, A3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi import Query as QueryParam

from app.api.deps import SessionDep, SettingsDep
from app.api.filters import RepositoryFilters, search_text
from app.api.pagination import build_page
from app.api.v1.dashboard.common import CursorQuery, LimitQuery, ScopeDep, page_args
from app.db import read
from app.db.repositories import dashboard as queries
from app.schemas.dashboard import (
    Page,
    RepositoryDetailResponse,
    RepositorySummary,
    as_int,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=Page[RepositorySummary], summary="Repository list")
async def list_repositories(
    session: SessionDep,
    scope: ScopeDep,
    settings: SettingsDep,
    q: str | None = QueryParam(default=None, description="Substring of the full name."),
    is_active: bool | None = QueryParam(default=None),
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[RepositorySummary]:
    page_size, position = page_args(limit, cursor, settings)
    filters = RepositoryFilters(search=search_text(q), is_active=is_active)
    rows = await read.fetch_all(
        session,
        queries.repositories_page(scope=scope, filters=filters, cursor=position, limit=page_size),
    )
    page = build_page(
        rows,
        limit=page_size,
        # The cursor carries the exact sort key — name and id — so resuming is
        # a range scan on the same index the ORDER BY uses.
        cursor_for=lambda row: {"full_name": row["full_name"], "id": row["id"]},
    )
    return Page[RepositorySummary](
        items=[_summary(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{repository_id}",
    response_model=RepositoryDetailResponse,
    summary="Repository detail",
)
async def get_repository(
    session: SessionDep, scope: ScopeDep, repository_id: int
) -> RepositoryDetailResponse:
    row = await read.fetch_one(
        session, queries.repository_detail(scope=scope, repository_id=repository_id)
    )
    if row is None:
        # 404 for "does not exist" *and* for "exists but is outside your
        # scope". A 403 would confirm that some other account has a repository
        # with this id (architecture.md §17.7).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")

    return RepositoryDetailResponse(
        id=as_int(row["id"]),
        full_name=row["full_name"],
        owner_login=row["owner_login"],
        name=row["name"],
        default_branch=row["default_branch"],
        is_private=bool(row["is_private"]),
        is_active=bool(row["is_active"]),
        primary_language=row["primary_language"],
        created_at=row["created_at"],
        installation_account=row["account_login"],
        installation_account_type=row["account_type"],
        installation_suspended=bool(row["installation_suspended"]),
        pull_requests_total=as_int(row["pull_requests_total"]),
        pull_requests_reviewed=as_int(row["pull_requests_reviewed"]),
        review_runs_total=as_int(row["review_runs_total"]),
        review_runs_partial=as_int(row["review_runs_partial"]),
        review_runs_failed=as_int(row["review_runs_failed"]),
        latest_review_at=row["latest_review_at"],
        validated_findings=as_int(row["validated_findings"]),
        rejected_claims=as_int(row["rejected_claims"]),
        open_findings=as_int(row["open_findings"]),
        published_comments=as_int(row["published_comments"]),
    )


def _summary(row: dict) -> RepositorySummary:
    return RepositorySummary(
        id=as_int(row["id"]),
        full_name=row["full_name"],
        owner_login=row["owner_login"],
        name=row["name"],
        default_branch=row["default_branch"],
        is_private=bool(row["is_private"]),
        is_active=bool(row["is_active"]),
        primary_language=row["primary_language"],
        latest_review_at=row["latest_review_at"],
        review_runs=as_int(row["review_runs"]),
        pull_requests_reviewed=as_int(row["pull_requests_reviewed"]),
        open_findings=as_int(row["open_findings"]),
        open_high_severity=as_int(row["open_high_severity"]),
    )
