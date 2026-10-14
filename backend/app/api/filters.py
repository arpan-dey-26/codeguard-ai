"""Typed, allow-listed query filters.

Framework-free, like ``app.api.pagination``, and for the same reason: this is
where a bad value turns into a bad query, so it must be testable on its own.

**Every filter is an allow-list, never a pass-through.** Architecture.md §17.2
rules out a generic query DSL, and this module is what makes that concrete: a
caller may say `severity=critical`, and may not say anything the enum does not
contain. The value that reaches SQL is therefore always one of a fixed set of
literals this repository wrote, so a filter cannot become an injection vector
even before the parameter binding that also prevents it.

**Unknown values are rejected, not ignored.** Silently dropping
`severity=criticl` returns *every* severity and the caller believes they
filtered. A 422 naming the field is the only honest answer.

**Time ranges are half-open and explicit.** `[from, to)` with an inclusive
start and exclusive end, so consecutive buckets never double-count the instant
on their boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.errors import PermanentInputError

SEVERITIES = ("critical", "high", "medium", "low", "info")
CATEGORIES = ("bug", "security", "performance", "resource", "maintainability")
FINDING_STATUSES = ("open", "resolved", "dismissed", "false_positive")
MAPPING_STATUSES = ("inline", "summary", "dashboard_only")
RUN_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "partial_success",
    "failed",
    "superseded",
    "skipped",
)
PR_STATES = ("open", "closed", "merged")

VALIDATION_RESULTS = ("validated", "rejected")
"""The single honest discriminator for a finding row.

A row with ``rejection_reason IS NULL`` passed every structural gate; a row
with one did not. Deliberately **not** called "true positive" / "false
positive": a structural rejection means the claim failed a *structural* gate,
which is a statement about anchoring, not about whether the issue is real.
"""

PUBLICATION_STATES = ("published", "not_published")
"""Derived from ``review_comments`` rows — actual stored publication state.

Never derived from LineMap commentability. Our LineMap says which lines *we*
consider commentable; whether GitHub accepted a comment is a different fact,
and only a ``review_comments`` row is evidence of it.
"""

MAX_SEARCH_CHARS = 120
MAX_RANGE_DAYS = 400


class InvalidFilterError(PermanentInputError):
    """A filter value is outside its allow-list, or a range is impossible."""

    problem_type = "invalid-filter"
    title = "Invalid filter"


def one_of(value: str | None, allowed: Sequence[str], *, field_name: str) -> str | None:
    """Validate a single optional value against its allow-list."""
    if value is None or value == "":
        return None
    normalised = value.strip().lower()
    if normalised not in allowed:
        raise InvalidFilterError(f"{field_name} must be one of: {', '.join(allowed)}.")
    return normalised


def many_of(
    values: Iterable[str] | None, allowed: Sequence[str], *, field_name: str
) -> tuple[str, ...]:
    """Validate a repeated query parameter.

    Returns a **deduplicated, sorted** tuple. Sorting matters beyond tidiness:
    the filter set becomes part of a cache key and of a test's expected SQL, and
    `?severity=high&severity=critical` must not be a different query from
    `?severity=critical&severity=high`.
    """
    if not values:
        return ()
    seen: set[str] = set()
    for raw in values:
        candidate = (raw or "").strip().lower()
        if not candidate:
            continue
        if candidate not in allowed:
            raise InvalidFilterError(f"{field_name} must be one of: {', '.join(allowed)}.")
        seen.add(candidate)
    return tuple(sorted(seen))


def positive_int(value: int | None, *, field_name: str) -> int | None:
    if value is None:
        return None
    if value < 1:
        raise InvalidFilterError(f"{field_name} must be a positive integer.")
    return value


def search_text(value: str | None) -> str | None:
    """Normalise a free-text search term.

    Length-capped and stripped. The term is bound as a parameter to an
    `ILIKE` predicate with its wildcards escaped, so `%` and `_` typed by a
    user match themselves rather than becoming a full-table wildcard scan.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_SEARCH_CHARS:
        raise InvalidFilterError(f"q must be at most {MAX_SEARCH_CHARS} characters.")
    return trimmed


