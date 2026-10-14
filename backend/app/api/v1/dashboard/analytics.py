"""Overview and analytics endpoints (architecture.md A1, A14, A15, A18).

Every number here is a SQL aggregate over the whole filtered population, not a
sum over a page. That distinction is the entire reason these endpoints exist
separately from the list endpoints: a "total findings" computed in the browser
from twenty-five rows is a lie that looks like a feature.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Query as QueryParam

from app.api.deps import SessionDep, SettingsDep
from app.api.filters import parse_bucket
from app.api.v1.dashboard.common import ScopeDep, SinceQuery, UntilQuery, time_range
from app.db import read
from app.db.repositories import dashboard as queries
from app.schemas.dashboard import (
    AnchorCounts,
    CountBucket,
    CoverageResponse,
    FunnelCounts,
    OverviewResponse,
    ProcessingHealthResponse,
    RecentActivityEntry,
    RunCounts,
    TrendPoint,
    TrendResponse,
    as_int,
)

router = APIRouter(tags=["analytics"])


@router.get("/overview", response_model=OverviewResponse, summary="Dashboard KPI tiles")
async def overview(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
    repository_id: int | None = QueryParam(default=None, ge=1),
) -> OverviewResponse:
    window = time_range(since, until)
    row = (
        await read.fetch_one(
            session,
            queries.overview_summary(scope=scope, time_range=window, repository_id=repository_id),
        )
        or {}
    )

    return OverviewResponse(
        range_from=window.start,
        range_to=window.end,
        repositories_visible=as_int(row.get("repositories_visible")),
        repositories_active=as_int(row.get("repositories_active")),
        pull_requests_reviewed=as_int(row.get("pull_requests_reviewed")),
        runs=RunCounts(
            total=as_int(row.get("review_runs_total")),
            succeeded=as_int(row.get("runs_succeeded")),
            partial_success=as_int(row.get("runs_partial_success")),
            failed=as_int(row.get("runs_failed")),
            superseded=as_int(row.get("runs_superseded")),
            in_flight=as_int(row.get("runs_in_flight")),
            limited_coverage=as_int(row.get("runs_limited_coverage")),
        ),
        funnel=FunnelCounts(
            claims_emitted=as_int(row.get("claims_emitted")),
            claims_dropped=as_int(row.get("claims_dropped")),
            validated_findings=as_int(row.get("validated_findings")),
            rejected_claims=as_int(row.get("rejected_claims")),
            published_comments=as_int(row.get("published_comments")),
            findings_demoted=as_int(row.get("findings_demoted")),
        ),
        open_findings=as_int(row.get("open_findings")),
        open_high_severity=as_int(row.get("open_high_severity")),
        anchors=AnchorCounts(
            exact=as_int(row.get("anchors_exact")),
            relocated=as_int(row.get("anchors_relocated")),
        ),
        duration_p50_ms=_ms(row.get("duration_p50_ms")),
        duration_p95_ms=_ms(row.get("duration_p95_ms")),
    )


@router.get(
    "/analytics/findings-by-severity",
    response_model=list[CountBucket],
    summary="Validated findings grouped by severity",
)
async def findings_by_severity(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
    repository_id: int | None = QueryParam(default=None, ge=1),
) -> list[CountBucket]:
    window = time_range(since, until)
    rows = await read.fetch_all(
        session,
        queries.findings_by_severity(scope=scope, time_range=window, repository_id=repository_id),
    )
    counts = read.counts_by_key(rows, key="severity")
    # Returned in severity order with explicit zeros, so the chart's categories
    # are stable between requests. A grouped query only returns the keys that
    # have rows, and a bar chart whose categories appear and disappear between
    # refreshes is unreadable.
    return [
        CountBucket(key=name, count=counts.get(name, 0))
        for name in ("critical", "high", "medium", "low", "info")
    ]


@router.get(
    "/analytics/findings-by-category",
    response_model=list[CountBucket],
    summary="Validated findings grouped by category",
)
async def findings_by_category(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
    repository_id: int | None = QueryParam(default=None, ge=1),
) -> list[CountBucket]:
    window = time_range(since, until)
    rows = await read.fetch_all(
        session,
        queries.findings_by_category(scope=scope, time_range=window, repository_id=repository_id),
    )
    counts = read.counts_by_key(rows, key="category")
    return [
        CountBucket(key=name, count=counts.get(name, 0))
        for name in ("security", "bug", "resource", "performance", "maintainability")
    ]


@router.get(
    "/analytics/rejection-reasons",
    response_model=list[CountBucket],
    summary="Why structural validation rejected model claims",
)
async def rejection_reasons(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
    repository_id: int | None = QueryParam(default=None, ge=1),
) -> list[CountBucket]:
    """Rejection reasons, by validation gate.

    These are **structural** rejections. A claim counted here failed a gate
    such as "the line is not in this diff" or "the quoted source is not at that
    line". It is not evidence that the issue the model described is unreal, and
    the endpoint is named for the gate rather than for a verdict so that no
    consumer can honestly relabel it as a false-positive rate.
    """
    window = time_range(since, until)
    rows = await read.fetch_all(
        session,
        queries.rejection_reasons(scope=scope, time_range=window, repository_id=repository_id),
    )
    return [
        CountBucket(key=str(row["rejection_reason"]), count=as_int(row["count"])) for row in rows
    ]


@router.get(
    "/analytics/anchor-distribution",
    response_model=list[CountBucket],
    summary="Claimed line versus resolved line, in buckets",
)
async def anchor_distribution(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
    repository_id: int | None = QueryParam(default=None, ge=1),
) -> list[CountBucket]:
    """How far the model's claimed line sat from the line we resolved.

    Buckets, not a score. This measures the claimed-versus-resolved
    relationship **under the relocation policy in ADR-0011** — a property of
    this system's validator, not a measurement of the model's accuracy, and
    never to be presented as one.
    """
    window = time_range(since, until)
    rows = await read.fetch_all(
        session,
        queries.anchor_distribution(scope=scope, time_range=window, repository_id=repository_id),
    )
    counts = read.counts_by_key(rows, key="bucket")
    return [
        CountBucket(key=name, count=counts.get(name, 0))
        for name in ("exact", "off_by_1", "off_by_2", "off_by_3_plus", "unresolved")
    ]


@router.get("/analytics/trends", response_model=TrendResponse, summary="Review activity over time")
async def trends(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
    bucket: str | None = QueryParam(default="day"),
    repository_id: int | None = QueryParam(default=None, ge=1),
) -> TrendResponse:
    """Gap-filled time series.

    The range is **snapped to bucket boundaries** before querying, so every
    point covers the same span and the first column is never a partial day
    presented as a whole one. Buckets are UTC — stated in the response rather
    than left for the client to guess, because a chart bucketed in one zone and
    labelled in another is wrong in a way nobody notices.
    """
    width = parse_bucket(bucket)
    window = time_range(since, until).snapped(width)
    rows = await read.fetch_all(
        session,
        queries.review_trends(
            scope=scope, time_range=window, bucket=width, repository_id=repository_id
        ),
    )
    return TrendResponse(
        bucket=width,
        range_from=window.start,
        range_to=window.end,
        timezone="UTC",
        points=[
            TrendPoint(
                bucket_start=row["bucket_start"],
                runs_total=as_int(row["runs_total"]),
                runs_succeeded=as_int(row["runs_succeeded"]),
                runs_partial=as_int(row["runs_partial"]),
                runs_failed=as_int(row["runs_failed"]),
                runs_superseded=as_int(row["runs_superseded"]),
                pull_requests=as_int(row["pull_requests"]),
                validated_findings=as_int(row["validated_findings"]),
                rejected_claims=as_int(row["rejected_claims"]),
            )
            for row in rows
        ],
    )


@router.get(
    "/analytics/processing-health",
    response_model=ProcessingHealthResponse,
    summary="Run reliability",
)
async def processing_health(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
) -> ProcessingHealthResponse:
    window = time_range(since, until)
    row = (
        await read.fetch_one(session, queries.processing_health(scope=scope, time_range=window))
        or {}
    )
    errors = await read.fetch_all(
        session, queries.run_error_breakdown(scope=scope, time_range=window)
    )
    return ProcessingHealthResponse(
        range_from=window.start,
        range_to=window.end,
        runs_total=as_int(row.get("runs_total")),
        runs_terminal=as_int(row.get("runs_terminal")),
        succeeded=as_int(row.get("succeeded")),
        partial_success=as_int(row.get("partial_success")),
        failed=as_int(row.get("failed")),
        superseded=as_int(row.get("superseded")),
        skipped=as_int(row.get("skipped")),
        in_flight=as_int(row.get("in_flight")),
        limited_coverage=as_int(row.get("limited_coverage")),
        model_output_truncated=as_int(row.get("model_output_truncated")),
        duration_p50_ms=_ms(row.get("duration_p50_ms")),
        duration_p95_ms=_ms(row.get("duration_p95_ms")),
        error_breakdown=[
            CountBucket(key=str(e["error_code"]), count=as_int(e["count"])) for e in errors
        ],
    )


@router.get("/analytics/coverage", response_model=CoverageResponse, summary="Review coverage")
async def coverage(
    session: SessionDep,
    scope: ScopeDep,
    since: SinceQuery = None,
    until: UntilQuery = None,
    repository_id: int | None = QueryParam(default=None, ge=1),
) -> CoverageResponse:
    window = time_range(since, until)
    row = (
        await read.fetch_one(
            session,
            queries.coverage_summary(scope=scope, time_range=window, repository_id=repository_id),
        )
        or {}
    )
    skips = await read.fetch_all(
        session,
        queries.skip_reason_breakdown(scope=scope, time_range=window, repository_id=repository_id),
    )
    return CoverageResponse(
        range_from=window.start,
        range_to=window.end,
        files_changed=as_int(row.get("files_changed")),
        files_analysed=as_int(row.get("files_analysed")),
        files_skipped=as_int(row.get("files_skipped")),
        runs_over_budget=as_int(row.get("runs_over_budget")),
        runs_total=as_int(row.get("runs_total")),
        skip_reasons=[
            CountBucket(key=str(s["skip_reason"]), count=as_int(s["count"])) for s in skips
        ],
    )


@router.get(
    "/analytics/recent-activity",
    response_model=list[RecentActivityEntry],
    summary="The most recent review attempts",
)
async def recent_activity(
    session: SessionDep,
    scope: ScopeDep,
    settings: SettingsDep,
    limit: int = QueryParam(default=10, ge=1, le=50),
) -> list[RecentActivityEntry]:
    rows = await read.fetch_all(session, queries.recent_activity(scope=scope, limit=limit))
    return [
        RecentActivityEntry(
            id=str(row["id"]),
            status=row["status"],
            attempt=as_int(row["attempt"]),
            head_sha=row["head_sha"],
            is_stale=bool(row["is_stale"]),
            limited_coverage=bool(row["context_truncated"]),
            error_code=row["error_code"],
            findings_inline=as_int(row["findings_inline"]),
            rejected_claims=as_int(row["findings_dashboard_only"]),
            duration_ms=row["duration_ms"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
            pull_request_id=as_int(row["pull_request_id"]),
            pull_request_number=as_int(row["pull_request_number"]),
            title=row["title"],
            repository_id=as_int(row["repository_id"]),
            repository_full_name=row["repository_full_name"],
        )
        for row in rows
    ]


def _ms(value: object) -> int | None:
    """``percentile_cont`` returns a float; the API reports whole milliseconds."""
    return None if value is None else int(float(value))  # type: ignore[arg-type]
