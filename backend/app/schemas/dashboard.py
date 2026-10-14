"""Dashboard response schemas.

**Explicit models, and every one is built field by field from a row
dictionary.** No route calls ``model_validate`` on an ORM object, and no route
returns a row straight from the database. That is not ceremony: the tables
these queries read contain columns the API has no business exposing —
``error_message`` before redaction, internal ids, raw model prose on a rejected
claim — and the only reliable way to keep them out is for the response type to
enumerate what goes in.

**The vocabulary is precise, because the numbers are easy to overstate.** These
names are the contract the UI renders, and they were chosen to be impossible to
misread:

``claims_emitted``      what the model said.
``claims_dropped``      what could not even be parsed.
``validated_findings``  what passed every structural gate.
``rejected_claims``     what failed one. **Not** "false positives".
``published_comments``  what GitHub confirmed.

A validated finding is *structurally anchored to the reviewed source*. It is
not an independently proven defect, and nothing here may imply that it is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - see the note below
    """Keyset page. ``next_cursor`` is ``None`` on the last page.

    ``total`` is absent unless the endpoint was explicitly asked to count, and
    ``None`` means "not counted" rather than "zero" — a list that reports 0
    when it simply did not count is worse than one that reports nothing.

    **Why the old-style ``Generic[T]`` and not PEP 695 ``class Page[T]``.**
    Ruff's UP046 proposes the PEP 695 form, and for a project pinned to Python
    3.12 that proposal is correct in principle. It is declined deliberately:
    the only interpreter in the verification environment that has this
    project's dependencies is 3.11, so the PEP 695 form cannot be *executed* or
    *tested* here — it is a syntax error before the first assertion runs.

    Shipping a construct that the release process cannot exercise, on a
    linter's suggestion alone, is the kind of change this phase exists to
    prevent. ``Generic[T]`` is equivalent for every consumer, runs on 3.11 and
    3.12, and is covered by the tests. Revisit when the toolchain and the
    runtime agree on a version.
    """

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class FunnelCounts(BaseModel):
    """The model-output funnel for the selected window.

    Stage names are deliberately literal. ``rejected_claims`` counts claims
    that failed a *structural* validation gate — a statement about whether a
    claim could be anchored, not about whether the issue it describes is real.
    """

    claims_emitted: int = 0
    claims_dropped: int = 0
    validated_findings: int = 0
    rejected_claims: int = 0
    published_comments: int = 0
    findings_demoted: int = 0


class RunCounts(BaseModel):
    total: int = 0
    succeeded: int = 0
    partial_success: int = 0
    failed: int = 0
    superseded: int = 0
    in_flight: int = 0
    limited_coverage: int = 0
    """Runs that completed but did not see every changed file, because a
    budget stopped the context build. Counted separately from failures: the
    review happened and produced findings."""


class AnchorCounts(BaseModel):
    """Where the model's claimed line sat relative to the resolved line.

    Counts, never a percentage, and never labelled accuracy. It measures the
    claimed-vs-resolved relationship *under the implemented relocation policy*
    (ADR-0011), which is a property of this system, not of the model.
    """

    exact: int = 0
    relocated: int = 0


class OverviewResponse(BaseModel):
    range_from: datetime
    range_to: datetime
    repositories_visible: int = 0
    repositories_active: int = 0
    pull_requests_reviewed: int = 0
    """Distinct pull requests with at least one completed review in the window.
    Not the number of review attempts — see ``runs.total``."""

    runs: RunCounts = Field(default_factory=RunCounts)
    funnel: FunnelCounts = Field(default_factory=FunnelCounts)
    open_findings: int = 0
    open_high_severity: int = 0
    anchors: AnchorCounts = Field(default_factory=AnchorCounts)
    duration_p50_ms: int | None = None
    duration_p95_ms: int | None = None


class CountBucket(BaseModel):
    key: str
    count: int


class TrendPoint(BaseModel):
    bucket_start: datetime
    runs_total: int = 0
    runs_succeeded: int = 0
    runs_partial: int = 0
    runs_failed: int = 0
    runs_superseded: int = 0
    pull_requests: int = 0
    validated_findings: int = 0
    rejected_claims: int = 0


class TrendResponse(BaseModel):
    bucket: str
    range_from: datetime
    range_to: datetime
    timezone: str = "UTC"
    points: list[TrendPoint]


class ProcessingHealthResponse(BaseModel):
    range_from: datetime
    range_to: datetime
    runs_total: int = 0
    runs_terminal: int = 0
    succeeded: int = 0
    partial_success: int = 0
    failed: int = 0
    superseded: int = 0
    skipped: int = 0
    in_flight: int = 0
    limited_coverage: int = 0
    model_output_truncated: int = 0
    duration_p50_ms: int | None = None
    duration_p95_ms: int | None = None
    error_breakdown: list[CountBucket] = Field(default_factory=list)

    @property
    def completion_rate(self) -> float | None:
        """Succeeded ÷ terminal runs.

        Terminal only: a superseded or still-running attempt is not a
        reliability signal, and counting it would make the rate fall whenever
        somebody pushes twice in a minute.
        """
        if not self.runs_terminal:
            return None
        return self.succeeded / self.runs_terminal


class CoverageResponse(BaseModel):
    """Review coverage, with the reasons kept apart.

    Deliberately not a single percentage: a file can be absent from a review
    because it is binary, because no parser is installed for its language, or
    because a budget stopped the build, and those are not the same fact.
    """

    range_from: datetime
    range_to: datetime
    files_changed: int = 0
    files_analysed: int = 0
    files_skipped: int = 0
    runs_over_budget: int = 0
    runs_total: int = 0
    skip_reasons: list[CountBucket] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class RepositorySummary(BaseModel):
    id: int
    full_name: str
    owner_login: str
    name: str
    default_branch: str
    is_private: bool
    is_active: bool
    primary_language: str | None = None
    latest_review_at: datetime | None = None
    review_runs: int = 0
    pull_requests_reviewed: int = 0
    open_findings: int = 0
    open_high_severity: int = 0


class RepositoryDetailResponse(BaseModel):
    id: int
    full_name: str
    owner_login: str
    name: str
    default_branch: str
    is_private: bool
    is_active: bool
    primary_language: str | None = None
    created_at: datetime | None = None
    installation_account: str
    installation_account_type: str
    installation_suspended: bool = False
    pull_requests_total: int = 0
    pull_requests_reviewed: int = 0
    review_runs_total: int = 0
    review_runs_partial: int = 0
    review_runs_failed: int = 0
    latest_review_at: datetime | None = None
    validated_findings: int = 0
    rejected_claims: int = 0
    open_findings: int = 0
    published_comments: int = 0


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------


class PullRequestSummary(BaseModel):
    id: int
    number: int
    title: str
    author_login: str
    state: str
    is_draft: bool
    head_sha: str
    html_url: str
    updated_at: datetime | None = None
    repository_id: int
    repository_full_name: str
    changed_files_count: int = 0
    changed_files_truncated: bool = False

    current_run_id: str | None = None
    current_run_status: str | None = None
    """The status of the newest non-superseded run **for the current head
    commit**. ``None`` means this pull request has no review of its current
    commit — which is a real state, and must not be rendered as a pass."""

    current_run_attempt: int | None = None
    current_run_finished_at: datetime | None = None
    current_run_duration_ms: int | None = None
    current_run_limited_coverage: bool = False
    review_run_count: int = 0
    """Total attempts across every commit. Shown as history; never added into
    a "pull requests reviewed" total."""

    validated_findings: int = 0
    rejected_claims: int = 0
    published_comments: int = 0
    open_critical_high: int = 0


class ReviewRunSummary(BaseModel):
    id: str
    status: str
    trigger: str
    attempt: int
    head_sha: str
    is_stale: bool = False
    """True when this run's commit is no longer the pull request's head. A
    stale run is history, never the current result."""

    error_code: str | None = None
    limited_coverage: bool = False
    model_output_truncated: bool = False
    claims_emitted: int = 0
    claims_dropped: int = 0
    findings_inline: int = 0
    findings_summary: int = 0
    rejected_claims: int = 0
    published_comments: int = 0
    files_changed: int = 0
    files_analysed: int = 0
    files_skipped: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None
    pull_request_id: int | None = None
    pull_request_number: int | None = None
    title: str | None = None
    repository_id: int | None = None
    repository_full_name: str | None = None


class PullRequestDetailResponse(BaseModel):
    id: int
    number: int
    title: str
    author_login: str
    state: str
    is_draft: bool
    head_sha: str
    base_sha: str
    base_ref: str
    head_ref: str
    html_url: str
    changed_files_count: int = 0
    changed_files_truncated: bool = False
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    updated_at: datetime | None = None
    last_synced_at: datetime | None = None
    repository_id: int
    repository_full_name: str
    default_branch: str
    repository_active: bool
    current_run_id: str | None = None
    runs: list[ReviewRunSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Review runs
# ---------------------------------------------------------------------------


class ChangedFileSummary(BaseModel):
    id: int
    file_path: str
    previous_path: str | None = None
    change_status: str
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    has_patch: bool = False
    language: str | None = None
    is_analysed: bool = False
    skip_reason: str | None = None
    parse_ok: bool | None = None
    declarations_touched: int | None = None
    validated_findings: int = 0


class ReviewRunDetailResponse(BaseModel):
    id: str
    status: str
    trigger: str
    attempt: int
    head_sha: str
    base_sha: str
    is_stale: bool = False
    error_code: str | None = None
    error_detail: str | None = None
    """Redacted and length-capped before it reaches this field. It is rendered
    in a browser and an unredacted provider error can carry a URL with a key."""

    provider: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    ai_latency_ms: int | None = None
    model_output_truncated: bool = False
    limited_coverage: bool = False
    files_changed: int = 0
    files_analysed: int = 0
    files_skipped: int = 0
    funnel: FunnelCounts = Field(default_factory=FunnelCounts)
    findings_persisted: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None
    github_review_id: int | None = None
    pull_request_id: int
    pull_request_number: int
    pull_request_title: str
    pull_request_url: str
    repository_id: int
    repository_full_name: str
    changed_files: list[ChangedFileSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class FindingSummary(BaseModel):
    id: str
    severity: str
    category: str
    title: str
    status: str
    mapping_status: str
    validation: str
    """``validated`` or ``rejected`` — derived from ``rejection_reason``, which
    is the only honest discriminator the table carries."""

    rejection_reason: str | None = None
    file_path: str
    claimed_line: int
    resolved_line: int | None = None
    anchor_offset: int | None = None
    anchor_verified: bool = False
    side: str = "RIGHT"
    qualified_name: str = ""
    model_confidence: float | None = None
    effective_confidence: float | None = None
    cwe_id: str | None = None
    is_published: bool = False
    """True iff a ``review_comments`` row exists — evidence that GitHub
    accepted the comment. Never inferred from our own LineMap."""

    is_stale: bool = False
    created_at: datetime | None = None
    review_run_id: str
    pull_request_id: int
    pull_request_number: int
    repository_id: int
    repository_full_name: str
    head_sha: str


class FindingEventEntry(BaseModel):
    id: int
    from_status: str
    to_status: str
    source: str
    note: str | None = None
    created_at: datetime | None = None


class SourceContextLine(BaseModel):
    line: int
    text: str
    is_anchor: bool = False


class FindingDetailResponse(BaseModel):
    id: str
    severity: str
    category: str
    title: str
    description: str
    impact: str = ""
    suggested_fix: str | None = None
    rationale: str | None = None
    cwe_id: str | None = None
    status: str
    mapping_status: str
    validation: str
    rejection_reason: str | None = None

    file_path: str
    claimed_line: int
    claimed_line_end: int | None = None
    resolved_line: int | None = None
    anchor_offset: int | None = None
    anchor_snippet: str = ""
    anchor_verified: bool = False
    side: str = "RIGHT"
    qualified_name: str = ""
    declaration_id: str | None = None
    fingerprint: str

    model_confidence: float | None = None
    effective_confidence: float | None = None
    model_finding_id: str = ""
    model_name: str | None = None
    prompt_version: str | None = None

    head_sha: str
    is_stale: bool = False
    review_run_id: str
    review_run_status: str
    review_run_attempt: int
    repository_id: int
    repository_full_name: str
    pull_request_id: int
    pull_request_number: int
    pull_request_url: str

    is_published: bool = False
    published_comment_url: str | None = None
    published_at: datetime | None = None
    published_line: int | None = None
    published_side: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    events: list[FindingEventEntry] = Field(default_factory=list)
    source_context: list[SourceContextLine] = Field(default_factory=list)
    """The one stored line the finding is anchored to, at most.

    Phase 3 deliberately does not persist repository source (§16.7), so the
    only source text the API can show is the single ``anchor_snippet`` the
    finding already carries. The dashboard therefore shows *that line*, not a
    file window — and says so, rather than implying the rest was withheld.
    """


# ---------------------------------------------------------------------------
# Integration and settings
# ---------------------------------------------------------------------------


class InstallationSummary(BaseModel):
    id: int
    github_installation_id: int
    account_login: str
    account_type: str
    repository_selection: str | None = None
    suspended: bool = False
    uninstalled: bool = False
    repository_count: int = 0
    active_repository_count: int = 0
    last_event_at: datetime | None = None
    failed_deliveries: int = 0


class IntegrationStatusResponse(BaseModel):
    github_app_configured: bool
    webhook_path: str
    installations: list[InstallationSummary] = Field(default_factory=list)


class SettingsResponse(BaseModel):
    """Non-secret configuration, for the settings page.

    Every field is either a literal with no confidentiality or a **boolean
    about** a secret. No key, token or secret value appears here, and there is
    no write endpoint: this build has no secure secret management, so offering
    to edit credentials from a browser would be the wrong feature to add.
    """

    environment: str
    app_version: str
    github_app_configured: bool
    github_webhook_secret_configured: bool
    ai_provider: str
    ai_model: str | None = None
    ai_provider_configured: bool
    ai_provider_implemented: bool
    prompt_version: str
    review_publishing_enabled: bool
    dashboard_authenticated: bool = False
    dashboard_unauthenticated_reads_enabled: bool = False
    max_inline_comments: int
    min_confidence: float
    min_confidence_high_severity: float
    max_files_per_review: int
    max_findings_per_review: int


class RecentActivityEntry(BaseModel):
    id: str
    status: str
    attempt: int
    head_sha: str
    is_stale: bool = False
    limited_coverage: bool = False
    error_code: str | None = None
    findings_inline: int = 0
    rejected_claims: int = 0
    duration_ms: int | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    pull_request_id: int
    pull_request_number: int
    title: str
    repository_id: int
    repository_full_name: str


def as_int(value: Any, default: int = 0) -> int:
    return default if value is None else int(value)


def as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def as_bool(value: Any) -> bool:
    return bool(value)


def validation_of(rejection_reason: Any) -> str:
    """The single place the validated/rejected distinction is derived."""
    return "rejected" if rejection_reason else "validated"


def anchor_context(*, anchor_snippet: Any, resolved_line: Any) -> list[SourceContextLine]:
    """The one stored line a finding is anchored to, when there is one.

    Phase 3 deliberately does not persist repository source (architecture.md
    §16.7 — storing customer code creates a data-protection obligation with no
    upside), so the only source text this API *can* return is the single
    ``anchor_snippet`` already on the finding. One line is the honest maximum,
    and the UI says so rather than implying a window was withheld.

    The number is the **resolved** line, because that is the line the snippet
    was actually verified against. Returning the claimed line would label our
    verified text with the model's unverified coordinate.
    """
    snippet = (anchor_snippet or "").strip()
    if not snippet or resolved_line is None:
        return []
    return [SourceContextLine(line=int(resolved_line), text=snippet, is_anchor=True)]


__all__ = [
    "AnchorCounts",
    "ChangedFileSummary",
    "CountBucket",
    "CoverageResponse",
    "FindingDetailResponse",
    "FindingEventEntry",
    "FindingSummary",
    "FunnelCounts",
    "InstallationSummary",
    "IntegrationStatusResponse",
    "OverviewResponse",
    "Page",
    "ProcessingHealthResponse",
    "PullRequestDetailResponse",
    "PullRequestSummary",
    "RecentActivityEntry",
    "RepositoryDetailResponse",
    "RepositorySummary",
    "ReviewRunDetailResponse",
    "ReviewRunSummary",
    "RunCounts",
    "SettingsResponse",
    "SourceContextLine",
    "TrendPoint",
    "TrendResponse",
    "anchor_context",
    "as_bool",
    "as_float",
    "as_int",
    "validation_of",
]
