"""Dashboard read queries.

**Every number the dashboard shows is produced here, in SQL, by the database.**
Nothing is aggregated in Python over a paginated slice — that is the classic
way a "total" ends up describing page one — and nothing is invented.

Two structural decisions make this module what it is.

**1. Building a query is separate from running one.** Each public function
returns a ``Query`` — SQL text plus a parameter dict — and an executor runs it.
That is not indirection for its own sake: it makes every aggregate testable by
running the *exact* SQL the application would run, against a real PostgreSQL,
without needing the ORM or an ASGI server. An analytics layer whose correctness
rests on "the ORM probably emitted what I meant" is not verifiable.

**2. Values are bound; structure is chosen from constants.** A filter's *value*
always arrives as a bound parameter. A filter's *shape* — which WHERE clause,
which ORDER BY, which bucket width — is selected from literals written in this
file, keyed by an allow-listed token that ``app.api.filters`` has already
validated. There is no code path where caller text becomes SQL.

**Metric vocabulary**, used consistently throughout and documented in
``docs/dashboard-metrics.md``:

``claims_emitted``      items the model put in its array (``review_runs``).
``claims_dropped``      items the provider could not parse at all.
``validated_findings``  rows in ``findings`` with ``rejection_reason IS NULL``.
``rejected_claims``     rows in ``findings`` with a ``rejection_reason``.
``published_comments``  rows in ``review_comments`` — comments GitHub confirmed.

A rejected claim is **not** a confirmed false positive. It is a claim that
failed a structural gate. Nothing in this module may be labelled otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.api.filters import (
    FindingFilters,
    PullRequestFilters,
    RepositoryFilters,
    RunFilters,
    TimeRange,
    escape_like,
)
from app.api.scope import ViewerScope

# ---------------------------------------------------------------------------
# Query object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Query:
    """SQL plus its bound parameters. Never concatenated with caller input."""

    sql: str
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Shared fragments
# ---------------------------------------------------------------------------

# A finding is "validated" iff it carries no rejection reason. One predicate,
# written once, so a dashboard tile and a CSV export cannot disagree about what
# the word means.
VALIDATED = "f.rejection_reason IS NULL"
REJECTED = "f.rejection_reason IS NOT NULL"

PUBLISHED = "EXISTS (SELECT 1 FROM review_comments rc WHERE rc.finding_id = f.id)"

# Runs that represent completed work. `superseded` is excluded because the run
# was overtaken by a newer commit and never finished; counting it as a review
# would inflate every throughput metric with work that did not happen.
COMPLETED_RUN_STATUSES = ("succeeded", "partial_success")
TERMINAL_RUN_STATUSES = ("succeeded", "partial_success", "failed", "skipped")


def _scope_clause(scope: ViewerScope, params: dict[str, Any], *, alias: str = "r") -> str:
    """Restrict to the caller's installations.

    Returns a clause that is always safe to AND into a WHERE. An unrestricted
    scope yields ``TRUE`` rather than being omitted, so every query has the
    same shape whether or not scoping is active — a query that only *sometimes*
    has a scope clause is a query where someone eventually forgets it.
    """
    if scope.is_unrestricted:
        return "TRUE"
    if scope.sees_nothing:
        # Distinct from unrestricted: a caller with access to nothing must get
        # empty results, not everything.
        return "FALSE"
    params["scope_installations"] = list(scope.installation_ids or ())
    return f"{alias}.installation_id = ANY(:scope_installations)"


def _range_clause(time_range: TimeRange | None, params: dict[str, Any], *, column: str) -> str:
    """Half-open ``[start, end)``.

    Exclusive on the end so that two adjacent ranges — or two adjacent chart
    buckets — never both contain the boundary instant.
    """
    if time_range is None:
        return "TRUE"
    params["range_start"] = time_range.start
    params["range_end"] = time_range.end
    return f"{column} >= :range_start AND {column} < :range_end"


def _and(*clauses: str) -> str:
    kept = [c for c in clauses if c and c != "TRUE"]
    return " AND ".join(kept) if kept else "TRUE"


# ---------------------------------------------------------------------------
# Overview (A1)
# ---------------------------------------------------------------------------


def overview_summary(
    *,
    scope: ViewerScope,
    time_range: TimeRange,
    repository_id: int | None = None,
) -> Query:
    """The KPI tiles, as one round trip.

    One query rather than six, because six sequential aggregates over the same
    tables is six planner invocations and six round trips to render a single
    row of tiles. Each sub-select is independently scoped and ranged, so the
    tiles cannot silently describe different populations — the specific bug
    that makes a dashboard say "12 reviews, 40 findings" when the 40 came from
    a different window.
    """
    params: dict[str, Any] = {}
    scope_sql = _scope_clause(scope, params)
    repo_sql = "TRUE"
    if repository_id is not None:
        params["repository_id"] = repository_id
        repo_sql = "r.id = :repository_id"
    params["range_start"] = time_range.start
    params["range_end"] = time_range.end
    params["completed_statuses"] = list(COMPLETED_RUN_STATUSES)

    visible_repos = f"""
        SELECT r.id FROM repositories r
        WHERE {_and(scope_sql, repo_sql)}
    """

    sql = f"""
    WITH visible AS ({visible_repos}),
    runs_in_range AS (
        SELECT rr.*
        FROM review_runs rr
        JOIN visible v ON v.id = rr.repository_id
        WHERE rr.created_at >= :range_start AND rr.created_at < :range_end
    ),
    findings_in_range AS (
        SELECT f.*
        FROM findings f
        JOIN visible v ON v.id = f.repository_id
        WHERE f.created_at >= :range_start AND f.created_at < :range_end
    )
    SELECT
        (SELECT count(*) FROM visible)                                   AS repositories_visible,
        (SELECT count(*) FROM repositories r
          WHERE r.id IN (SELECT id FROM visible) AND r.is_active)        AS repositories_active,

        -- Unique pull requests that produced at least one completed review.
        -- COUNT(DISTINCT pull_request_id), never COUNT(*): three attempts at
        -- one pull request are one pull request reviewed.
        (SELECT count(DISTINCT pull_request_id) FROM runs_in_range
          WHERE status = ANY(:completed_statuses))                       AS pull_requests_reviewed,

        -- Review ATTEMPTS. Deliberately a different number from the line
        -- above, and never presented as if it were the same one.
        (SELECT count(*) FROM runs_in_range)                             AS review_runs_total,
        (SELECT count(*) FROM runs_in_range WHERE status = 'succeeded')  AS runs_succeeded,
        (SELECT count(*) FROM runs_in_range
          WHERE status = 'partial_success')                              AS runs_partial_success,
        (SELECT count(*) FROM runs_in_range WHERE status = 'failed')     AS runs_failed,
        (SELECT count(*) FROM runs_in_range WHERE status = 'superseded') AS runs_superseded,
        (SELECT count(*) FROM runs_in_range
          WHERE status IN ('queued', 'running'))                         AS runs_in_flight,
        (SELECT count(*) FROM runs_in_range WHERE context_truncated)     AS runs_limited_coverage,

        -- The model-output funnel, summed over runs rather than counted over
        -- rows: claims dropped before persistence have no row to count.
        (SELECT COALESCE(sum(findings_emitted), 0) FROM runs_in_range)   AS claims_emitted,
        (SELECT COALESCE(sum(findings_dropped), 0) FROM runs_in_range)   AS claims_dropped,

        (SELECT count(*) FROM findings_in_range f
          WHERE f.rejection_reason IS NULL)                              AS validated_findings,
        (SELECT count(*) FROM findings_in_range f
          WHERE f.rejection_reason IS NOT NULL)                          AS rejected_claims,
        (SELECT count(*) FROM findings_in_range f
          WHERE f.rejection_reason IS NULL AND f.status = 'open')        AS open_findings,
        (SELECT count(*) FROM findings_in_range f
          WHERE f.rejection_reason IS NULL AND f.status = 'open'
            AND f.severity IN ('critical', 'high'))                      AS open_high_severity,

        -- Publication is read from review_comments — the record of what
        -- GitHub actually accepted. Never inferred from LineMap
        -- commentability, which is our opinion, not GitHub's.
        (SELECT count(*) FROM review_comments rc
          JOIN runs_in_range rr ON rr.id = rc.review_run_id)             AS published_comments,
        (SELECT COALESCE(sum(findings_summary), 0) FROM runs_in_range)   AS findings_demoted,

        (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)
           FROM runs_in_range WHERE duration_ms IS NOT NULL)             AS duration_p50_ms,
        (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
           FROM runs_in_range WHERE duration_ms IS NOT NULL)             AS duration_p95_ms,

        (SELECT count(*) FROM findings_in_range f
          WHERE f.rejection_reason IS NULL AND f.anchor_offset = 0)      AS anchors_exact,
        (SELECT count(*) FROM findings_in_range f
          WHERE f.rejection_reason IS NULL
            AND f.anchor_offset IS NOT NULL AND f.anchor_offset <> 0)    AS anchors_relocated
    """
    return Query(sql=sql, params=params)


def findings_by_severity(
    *, scope: ViewerScope, time_range: TimeRange, repository_id: int | None = None
) -> Query:
    """Validated findings grouped by severity.

    Restricted to validated rows on purpose: a rejected claim's severity is the
    model's unverified opinion about something we refused to publish, and
    mixing it into a severity chart would present unverified claims as findings.
    """
    params: dict[str, Any] = {}
    clauses = _finding_scope_clauses(scope, time_range, repository_id, params)
    sql = f"""
    SELECT f.severity, count(*) AS count
    FROM findings f
    JOIN repositories r ON r.id = f.repository_id
    WHERE {_and(*clauses, VALIDATED)}
    GROUP BY f.severity
    """
    return Query(sql=sql, params=params)


def findings_by_category(
    *, scope: ViewerScope, time_range: TimeRange, repository_id: int | None = None
) -> Query:
    params: dict[str, Any] = {}
    clauses = _finding_scope_clauses(scope, time_range, repository_id, params)
    sql = f"""
    SELECT f.category, count(*) AS count
    FROM findings f
    JOIN repositories r ON r.id = f.repository_id
    WHERE {_and(*clauses, VALIDATED)}
    GROUP BY f.category
    """
    return Query(sql=sql, params=params)


def rejection_reasons(
    *, scope: ViewerScope, time_range: TimeRange, repository_id: int | None = None
) -> Query:
    """Why structural validation rejected model claims.

    This is the phase's most useful diagnostic: a rising ``anchor_not_found``
    rate is a prompt regression, and a non-zero ``unsafe_path`` rate is a
    prompt-injection indicator. It is **not** a false-positive rate.
    """
    params: dict[str, Any] = {}
    clauses = _finding_scope_clauses(scope, time_range, repository_id, params)
    sql = f"""
    SELECT f.rejection_reason, count(*) AS count
    FROM findings f
    JOIN repositories r ON r.id = f.repository_id
    WHERE {_and(*clauses, REJECTED)}
    GROUP BY f.rejection_reason
    ORDER BY count DESC, f.rejection_reason
    """
    return Query(sql=sql, params=params)


def anchor_distribution(
    *, scope: ViewerScope, time_range: TimeRange, repository_id: int | None = None
) -> Query:
    """How far the model's claimed line sat from the resolved line.

    Buckets, not a percentage. ``anchor_offset`` measures the relationship
    between a claimed location and the location our relocation policy resolved
    — under *that policy*. It is not an accuracy score and must never be
    labelled as one.
    """
    params: dict[str, Any] = {}
    clauses = _finding_scope_clauses(scope, time_range, repository_id, params)
    sql = f"""
    SELECT
        CASE
            WHEN f.anchor_offset = 0 THEN 'exact'
            WHEN abs(f.anchor_offset) = 1 THEN 'off_by_1'
            WHEN abs(f.anchor_offset) = 2 THEN 'off_by_2'
            WHEN abs(f.anchor_offset) >= 3 THEN 'off_by_3_plus'
            ELSE 'unresolved'
        END AS bucket,
        count(*) AS count
    FROM findings f
    JOIN repositories r ON r.id = f.repository_id
    WHERE {_and(*clauses, VALIDATED)}
    GROUP BY bucket
    """
    return Query(sql=sql, params=params)


def _finding_scope_clauses(
    scope: ViewerScope,
    time_range: TimeRange | None,
    repository_id: int | None,
    params: dict[str, Any],
) -> list[str]:
    clauses = [
        _scope_clause(scope, params),
        _range_clause(time_range, params, column="f.created_at"),
    ]
    if repository_id is not None:
        params["repository_id"] = repository_id
        clauses.append("f.repository_id = :repository_id")
    return clauses


# ---------------------------------------------------------------------------
# Time series (A14)
# ---------------------------------------------------------------------------


_BUCKET_WIDTH = {"day": "1 day", "week": "1 week"}
"""Bucket widths, keyed by an already-validated token.