def escape_like(term: str) -> str:
    """Escape LIKE metacharacters so user text is matched literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A half-open UTC range: ``start <= t < end``.

    Both ends are always present and always UTC. A "range" with an open end is
    an invitation for two endpoints to disagree about whether today counts, and
    for a chart's last bucket to be a partial day presented as a whole one.
    """

    start: datetime
    end: datetime

    @property
    def days(self) -> int:
        return max(1, (self.end - self.start).days)

    def snapped(self, bucket: str) -> TimeRange:
        """Widen the range so both ends land on bucket boundaries.

        A time-series query generates one row per bucket starting at
        ``date_trunc(bucket, start)``. If the caller's start is 12:00 on a
        Tuesday, that first bucket begins before the range and contains only
        part of a day's data — a chart whose first column is silently a partial
        day, which reads as a dip that did not happen.

        Snapping makes the bucket count a function of the range alone, so the
        series length is predictable and every column covers the same span.
        Widening rather than narrowing keeps the caller's requested window
        fully inside the result.
        """
        start = _start_of_day(self.start)
        if bucket == "week":
            start -= timedelta(days=start.weekday())
        end = self.end
        if end != _start_of_day(end):
            end = _start_of_day(end) + timedelta(days=1)
        return TimeRange(start=start, end=end)

    def to_dict(self) -> dict[str, str]:
        return {"from": self.start.isoformat(), "to": self.end.isoformat()}


def parse_time_range(
    since: str | None = None,
    until: str | None = None,
    *,
    default_days: int = 30,
    now: datetime | None = None,
) -> TimeRange:
    """Build a validated half-open UTC range.

    ``end`` defaults to the start of *tomorrow* UTC, so the current day is
    included in full and the last bucket of a chart is not a partial day whose
    value drops every time the user reloads.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    default_end = _start_of_day(current) + timedelta(days=1)

    end = _parse_instant(until, field_name="to") or default_end
    start = _parse_instant(since, field_name="from") or (end - timedelta(days=default_days))

    if start >= end:
        raise InvalidFilterError("'from' must be earlier than 'to'.")
    if (end - start).days > MAX_RANGE_DAYS:
        raise InvalidFilterError(f"The range must not exceed {MAX_RANGE_DAYS} days.")
    return TimeRange(start=start, end=end)


def _parse_instant(value: str | None, *, field_name: str) -> datetime | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    # `fromisoformat` in 3.11+ accepts a trailing Z, but normalising first
    # keeps behaviour identical if the runtime floor ever moves back.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidFilterError(f"{field_name} must be an ISO-8601 timestamp.") from exc
    # A naive timestamp is *assumed UTC*, never local: the server's timezone is
    # a deployment accident and must never change what a query means.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


BUCKETS = ("day", "week")


def parse_bucket(value: str | None) -> str:
    return one_of(value, BUCKETS, field_name="bucket") or "day"


@dataclass(frozen=True, slots=True)
class FindingFilters:
    """Every filter the findings list accepts, already validated."""

    repository_id: int | None = None
    pull_request_id: int | None = None
    review_run_id: str | None = None
    severities: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    mapping_statuses: tuple[str, ...] = ()
    validation: str | None = None
    publication: str | None = None
    rejection_reason: str | None = None
    search: str | None = None
    time_range: TimeRange | None = None

    def describe(self) -> dict[str, object]:
        """A stable, loggable description. Contains no user-supplied SQL."""
        described: dict[str, object] = {
            "repository_id": self.repository_id,
            "pull_request_id": self.pull_request_id,
            "severities": list(self.severities),
            "categories": list(self.categories),
            "statuses": list(self.statuses),
            "mapping_statuses": list(self.mapping_statuses),
            "validation": self.validation,
            "publication": self.publication,
            "has_search": self.search is not None,
        }
        if self.time_range:
            described["range"] = self.time_range.to_dict()
        return described


@dataclass(frozen=True, slots=True)
class RunFilters:
    repository_id: int | None = None
    pull_request_id: int | None = None
    statuses: tuple[str, ...] = ()
    time_range: TimeRange | None = None
    include_superseded: bool = True
    """Superseded runs are shown in history by default and excluded from
    "current state" queries explicitly. Hiding them everywhere would erase the
    record that a review was started and overtaken."""


@dataclass(frozen=True, slots=True)
class PullRequestFilters:
    repository_id: int | None = None
    states: tuple[str, ...] = ()
    run_statuses: tuple[str, ...] = ()
    search: str | None = None
    time_range: TimeRange | None = None


@dataclass(frozen=True, slots=True)
class RepositoryFilters:
    search: str | None = None
    is_active: bool | None = None
    installation_id: int | None = None
    _unused: tuple[str, ...] = field(default=(), repr=False)


__all__ = [
    "BUCKETS",
    "CATEGORIES",
    "FINDING_STATUSES",
    "MAPPING_STATUSES",
    "MAX_RANGE_DAYS",
    "MAX_SEARCH_CHARS",
    "PR_STATES",
    "PUBLICATION_STATES",
    "RUN_STATUSES",
    "SEVERITIES",
    "VALIDATION_RESULTS",
    "FindingFilters",
    "InvalidFilterError",
    "PullRequestFilters",
    "RepositoryFilters",
    "RunFilters",
    "TimeRange",
    "escape_like",
    "many_of",
    "one_of",
    "parse_bucket",
    "parse_time_range",
    "positive_int",
    "search_text",
]
