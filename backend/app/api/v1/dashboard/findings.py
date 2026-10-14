"""Findings endpoints (architecture.md A11, A12).

The workhorse screen's data source, and the one place where careless wording
would do the most damage. Two rules run through every response here:

**A rejected claim is not a false positive.** It is a claim that failed a
structural gate. The field is called ``validation`` with values ``validated``
and ``rejected``, and the reason is the gate's own name.

**A validated finding is not a proven defect.** It means the claim is
structurally anchored to the reviewed source at the reviewed commit. Whether
the issue is real is a question this system does not answer.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi import Query as QueryParam

from app.api.deps import SessionDep, SettingsDep
from app.api.filters import (
    CATEGORIES,
    FINDING_STATUSES,
    MAPPING_STATUSES,
    PUBLICATION_STATES,
    SEVERITIES,
    VALIDATION_RESULTS,
    FindingFilters,
    many_of,
    one_of,
    search_text,
)
from app.api.pagination import build_page
from app.api.v1.dashboard.common import (
    CursorQuery,
    LimitQuery,
    ScopeDep,
    SinceQuery,
    UntilQuery,
    page_args,
    time_range,
)
from app.db import read
from app.db.repositories import dashboard as queries
from app.schemas.dashboard import (
    FindingDetailResponse,
    FindingEventEntry,
    FindingSummary,
    Page,
    anchor_context,
    as_float,
    as_int,
    validation_of,
)

router = APIRouter(prefix="/findings", tags=["findings"])

REJECTION_REASONS = (
    "malformed",
    "secret_in_text",
    "unsafe_path",
    "unknown_file",
    "sha_mismatch",
    "wrong_side",
    "line_out_of_range",
    "line_not_in_diff",
    "line_not_presented",
    "no_source_line",
    "anchor_too_short",
    "anchor_not_found",
    "anchor_ambiguous",
    "low_confidence",
    "duplicate",
    "over_limit",
)


@router.get("", response_model=Page[FindingSummary], summary="Findings list")
async def list_findings(
    session: SessionDep,
    scope: ScopeDep,
    settings: SettingsDep,
    repository_id: int | None = QueryParam(default=None, ge=1),
    pull_request_id: int | None = QueryParam(default=None, ge=1),
    review_run_id: str | None = QueryParam(default=None),
    severity: list[str] | None = QueryParam(default=None),
    category: list[str] | None = QueryParam(default=None),
    finding_status: list[str] | None = QueryParam(default=None, alias="status"),
    mapping_status: list[str] | None = QueryParam(default=None),
    validation: str | None = QueryParam(default=None),
    publication: str | None = QueryParam(default=None),
    rejection_reason: str | None = QueryParam(default=None),
    q: str | None = QueryParam(default=None),
    since: SinceQuery = None,
    until: UntilQuery = None,
    include_total: bool = QueryParam(
        default=False,
        description="Also run an exact COUNT for this filter set. Off by default.",
    ),
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[FindingSummary]:
    page_size, position = page_args(limit, cursor, settings)
    filters = FindingFilters(
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        review_run_id=_uuid_or_none(review_run_id),
        severities=many_of(severity, SEVERITIES, field_name="severity"),
        categories=many_of(category, CATEGORIES, field_name="category"),
        statuses=many_of(finding_status, FINDING_STATUSES, field_name="status"),
        mapping_statuses=many_of(mapping_status, MAPPING_STATUSES, field_name="mapping_status"),
        validation=one_of(validation, VALIDATION_RESULTS, field_name="validation"),
        publication=one_of(publication, PUBLICATION_STATES, field_name="publication"),
        rejection_reason=one_of(rejection_reason, REJECTION_REASONS, field_name="rejection_reason"),
        search=search_text(q),
        time_range=time_range(since, until) if (since or until) else None,
    )

    rows = await read.fetch_all(
        session,
        queries.findings_page(scope=scope, filters=filters, cursor=position, limit=page_size),
    )
    total = None
    if include_total:
        # Opt-in: an exact COUNT over a filtered findings table is its own scan,
        # and paying for it on every scroll is how a list screen becomes the
        # slowest thing in the product.
        total = as_int(
            await read.fetch_scalar(session, queries.findings_count(scope=scope, filters=filters))
        )

    page = build_page(
        rows,
        limit=page_size,
        cursor_for=lambda row: {"created_at": row["created_at"], "id": str(row["id"])},
        total=total,
    )
    return Page[FindingSummary](
        items=[_summary(row) for row in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.get("/{finding_id}", response_model=FindingDetailResponse, summary="Finding detail")
async def get_finding(
    session: SessionDep, scope: ScopeDep, finding_id: str
) -> FindingDetailResponse:
    row = await read.fetch_one(
        session, queries.finding_detail(scope=scope, finding_id=_uuid_or_404(finding_id))
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found.")

    events = await read.fetch_all(session, queries.finding_events(finding_id=str(row["id"])))

    return FindingDetailResponse(
        id=str(row["id"]),
        severity=row["severity"],
        category=row["category"],
        title=row["title"],
        description=row["description"],
        impact=row["impact"] or "",
        suggested_fix=row["suggested_fix"],
        rationale=row["rationale"],
        cwe_id=row["cwe_id"],
        status=row["status"],
        mapping_status=row["mapping_status"],
        validation=validation_of(row["rejection_reason"]),
        rejection_reason=row["rejection_reason"],
        file_path=row["file_path"],
        claimed_line=as_int(row["claimed_line_start"]),
        claimed_line_end=row["claimed_line_end"],
        resolved_line=row["resolved_line"],
        anchor_offset=row["anchor_offset"],
        anchor_snippet=row["anchor_snippet"] or "",
        anchor_verified=bool(row["anchor_verified"]),
        side=row["side"],
        qualified_name=row["qualified_name"] or "",
        declaration_id=row["declaration_id"],
        fingerprint=row["fingerprint"],
        model_confidence=as_float(row["model_confidence"]),
        effective_confidence=as_float(row["effective_confidence"]),
        model_finding_id=row["model_finding_id"] or "",
        model_name=row["model_name"],
        prompt_version=row["prompt_version"],
        head_sha=row["head_sha"],
        is_stale=bool(row["is_stale"]),
        review_run_id=str(row["review_run_id"]),
        review_run_status=row["review_run_status"],
        review_run_attempt=as_int(row["attempt"]),
        repository_id=as_int(row["repository_id"]),
        repository_full_name=row["repository_full_name"],
        pull_request_id=as_int(row["pull_request_id"]),
        pull_request_number=as_int(row["pull_request_number"]),
        pull_request_url=row["pull_request_url"],
        is_published=row["github_comment_id"] is not None or row["posted_at"] is not None,
        published_comment_url=row["comment_url"],
        published_at=row["posted_at"],
        published_line=row["comment_line"],
        published_side=row["comment_side"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        events=[
            FindingEventEntry(
                id=as_int(e["id"]),
                from_status=e["from_status"],
                to_status=e["to_status"],
                source=e["source"],
                note=e["note"],
                created_at=e["created_at"],
            )
            for e in events
        ],
        source_context=anchor_context(
            anchor_snippet=row["anchor_snippet"], resolved_line=row["resolved_line"]
        ),
    )


def _summary(row: dict) -> FindingSummary:
    return FindingSummary(
        id=str(row["id"]),
        severity=row["severity"],
        category=row["category"],
        title=row["title"],
        status=row["status"],
        mapping_status=row["mapping_status"],
        validation=validation_of(row["rejection_reason"]),
        rejection_reason=row["rejection_reason"],
        file_path=row["file_path"],
        claimed_line=as_int(row["claimed_line_start"]),
        resolved_line=row["resolved_line"],
        anchor_offset=row["anchor_offset"],
        anchor_verified=bool(row["anchor_verified"]),
        side=row["side"],
        qualified_name=row["qualified_name"] or "",
        model_confidence=as_float(row["model_confidence"]),
        effective_confidence=as_float(row["effective_confidence"]),
        cwe_id=row["cwe_id"],
        is_published=bool(row["is_published"]),
        is_stale=bool(row["is_stale"]),
        created_at=row["created_at"],
        review_run_id=str(row["review_run_id"]),
        pull_request_id=as_int(row["pull_request_id"]),
        pull_request_number=as_int(row["pull_request_number"]),
        repository_id=as_int(row["repository_id"]),
        repository_full_name=row["repository_full_name"],
        head_sha=row["head_sha"],
    )


def _uuid_or_none(value: str | None) -> str | None:
    """Validate a UUID before it reaches a ``::uuid`` cast.

    Without this, a malformed id becomes a PostgreSQL cast error — a 500 for
    what is a client mistake, with a database message in the log for every
    passing scanner.
    """
    import uuid as _uuid

    if value is None or not value.strip():
        return None
    try:
        return str(_uuid.UUID(value.strip()))
    except ValueError as exc:
        from app.api.filters import InvalidFilterError

        raise InvalidFilterError("review_run_id must be a UUID.") from exc


def _uuid_or_404(value: str) -> str:
    import uuid as _uuid

    try:
        return str(_uuid.UUID(value.strip()))
    except ValueError as exc:
        # A path segment that is not a UUID cannot name a finding, so this is
        # "not found" rather than a validation failure — and it keeps the id
        # space from being probeable through differing error codes.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found."
        ) from exc