A dict of constants rather than string interpolation of the caller's value:
the key has been through ``parse_bucket``, and the value it selects is text
this file wrote.
"""


def review_trends(
    *,
    scope: ViewerScope,
    time_range: TimeRange,
    bucket: str = "day",
    repository_id: int | None = None,
) -> Query:
    """Review activity over time, **gap-filled**.

    ``generate_series`` produces every bucket in the range and the aggregates
    LEFT JOIN onto it, so a day with no reviews is a zero rather than a missing
    point. A line chart that simply omits quiet days draws a straight line
    across them and shows a trend that did not happen.

    Buckets are UTC (``date_trunc`` on a ``timestamptz`` uses the session
    ``TimeZone``, which the executor pins to UTC) and half-open, matching the
    range semantics in ``app.api.filters``.
    """
    width = _BUCKET_WIDTH.get(bucket, "1 day")
    params: dict[str, Any] = {"range_start": time_range.start, "range_end": time_range.end}
    scope_sql = _scope_clause(scope, params)
    repo_sql = "TRUE"
    if repository_id is not None:
        params["repository_id"] = repository_id
        repo_sql = "rr.repository_id = :repository_id"

    sql = f"""
    WITH buckets AS (
        SELECT generate_series(
            date_trunc('{bucket}', CAST(:range_start AS timestamptz)),
            CAST(:range_end AS timestamptz) - interval '1 microsecond',
            interval '{width}'
        ) AS bucket_start
    ),
    runs AS (
        SELECT
            date_trunc('{bucket}', rr.created_at) AS bucket_start,
            count(*)                                      AS runs_total,
            count(*) FILTER (WHERE rr.status = 'succeeded')        AS runs_succeeded,
            count(*) FILTER (WHERE rr.status = 'partial_success')  AS runs_partial,
            count(*) FILTER (WHERE rr.status = 'failed')           AS runs_failed,
            count(*) FILTER (WHERE rr.status = 'superseded')       AS runs_superseded,
            count(DISTINCT rr.pull_request_id)                     AS pull_requests
        FROM review_runs rr
        JOIN repositories r ON r.id = rr.repository_id
        WHERE rr.created_at >= :range_start AND rr.created_at < :range_end
          AND {_and(scope_sql, repo_sql)}
        GROUP BY 1
    ),
    finding_counts AS (
        SELECT
            date_trunc('{bucket}', f.created_at) AS bucket_start,
            count(*) FILTER (WHERE f.rejection_reason IS NULL)     AS validated_findings,
            count(*) FILTER (WHERE f.rejection_reason IS NOT NULL) AS rejected_claims
        FROM findings f
        JOIN repositories r ON r.id = f.repository_id
        WHERE f.created_at >= :range_start AND f.created_at < :range_end
          AND {_and(scope_sql, "TRUE" if repository_id is None else "f.repository_id = :repository_id")}
        GROUP BY 1
    )
    SELECT
        b.bucket_start,
        COALESCE(runs.runs_total, 0)          AS runs_total,
        COALESCE(runs.runs_succeeded, 0)      AS runs_succeeded,
        COALESCE(runs.runs_partial, 0)        AS runs_partial,
        COALESCE(runs.runs_failed, 0)         AS runs_failed,
        COALESCE(runs.runs_superseded, 0)     AS runs_superseded,
        COALESCE(runs.pull_requests, 0)       AS pull_requests,
        COALESCE(fc.validated_findings, 0)    AS validated_findings,
        COALESCE(fc.rejected_claims, 0)       AS rejected_claims
    FROM buckets b
    LEFT JOIN runs ON runs.bucket_start = b.bucket_start
    LEFT JOIN finding_counts fc ON fc.bucket_start = b.bucket_start
    ORDER BY b.bucket_start
    """
    return Query(sql=sql, params=params)


# ---------------------------------------------------------------------------
# Repositories (A2, A3)
# ---------------------------------------------------------------------------


def repositories_page(
    *,
    scope: ViewerScope,
    filters: RepositoryFilters,
    cursor: dict[str, Any],
    limit: int,
) -> Query:
    """Repository list, keyset-paginated by ``(full_name, id)``.

    Sorted by name rather than by activity: a list a human scans must be in an
    order they can predict. ``id`` is the tie-break so the sort key is unique —
    a keyset cursor over a non-unique key silently skips rows.
    """
    params: dict[str, Any] = {"limit": limit + 1}
    clauses = [_scope_clause(scope, params)]

    if filters.is_active is not None:
        params["is_active"] = filters.is_active
        clauses.append("r.is_active = :is_active")
    if filters.installation_id is not None:
        params["installation_id"] = filters.installation_id
        clauses.append("r.installation_id = :installation_id")
    if filters.search:
        params["search"] = f"%{escape_like(filters.search)}%"
        clauses.append("r.full_name ILIKE :search ESCAPE '\\'")
    if cursor:
        params["cursor_name"] = cursor.get("full_name")
        params["cursor_id"] = cursor.get("id")
        clauses.append("(r.full_name, r.id) > (:cursor_name, :cursor_id)")

    sql = f"""
    SELECT
        r.id, r.full_name, r.owner_login, r.name, r.default_branch,
        r.is_private, r.is_active, r.primary_language, r.installation_id,
        agg.latest_review_at,
        COALESCE(agg.review_runs, 0)          AS review_runs,
        COALESCE(agg.pull_requests, 0)        AS pull_requests_reviewed,
        COALESCE(agg.open_findings, 0)        AS open_findings,
        COALESCE(agg.open_high_severity, 0)   AS open_high_severity
    FROM repositories r
    LEFT JOIN LATERAL (
        SELECT
            max(rr.finished_at)                  AS latest_review_at,
            count(*)                             AS review_runs,
            count(DISTINCT rr.pull_request_id)   AS pull_requests
        FROM review_runs rr
        WHERE rr.repository_id = r.id
          AND rr.status IN ('succeeded', 'partial_success')
    ) runs ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            runs.latest_review_at, runs.review_runs, runs.pull_requests,
            count(*) FILTER (
                WHERE f.rejection_reason IS NULL AND f.status = 'open'
            ) AS open_findings,
            count(*) FILTER (
                WHERE f.rejection_reason IS NULL AND f.status = 'open'
                  AND f.severity IN ('critical', 'high')
            ) AS open_high_severity
        FROM findings f
        WHERE f.repository_id = r.id
    ) agg ON TRUE
    WHERE {_and(*clauses)}
    ORDER BY r.full_name, r.id
    LIMIT :limit
    """
    return Query(sql=sql, params=params)


def repository_detail(*, scope: ViewerScope, repository_id: int) -> Query:
    """One repository plus its aggregates.

    The scope clause is applied here too, so a caller outside scope gets **no
    row** and the route returns 404. Architecture.md §17.7: 404 rather than 403
    for anything outside the caller's scope, because 403 confirms the resource
    exists and leaks the existence of other accounts' repositories.
    """
    params: dict[str, Any] = {"repository_id": repository_id}
    scope_sql = _scope_clause(scope, params)
    sql = f"""
    SELECT
        r.id, r.full_name, r.owner_login, r.name, r.default_branch,
        r.is_private, r.is_active, r.primary_language, r.installation_id,
        r.created_at,
        i.account_login, i.account_type, i.suspended_at IS NOT NULL AS installation_suspended,
        (SELECT count(*) FROM pull_requests p WHERE p.repository_id = r.id)      AS pull_requests_total,
        (SELECT count(DISTINCT rr.pull_request_id) FROM review_runs rr
          WHERE rr.repository_id = r.id
            AND rr.status IN ('succeeded','partial_success'))                    AS pull_requests_reviewed,
        (SELECT count(*) FROM review_runs rr WHERE rr.repository_id = r.id)      AS review_runs_total,
        (SELECT count(*) FROM review_runs rr
          WHERE rr.repository_id = r.id AND rr.status = 'partial_success')       AS review_runs_partial,
        (SELECT count(*) FROM review_runs rr
          WHERE rr.repository_id = r.id AND rr.status = 'failed')                AS review_runs_failed,
        (SELECT max(rr.finished_at) FROM review_runs rr
          WHERE rr.repository_id = r.id
            AND rr.status IN ('succeeded','partial_success'))                    AS latest_review_at,
        (SELECT count(*) FROM findings f
          WHERE f.repository_id = r.id AND f.rejection_reason IS NULL)           AS validated_findings,
        (SELECT count(*) FROM findings f
          WHERE f.repository_id = r.id AND f.rejection_reason IS NOT NULL)       AS rejected_claims,
        (SELECT count(*) FROM findings f
          WHERE f.repository_id = r.id AND f.rejection_reason IS NULL
            AND f.status = 'open')                                               AS open_findings,
        (SELECT count(*) FROM review_comments rc
          JOIN review_runs rr ON rr.id = rc.review_run_id
          WHERE rr.repository_id = r.id)                                         AS published_comments
    FROM repositories r
    JOIN installations i ON i.id = r.installation_id
    WHERE r.id = :repository_id AND {scope_sql}
    """
    return Query(sql=sql, params=params)


# ---------------------------------------------------------------------------
# Pull requests (A5, A6)
# ---------------------------------------------------------------------------

# "The current review" for a pull request: the newest non-superseded run for
# the commit that is currently head. Anchored to `head_sha` rather than to
# time, so a run for an older commit can never present itself as the current
# result even if it finished later than a superseded newer one.
_CURRENT_RUN_LATERAL = """
    SELECT rr.id, rr.status, rr.attempt, rr.head_sha, rr.finished_at, rr.started_at,
           rr.duration_ms, rr.error_code, rr.context_truncated,
           rr.findings_emitted, rr.findings_dropped, rr.findings_inline,
           rr.findings_summary, rr.findings_dashboard_only, rr.files_changed,
           rr.files_analysed, rr.files_skipped
    FROM review_runs rr
    WHERE rr.pull_request_id = p.id
      AND rr.head_sha = p.head_sha
      AND rr.status <> 'superseded'
    ORDER BY rr.attempt DESC
    LIMIT 1
