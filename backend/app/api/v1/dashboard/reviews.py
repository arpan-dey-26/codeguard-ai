"""Pull-request and review-run endpoints (architecture.md A5–A8).

The distinction these routes exist to preserve: a **pull request** is a thing a
developer opened, and a **review run** is one attempt to review one commit of
it. Three attempts at one pull request are three runs and one pull request, and
no response here blurs the two.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi import Query as QueryParam

from app.api.deps import SessionDep, SettingsDep
from app.api.filters import (
    PR_STATES,
    RUN_STATUSES,
    PullRequestFilters,
    RunFilters,
    many_of,
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
    redact_error,
    time_range,
)
from app.db import read
from app.db.repositories import dashboard as queries
from app.schemas.dashboard import (
    ChangedFileSummary,
    FunnelCounts,
    Page,
    PullRequestDetailResponse,
    PullRequestSummary,
    ReviewRunDetailResponse,
    ReviewRunSummary,
    as_int,
)

router = APIRouter(tags=["reviews"])


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------


@router.get("/pull-requests", response_model=Page[PullRequestSummary], summary="Pull-request list")
async def list_pull_requests(
    session: SessionDep,
    scope: ScopeDep,
    settings: SettingsDep,
    repository_id: int | None = QueryParam(default=None, ge=1),
    state: list[str] | None = QueryParam(default=None),
    run_status: list[str] | None = QueryParam(default=None),
    q: str | None = QueryParam(default=None),
    since: SinceQuery = None,
    until: UntilQuery = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[PullRequestSummary]:
    page_size, position = page_args(limit, cursor, settings)
    filters = PullRequestFilters(
        repository_id=repository_id,
        states=many_of(state, PR_STATES, field_name="state"),
        run_statuses=many_of(run_status, RUN_STATUSES, field_name="run_status"),
        search=search_text(q),
        time_range=time_range(since, until) if (since or until) else None,
    )
    rows = await read.fetch_all(
        session,
        queries.pull_requests_page(scope=scope, filters=filters, cursor=position, limit=page_size),
    )
    page = build_page(
        rows,
        limit=page_size,
        cursor_for=lambda row: {"updated_at": row["updated_at"], "id": row["id"]},
    )
    return Page[PullRequestSummary](
        items=[_pr_summary(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/pull-requests/{pull_request_id}",
    response_model=PullRequestDetailResponse,
    summary="Pull request with every review attempt",
)
async def get_pull_request(
    session: SessionDep, scope: ScopeDep, pull_request_id: int
) -> PullRequestDetailResponse:
    row = await read.fetch_one(
        session, queries.pull_request_detail(scope=scope, pull_request_id=pull_request_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found.")

    # Every attempt, including superseded ones. The most informative thing
    # about a pull request is often that the first run found four issues and
    # the one after the fix found none.
    run_rows = await read.fetch_all(
        session, queries.pull_request_runs(pull_request_id=pull_request_id)
    )

    return PullRequestDetailResponse(
        id=as_int(row["id"]),
        number=as_int(row["number"]),
        title=row["title"],
        author_login=row["author_login"],
        state=row["state"],
        is_draft=bool(row["is_draft"]),
        head_sha=row["head_sha"],
        base_sha=row["base_sha"],
        base_ref=row["base_ref"],
        head_ref=row["head_ref"],
        html_url=row["html_url"],
        changed_files_count=as_int(row["changed_files_count"]),
        changed_files_truncated=bool(row["changed_files_truncated"]),
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        merged_at=row["merged_at"],
        updated_at=row["updated_at"],
        last_synced_at=row["last_synced_at"],
        repository_id=as_int(row["repository_id"]),
        repository_full_name=row["repository_full_name"],
        default_branch=row["default_branch"],
        repository_active=bool(row["repository_active"]),
        current_run_id=str(row["current_run_id"]) if row["current_run_id"] else None,
        runs=[_run_summary(r) for r in run_rows],
    )


# ---------------------------------------------------------------------------
# Review runs
# ---------------------------------------------------------------------------


@router.get("/review-runs", response_model=Page[ReviewRunSummary], summary="Review-run list")
async def list_review_runs(
    session: SessionDep,
    scope: ScopeDep,
    settings: SettingsDep,
    repository_id: int | None = QueryParam(default=None, ge=1),
    pull_request_id: int | None = QueryParam(default=None, ge=1),
    run_status: list[str] | None = QueryParam(default=None),
    include_superseded: bool = QueryParam(default=True),
    since: SinceQuery = None,
    until: UntilQuery = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[ReviewRunSummary]:
    page_size, position = page_args(limit, cursor, settings)
    filters = RunFilters(
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        statuses=many_of(run_status, RUN_STATUSES, field_name="run_status"),
        time_range=time_range(since, until) if (since or until) else None,
        include_superseded=include_superseded,
    )
    rows = await read.fetch_all(
        session,
        queries.review_runs_page(scope=scope, filters=filters, cursor=position, limit=page_size),
    )
    page = build_page(
        rows,
        limit=page_size,
        cursor_for=lambda row: {"created_at": row["created_at"], "id": str(row["id"])},
    )
    return Page[ReviewRunSummary](
        items=[_run_summary(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/review-runs/{review_run_id}",
    response_model=ReviewRunDetailResponse,
    summary="Review detail, including the validation funnel",
)
async def get_review_run(
    session: SessionDep, scope: ScopeDep, review_run_id: str
) -> ReviewRunDetailResponse:
    row = await read.fetch_one(
        session, queries.review_run_detail(scope=scope, review_run_id=review_run_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review run not found.")

    files = await read.fetch_all(
        session, queries.review_run_changed_files(review_run_id=review_run_id)
    )

    return ReviewRunDetailResponse(
        id=str(row["id"]),
        status=row["status"],
        trigger=row["trigger"],
        attempt=as_int(row["attempt"]),
        head_sha=row["head_sha"],
        base_sha=row["base_sha"],
        is_stale=bool(row["is_stale"]),
        error_code=row["error_code"],
        error_detail=redact_error(row["error_message"]),
        provider=row["provider"],
        model_name=row["model_name"],
        prompt_version=row["prompt_version"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        ai_latency_ms=row["ai_latency_ms"],
        model_output_truncated=bool(row["ai_truncated"]),
        limited_coverage=bool(row["context_truncated"]),
        files_changed=as_int(row["files_changed"]),
        files_analysed=as_int(row["files_analysed"]),
        files_skipped=as_int(row["files_skipped"]),
        funnel=FunnelCounts(
            claims_emitted=as_int(row["findings_emitted"]),
            claims_dropped=as_int(row["findings_dropped"]),
            validated_findings=as_int(row["findings_inline"]) + as_int(row["findings_summary"]),
            rejected_claims=as_int(row["findings_dashboard_only"]),
            published_comments=as_int(row["published_comments"]),
            findings_demoted=as_int(row["findings_summary"]),
        ),
        findings_persisted=as_int(row["findings_persisted"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
        created_at=row["created_at"],
        github_review_id=row["github_review_id"],
        pull_request_id=as_int(row["pull_request_id"]),
        pull_request_number=as_int(row["pull_request_number"]),
        pull_request_title=row["title"],
        pull_request_url=row["html_url"],
        repository_id=as_int(row["repository_id"]),
        repository_full_name=row["repository_full_name"],
        changed_files=[
            ChangedFileSummary(
                id=as_int(f["id"]),
                file_path=f["file_path"],
                previous_path=f["previous_path"],
                change_status=f["change_status"],
                additions=as_int(f["additions"]),
                deletions=as_int(f["deletions"]),
                changes=as_int(f["changes"]),
                has_patch=bool(f["has_patch"]),
                language=f["language"],
                is_analysed=bool(f["is_analysed"]),
                skip_reason=f["skip_reason"],
                parse_ok=f["parse_ok"],
                declarations_touched=f["declarations_touched"],
                validated_findings=as_int(f["validated_findings"]),
            )
            for f in files
        ],
    )


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _pr_summary(row: dict) -> PullRequestSummary:
    return PullRequestSummary(
        id=as_int(row["id"]),
        number=as_int(row["number"]),
        title=row["title"],
        author_login=row["author_login"],
        state=row["state"],
        is_draft=bool(row["is_draft"]),
        head_sha=row["head_sha"],
        html_url=row["html_url"],
        updated_at=row["updated_at"],
        repository_id=as_int(row["repository_id"]),
        repository_full_name=row["repository_full_name"],
        changed_files_count=as_int(row["changed_files_count"]),
        changed_files_truncated=bool(row["changed_files_truncated"]),
        current_run_id=str(row["current_run_id"]) if row["current_run_id"] else None,
        current_run_status=row["current_run_status"],
        current_run_attempt=row["current_run_attempt"],
        current_run_finished_at=row["current_run_finished_at"],
        current_run_duration_ms=row["current_run_duration_ms"],
        current_run_limited_coverage=bool(row["current_run_limited_coverage"]),
        review_run_count=as_int(row["review_run_count"]),
        validated_findings=as_int(row["validated_findings"]),
        rejected_claims=as_int(row["rejected_claims"]),
        published_comments=as_int(row["published_comments"]),
        open_critical_high=as_int(row["open_critical_high"]),
    )


def _run_summary(row: dict) -> ReviewRunSummary:
    return ReviewRunSummary(
        id=str(row["id"]),
        status=row["status"],
        trigger=row["trigger"],
        attempt=as_int(row["attempt"]),
        head_sha=row["head_sha"],
        is_stale=bool(row.get("is_stale")),
        error_code=row.get("error_code"),
        limited_coverage=bool(row.get("context_truncated")),
        model_output_truncated=bool(row.get("ai_truncated")),
        claims_emitted=as_int(row.get("findings_emitted")),
        claims_dropped=as_int(row.get("findings_dropped")),
        findings_inline=as_int(row.get("findings_inline")),
        findings_summary=as_int(row.get("findings_summary")),
        rejected_claims=as_int(row.get("findings_dashboard_only")),
        published_comments=as_int(row.get("published_comments")),
        files_changed=as_int(row.get("files_changed")),
        files_analysed=as_int(row.get("files_analysed")),
        files_skipped=as_int(row.get("files_skipped")),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        duration_ms=row.get("duration_ms"),
        created_at=row.get("created_at"),
        pull_request_id=row.get("pull_request_id"),
        pull_request_number=row.get("pull_request_number"),
        title=row.get("title"),
        repository_id=row.get("repository_id"),
        repository_full_name=row.get("repository_full_name"),
    )