"""


def pull_requests_page(
    *,
    scope: ViewerScope,
    filters: PullRequestFilters,
    cursor: dict[str, Any],
    limit: int,
) -> Query:
    """Pull-request list with each PR's *current* review.

    Keyset by ``(updated_at DESC, id DESC)``: the natural reading order is
    most-recently-touched first, and ``id`` makes the key unique.
    """
    params: dict[str, Any] = {"limit": limit + 1}
    clauses = [_scope_clause(scope, params)]

    if filters.repository_id is not None:
        params["repository_id"] = filters.repository_id
        clauses.append("p.repository_id = :repository_id")
    if filters.states:
        params["pr_states"] = list(filters.states)
        clauses.append("p.state = ANY(:pr_states)")
    if filters.search:
        params["search"] = f"%{escape_like(filters.search)}%"
        clauses.append("p.title ILIKE :search ESCAPE '\\'")
    if filters.time_range is not None:
        clauses.append(_range_clause(filters.time_range, params, column="p.updated_at"))
    if filters.run_statuses:
        params["run_statuses"] = list(filters.run_statuses)
        clauses.append("current_run.status = ANY(:run_statuses)")
    if cursor:
        params["cursor_updated_at"] = cursor.get("updated_at")
        params["cursor_id"] = cursor.get("id")
        clauses.append("(p.updated_at, p.id) < (:cursor_updated_at, :cursor_id)")

    sql = f"""
    SELECT
        p.id, p.number, p.title, p.author_login, p.state, p.is_draft,
        p.head_sha, p.base_ref, p.head_ref, p.html_url, p.updated_at,
        p.changed_files_count, p.changed_files_truncated,
        r.id AS repository_id, r.full_name AS repository_full_name,
        current_run.id            AS current_run_id,
        current_run.status        AS current_run_status,
        current_run.attempt       AS current_run_attempt,
        current_run.finished_at   AS current_run_finished_at,
        current_run.duration_ms   AS current_run_duration_ms,
        current_run.context_truncated AS current_run_limited_coverage,
        COALESCE(counts.validated_findings, 0) AS validated_findings,
        COALESCE(counts.rejected_claims, 0)    AS rejected_claims,
        COALESCE(counts.published_comments, 0) AS published_comments,
        COALESCE(counts.open_critical_high, 0) AS open_critical_high,
        (SELECT count(*) FROM review_runs rr2 WHERE rr2.pull_request_id = p.id) AS review_run_count
    FROM pull_requests p
    JOIN repositories r ON r.id = p.repository_id
    LEFT JOIN LATERAL ({_CURRENT_RUN_LATERAL}) current_run ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            count(*) FILTER (WHERE f.rejection_reason IS NULL)     AS validated_findings,
            count(*) FILTER (WHERE f.rejection_reason IS NOT NULL) AS rejected_claims,
            count(*) FILTER (
                WHERE f.rejection_reason IS NULL AND f.status = 'open'
                  AND f.severity IN ('critical','high')
            )                                                      AS open_critical_high,
            (SELECT count(*) FROM review_comments rc
              WHERE rc.review_run_id = current_run.id)             AS published_comments
        FROM findings f
        WHERE f.review_run_id = current_run.id
    ) counts ON TRUE
    WHERE {_and(*clauses)}
    ORDER BY p.updated_at DESC, p.id DESC
    LIMIT :limit
    """
    return Query(sql=sql, params=params)


def pull_request_detail(*, scope: ViewerScope, pull_request_id: int) -> Query:
    params: dict[str, Any] = {"pull_request_id": pull_request_id}
    scope_sql = _scope_clause(scope, params)
    sql = f"""
    SELECT
        p.id, p.number, p.title, p.author_login, p.state, p.is_draft,
        p.head_sha, p.base_sha, p.base_ref, p.head_ref, p.html_url,
        p.changed_files_count, p.changed_files_truncated,
        p.opened_at, p.closed_at, p.merged_at, p.updated_at, p.last_synced_at,
        r.id AS repository_id, r.full_name AS repository_full_name,
        r.default_branch, r.is_active AS repository_active,
        current_run.id AS current_run_id
    FROM pull_requests p
    JOIN repositories r ON r.id = p.repository_id
    LEFT JOIN LATERAL ({_CURRENT_RUN_LATERAL}) current_run ON TRUE
    WHERE p.id = :pull_request_id AND {scope_sql}
    """
    return Query(sql=sql, params=params)


def pull_request_runs(*, pull_request_id: int) -> Query:
    """Every attempt for one pull request, newest first.

    All attempts, including superseded ones. The most informative thing about a
    pull request is often that attempt 1 found four issues and the attempt
    after the fix found none — a story that is invisible if only the current
    run is shown. Aggregate metrics elsewhere use ``COUNT(DISTINCT
    pull_request_id)`` precisely so that showing every attempt here does not
    inflate them.
    """
    return Query(
        sql="""
        SELECT
            rr.id, rr.attempt, rr.status, rr.trigger, rr.head_sha, rr.base_sha,
            rr.error_code, rr.error_message, rr.provider, rr.model_name,
            rr.prompt_version, rr.input_tokens, rr.output_tokens,
            rr.ai_latency_ms, rr.ai_truncated, rr.context_truncated,
            rr.files_changed, rr.files_analysed, rr.files_skipped,
            rr.findings_emitted, rr.findings_dropped, rr.findings_persisted,
            rr.findings_inline, rr.findings_summary, rr.findings_dashboard_only,
            rr.started_at, rr.finished_at, rr.duration_ms, rr.created_at,
            rr.github_review_id,
            (SELECT count(*) FROM review_comments rc WHERE rc.review_run_id = rr.id)
                AS published_comments,
            (rr.head_sha <> p.head_sha) AS is_stale
        FROM review_runs rr
        JOIN pull_requests p ON p.id = rr.pull_request_id
        WHERE rr.pull_request_id = :pull_request_id
        ORDER BY rr.created_at DESC, rr.attempt DESC
        """,
        params={"pull_request_id": pull_request_id},
    )


# ---------------------------------------------------------------------------
# Review runs (A7, A8)
# ---------------------------------------------------------------------------


def review_runs_page(
    *, scope: ViewerScope, filters: RunFilters, cursor: dict[str, Any], limit: int
) -> Query:
    params: dict[str, Any] = {"limit": limit + 1}
    clauses = [_scope_clause(scope, params)]

    if filters.repository_id is not None:
        params["repository_id"] = filters.repository_id
        clauses.append("rr.repository_id = :repository_id")
    if filters.pull_request_id is not None:
        params["pull_request_id"] = filters.pull_request_id
        clauses.append("rr.pull_request_id = :pull_request_id")
    if filters.statuses:
        params["run_statuses"] = list(filters.statuses)
        clauses.append("rr.status = ANY(:run_statuses)")
    if not filters.include_superseded:
        clauses.append("rr.status <> 'superseded'")
    if filters.time_range is not None:
        clauses.append(_range_clause(filters.time_range, params, column="rr.created_at"))
    if cursor:
        params["cursor_created_at"] = cursor.get("created_at")
        params["cursor_id"] = cursor.get("id")
        clauses.append("(rr.created_at, rr.id) < (:cursor_created_at, CAST(:cursor_id AS uuid))")

    sql = f"""
    SELECT
        rr.id, rr.status, rr.trigger, rr.attempt, rr.head_sha,
        rr.error_code, rr.context_truncated, rr.ai_truncated,
        rr.findings_emitted, rr.findings_dropped, rr.findings_inline,
        rr.findings_summary, rr.findings_dashboard_only,
        rr.files_changed, rr.files_analysed, rr.files_skipped,
        rr.started_at, rr.finished_at, rr.duration_ms, rr.created_at,
        p.id AS pull_request_id, p.number AS pull_request_number, p.title,
        r.id AS repository_id, r.full_name AS repository_full_name,
        (rr.head_sha <> p.head_sha) AS is_stale,
        (SELECT count(*) FROM review_comments rc WHERE rc.review_run_id = rr.id)
            AS published_comments
    FROM review_runs rr
    JOIN pull_requests p ON p.id = rr.pull_request_id
    JOIN repositories r ON r.id = rr.repository_id
    WHERE {_and(*clauses)}
    ORDER BY rr.created_at DESC, rr.id DESC
    LIMIT :limit
    """
    return Query(sql=sql, params=params)


def review_run_detail(*, scope: ViewerScope, review_run_id: str) -> Query:
    params: dict[str, Any] = {"review_run_id": review_run_id}
    scope_sql = _scope_clause(scope, params)
    sql = f"""
    SELECT
        rr.id, rr.status, rr.trigger, rr.attempt, rr.head_sha, rr.base_sha,
        rr.error_code, rr.error_message, rr.provider, rr.model_name,
        rr.prompt_version, rr.input_tokens, rr.output_tokens, rr.ai_latency_ms,
        rr.ai_truncated, rr.context_truncated,
        rr.files_changed, rr.files_analysed, rr.files_skipped,
        rr.findings_emitted, rr.findings_dropped, rr.findings_persisted,
        rr.findings_inline, rr.findings_summary, rr.findings_dashboard_only,
        rr.started_at, rr.finished_at, rr.duration_ms, rr.created_at,
        rr.github_review_id, rr.quality_score,
        p.id AS pull_request_id, p.number AS pull_request_number,
        p.title, p.html_url, p.head_sha AS pull_request_head_sha,
        r.id AS repository_id, r.full_name AS repository_full_name,
        (rr.head_sha <> p.head_sha) AS is_stale,
        (SELECT count(*) FROM review_comments rc WHERE rc.review_run_id = rr.id)
            AS published_comments
    FROM review_runs rr
    JOIN pull_requests p ON p.id = rr.pull_request_id
    JOIN repositories r ON r.id = rr.repository_id
    WHERE rr.id = CAST(:review_run_id AS uuid) AND {scope_sql}
    """
    return Query(sql=sql, params=params)


def review_run_changed_files(*, review_run_id: str) -> Query:
    """Per-file coverage for one run.

    Joined on ``(pull_request_id, head_sha)`` rather than on
    ``changed_files.review_run_id``: the changed-file set is keyed on the
    *commit* (ADR-0006), so a re-run of the same commit reuses those rows and
    the back-reference column may point at whichever run wrote them last.
    """
    return Query(
        sql="""
        SELECT
            cf.id, cf.file_path, cf.previous_path, cf.change_status,
            cf.additions, cf.deletions, cf.changes, cf.has_patch,
            cf.language, cf.is_analysed, cf.skip_reason, cf.parse_ok,
            cf.declarations_touched, cf.patch_line_count,
            (SELECT count(*) FROM findings f
              WHERE f.review_run_id = rr.id AND f.file_path = cf.file_path
                AND f.rejection_reason IS NULL) AS validated_findings
        FROM review_runs rr
        JOIN changed_files cf
          ON cf.pull_request_id = rr.pull_request_id AND cf.head_sha = rr.head_sha
        WHERE rr.id = CAST(:review_run_id AS uuid)
        ORDER BY cf.file_path
        """,
        params={"review_run_id": review_run_id},
    )


# ---------------------------------------------------------------------------
# Findings (A11, A12)
# ---------------------------------------------------------------------------


def findings_page(
    *, scope: ViewerScope, filters: FindingFilters, cursor: dict[str, Any], limit: int
) -> Query:
    params: dict[str, Any] = {"limit": limit + 1}
    clauses = _finding_clauses(scope, filters, params)

    if cursor:
        params["cursor_created_at"] = cursor.get("created_at")
        params["cursor_id"] = cursor.get("id")
        clauses.append("(f.created_at, f.id) < (:cursor_created_at, CAST(:cursor_id AS uuid))")

    sql = f"""
    SELECT
        f.id, f.severity, f.category, f.title, f.status, f.mapping_status,
        f.rejection_reason, f.file_path, f.claimed_line_start, f.resolved_line,
        f.anchor_offset, f.anchor_verified, f.side, f.qualified_name,
        f.model_confidence, f.effective_confidence, f.cwe_id, f.created_at,
        f.review_run_id, f.pull_request_id,
        r.id AS repository_id, r.full_name AS repository_full_name,
        p.number AS pull_request_number,
        rr.head_sha,
        (rr.head_sha <> p.head_sha) AS is_stale,
        {PUBLISHED} AS is_published
    FROM findings f
    JOIN repositories r ON r.id = f.repository_id
    JOIN pull_requests p ON p.id = f.pull_request_id
    JOIN review_runs rr ON rr.id = f.review_run_id
    WHERE {_and(*clauses)}
    ORDER BY f.created_at DESC, f.id DESC
    LIMIT :limit
    """
    return Query(sql=sql, params=params)


def findings_count(*, scope: ViewerScope, filters: FindingFilters) -> Query:
    """Exact count for the same filter set.

    A separate, explicitly requested query. Running it alongside every page
    would make the list endpoint pay for a full aggregate on each scroll, which
    is how a findings list becomes the slowest screen in the product.
    """
    params: dict[str, Any] = {}
    clauses = _finding_clauses(scope, filters, params)
    sql = f"""
    SELECT count(*) AS total
    FROM findings f
    JOIN repositories r ON r.id = f.repository_id
    JOIN pull_requests p ON p.id = f.pull_request_id
    JOIN review_runs rr ON rr.id = f.review_run_id
    WHERE {_and(*clauses)}
    """
    return Query(sql=sql, params=params)


def _finding_clauses(
    scope: ViewerScope, filters: FindingFilters, params: dict[str, Any]
) -> list[str]:
    clauses = [_scope_clause(scope, params)]

    if filters.repository_id is not None:
        params["repository_id"] = filters.repository_id
        clauses.append("f.repository_id = :repository_id")
    if filters.pull_request_id is not None:
        params["pull_request_id"] = filters.pull_request_id
        clauses.append("f.pull_request_id = :pull_request_id")
    if filters.review_run_id is not None:
        params["review_run_id"] = filters.review_run_id
        clauses.append("f.review_run_id = CAST(:review_run_id AS uuid)")
    if filters.severities:
        params["severities"] = list(filters.severities)
        clauses.append("f.severity = ANY(:severities)")
    if filters.categories:
        params["categories"] = list(filters.categories)
        clauses.append("f.category = ANY(:categories)")
    if filters.statuses:
        params["statuses"] = list(filters.statuses)
        clauses.append("f.status = ANY(:statuses)")
    if filters.mapping_statuses:
        params["mapping_statuses"] = list(filters.mapping_statuses)
        clauses.append("f.mapping_status = ANY(:mapping_statuses)")
    if filters.rejection_reason is not None:
        params["rejection_reason"] = filters.rejection_reason
        clauses.append("f.rejection_reason = :rejection_reason")

    # `validation` and `publication` are tokens the filter layer has already
    # checked against a closed list, so the SQL they select is chosen from
    # constants rather than built from their text.
    if filters.validation == "validated":
        clauses.append(VALIDATED)
    elif filters.validation == "rejected":
        clauses.append(REJECTED)

    if filters.publication == "published":
        clauses.append(PUBLISHED)
    elif filters.publication == "not_published":
        clauses.append(f"NOT {PUBLISHED}")

    if filters.search:
        params["search"] = f"%{escape_like(filters.search)}%"
        clauses.append("f.title ILIKE :search ESCAPE '\\'")
    if filters.time_range is not None:
        clauses.append(_range_clause(filters.time_range, params, column="f.created_at"))
    return clauses


def finding_detail(*, scope: ViewerScope, finding_id: str) -> Query:
    params: dict[str, Any] = {"finding_id": finding_id}
    scope_sql = _scope_clause(scope, params)
    sql = f"""
    SELECT
        f.id, f.severity, f.category, f.title, f.description, f.impact,
        f.suggested_fix, f.rationale, f.cwe_id, f.status, f.mapping_status,
        f.rejection_reason, f.file_path, f.claimed_line_start, f.claimed_line_end,
        f.resolved_line, f.anchor_offset, f.anchor_snippet, f.anchor_verified,
        f.side, f.declaration_id, f.qualified_name, f.fingerprint,
        f.model_confidence, f.effective_confidence, f.model_finding_id,
        f.created_at, f.updated_at,
        f.review_run_id, f.pull_request_id, f.repository_id,
        r.full_name AS repository_full_name,
        p.number AS pull_request_number, p.html_url AS pull_request_url,
        p.head_sha AS pull_request_head_sha,
        rr.head_sha, rr.status AS review_run_status, rr.attempt,
        rr.model_name, rr.prompt_version,
        (rr.head_sha <> p.head_sha) AS is_stale,
        rc.github_comment_id, rc.html_url AS comment_url, rc.posted_at,
        rc.line AS comment_line, rc.side AS comment_side
    FROM findings f
    JOIN repositories r ON r.id = f.repository_id
    JOIN pull_requests p ON p.id = f.pull_request_id
    JOIN review_runs rr ON rr.id = f.review_run_id
    LEFT JOIN review_comments rc ON rc.finding_id = f.id
    WHERE f.id = CAST(:finding_id AS uuid) AND {scope_sql}
    """
    return Query(sql=sql, params=params)


def finding_events(*, finding_id: str) -> Query:
    return Query(
        sql="""
        SELECT id, from_status, to_status, source, note, created_at, actor_user_id
        FROM finding_events
        WHERE finding_id = CAST(:finding_id AS uuid)
        ORDER BY created_at DESC, id DESC
        """,
        params={"finding_id": finding_id},
    )


# ---------------------------------------------------------------------------
# Integration + processing health (A18, A20)
# ---------------------------------------------------------------------------


def processing_health(*, scope: ViewerScope, time_range: TimeRange) -> Query:
    """Reliability panel.

    ``success_rate`` is computed over **terminal** runs only. Including
    superseded or still-running attempts in the denominator would make the rate
    fall whenever someone pushes twice quickly — which is not a reliability
    problem and must not look like one.
    """
    params: dict[str, Any] = {}
    scope_sql = _scope_clause(scope, params)
    range_sql = _range_clause(time_range, params, column="rr.created_at")
    params["terminal_statuses"] = list(TERMINAL_RUN_STATUSES)
    sql = f"""
    SELECT
        count(*)                                                  AS runs_total,
        count(*) FILTER (WHERE rr.status = ANY(:terminal_statuses)) AS runs_terminal,
        count(*) FILTER (WHERE rr.status = 'succeeded')           AS succeeded,
        count(*) FILTER (WHERE rr.status = 'partial_success')     AS partial_success,
        count(*) FILTER (WHERE rr.status = 'failed')              AS failed,
        count(*) FILTER (WHERE rr.status = 'superseded')          AS superseded,
        count(*) FILTER (WHERE rr.status = 'skipped')             AS skipped,
        count(*) FILTER (WHERE rr.status IN ('queued','running')) AS in_flight,
        count(*) FILTER (WHERE rr.context_truncated)              AS limited_coverage,
        count(*) FILTER (WHERE rr.ai_truncated)                   AS model_output_truncated,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY rr.duration_ms)
            FILTER (WHERE rr.duration_ms IS NOT NULL)             AS duration_p50_ms,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY rr.duration_ms)
            FILTER (WHERE rr.duration_ms IS NOT NULL)             AS duration_p95_ms
    FROM review_runs rr
    JOIN repositories r ON r.id = rr.repository_id
    WHERE {_and(scope_sql, range_sql)}
    """
    return Query(sql=sql, params=params)


def run_error_breakdown(*, scope: ViewerScope, time_range: TimeRange) -> Query:
    params: dict[str, Any] = {}
    scope_sql = _scope_clause(scope, params)
    range_sql = _range_clause(time_range, params, column="rr.created_at")
    sql = f"""
    SELECT rr.error_code, count(*) AS count
    FROM review_runs rr
    JOIN repositories r ON r.id = rr.repository_id
    WHERE {_and(scope_sql, range_sql, "rr.error_code IS NOT NULL")}
    GROUP BY rr.error_code
    ORDER BY count DESC, rr.error_code
    """
    return Query(sql=sql, params=params)


def integration_status(*, scope: ViewerScope) -> Query:
    """GitHub App installations.

    Exposes account identity and counts only. No token, no private key, no
    webhook secret — none of which are in these tables in the first place,
    which is the stronger guarantee.
    """
    params: dict[str, Any] = {}
    scope_sql = _scope_clause(scope, params, alias="r")
    sql = f"""
    SELECT
        i.id, i.github_installation_id, i.account_login, i.account_type,
        i.repository_selection, i.suspended_at, i.uninstalled_at, i.created_at,
        (SELECT count(*) FROM repositories r
          WHERE r.installation_id = i.id AND {scope_sql})            AS repository_count,
        (SELECT count(*) FROM repositories r
          WHERE r.installation_id = i.id AND r.is_active AND {scope_sql}) AS active_repository_count,
        (SELECT max(we.received_at) FROM webhook_events we
          WHERE we.github_installation_id = i.github_installation_id) AS last_event_at,
        (SELECT count(*) FROM webhook_events we
          WHERE we.github_installation_id = i.github_installation_id
            AND we.processing_status = 'failed')                      AS failed_deliveries
    FROM installations i
    WHERE EXISTS (
        SELECT 1 FROM repositories r WHERE r.installation_id = i.id AND {scope_sql}
    )
    ORDER BY i.account_login
    """
    return Query(sql=sql, params=params)


def recent_activity(*, scope: ViewerScope, limit: int = 10) -> Query:
    """The most recent review attempts, for the dashboard's activity panel."""
    params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
    scope_sql = _scope_clause(scope, params)
    sql = f"""
    SELECT
        rr.id, rr.status, rr.attempt, rr.head_sha, rr.duration_ms,
        rr.finished_at, rr.created_at, rr.context_truncated, rr.error_code,
        rr.findings_inline, rr.findings_dashboard_only,
        p.number AS pull_request_number, p.id AS pull_request_id, p.title,
        r.full_name AS repository_full_name, r.id AS repository_id,
        (rr.head_sha <> p.head_sha) AS is_stale
    FROM review_runs rr
    JOIN pull_requests p ON p.id = rr.pull_request_id
    JOIN repositories r ON r.id = rr.repository_id
    WHERE {scope_sql}
    ORDER BY rr.created_at DESC, rr.id DESC
    LIMIT :limit
    """
    return Query(sql=sql, params=params)


def coverage_summary(
    *, scope: ViewerScope, time_range: TimeRange, repository_id: int | None = None
) -> Query:
    """Review coverage, with reviewed and not-reviewed kept apart.

    Deliberately **not** reduced to a single "coverage %". Files can be absent
    from a review for reasons that are not comparable — a binary file, a
    language with no parser installed, a budget ceiling — and averaging them
    into one number hides the only thing worth knowing, which is *why*.
    """
    params: dict[str, Any] = {}
    scope_sql = _scope_clause(scope, params)
    range_sql = _range_clause(time_range, params, column="rr.created_at")
    repo_sql = "TRUE"
    if repository_id is not None:
        params["repository_id"] = repository_id
        repo_sql = "rr.repository_id = :repository_id"
    sql = f"""
    SELECT
        COALESCE(sum(rr.files_changed), 0)   AS files_changed,
        COALESCE(sum(rr.files_analysed), 0)  AS files_analysed,
        COALESCE(sum(rr.files_skipped), 0)   AS files_skipped,
        count(*) FILTER (WHERE rr.context_truncated) AS runs_over_budget,
        count(*)                              AS runs_total
    FROM review_runs rr
    JOIN repositories r ON r.id = rr.repository_id
    WHERE {_and(scope_sql, range_sql, repo_sql)}
    """
    return Query(sql=sql, params=params)


def skip_reason_breakdown(
    *, scope: ViewerScope, time_range: TimeRange, repository_id: int | None = None
) -> Query:
    """Why files were not analysed, by reason."""
    params: dict[str, Any] = {}
    scope_sql = _scope_clause(scope, params)
    range_sql = _range_clause(time_range, params, column="cf.created_at")
    repo_sql = "TRUE"
    if repository_id is not None:
        params["repository_id"] = repository_id
        repo_sql = "p.repository_id = :repository_id"
    sql = f"""
    SELECT COALESCE(cf.skip_reason, 'unspecified') AS skip_reason, count(*) AS count
    FROM changed_files cf
    JOIN pull_requests p ON p.id = cf.pull_request_id
    JOIN repositories r ON r.id = p.repository_id
    WHERE {_and(scope_sql, range_sql, repo_sql, "cf.is_analysed = false")}
    GROUP BY 1
    ORDER BY count DESC, skip_reason
    """
    return Query(sql=sql, params=params)


__all__ = [
    "COMPLETED_RUN_STATUSES",
    "PUBLISHED",
    "REJECTED",
    "TERMINAL_RUN_STATUSES",
    "VALIDATED",
    "Query",
    "anchor_distribution",
    "coverage_summary",
    "finding_detail",
    "finding_events",
    "findings_by_category",
    "findings_by_severity",
    "findings_count",
    "findings_page",
    "integration_status",
    "overview_summary",
    "processing_health",
    "pull_request_detail",
    "pull_request_runs",
    "pull_requests_page",
    "recent_activity",
    "rejection_reasons",
    "repositories_page",
    "repository_detail",
    "review_run_changed_files",
    "review_run_detail",
    "review_runs_page",
    "review_trends",
    "run_error_breakdown",
    "skip_reason_breakdown",
]
