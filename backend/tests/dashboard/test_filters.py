"""Filter validation.

Every test here is really the same test from a different angle: can a caller
get a value into a query that this repository did not write?
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.api.filters import (
    CATEGORIES,
    MAX_RANGE_DAYS,
    MAX_SEARCH_CHARS,
    RUN_STATUSES,
    SEVERITIES,
    InvalidFilterError,
    escape_like,
    many_of,
    one_of,
    parse_bucket,
    parse_time_range,
    positive_int,
    search_text,
)

NOW = datetime(2026, 9, 16, 15, 30, tzinfo=UTC)


# -- allow-lists -------------------------------------------------------------


@pytest.mark.parametrize("value", SEVERITIES)
def test_every_declared_severity_is_accepted(value):
    assert one_of(value, SEVERITIES, field_name="severity") == value


def test_case_and_whitespace_are_normalised():
    assert one_of("  CRITICAL ", SEVERITIES, field_name="severity") == "critical"


def test_an_unknown_value_is_rejected_rather_than_ignored():
    """Silently dropping `severity=criticl` returns every severity and the
    caller believes they filtered."""
    with pytest.raises(InvalidFilterError):
        one_of("criticl", SEVERITIES, field_name="severity")


def test_the_error_names_the_field_and_the_allowed_values():
    try:
        one_of("nope", CATEGORIES, field_name="category")
    except InvalidFilterError as exc:
        assert "category" in str(exc)
        assert "security" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected InvalidFilterError")


def test_an_absent_filter_is_not_an_error():
    assert one_of(None, SEVERITIES, field_name="severity") is None
    assert one_of("", SEVERITIES, field_name="severity") is None


@pytest.mark.parametrize(
    "hostile",
    [
        "critical'; DROP TABLE findings; --",
        "critical OR 1=1",
        "critical\x00",
        "critical UNION SELECT",
    ],
)
def test_an_injection_payload_never_survives_an_allow_list(hostile):
    with pytest.raises(InvalidFilterError):
        one_of(hostile, SEVERITIES, field_name="severity")


# -- repeated parameters -----------------------------------------------------


def test_repeated_values_are_deduplicated_and_sorted():
    """Sorting is not tidiness: `?severity=high&severity=critical` must be the
    same query as the reverse order."""
    assert many_of(["high", "critical", "high"], SEVERITIES, field_name="severity") == (
        "critical",
        "high",
    )


def test_one_bad_value_rejects_the_whole_set():
    with pytest.raises(InvalidFilterError):
        many_of(["high", "nonsense"], SEVERITIES, field_name="severity")


def test_no_values_means_no_filter():
    assert many_of(None, RUN_STATUSES, field_name="run_status") == ()
    assert many_of([], RUN_STATUSES, field_name="run_status") == ()
    assert many_of(["", "  "], RUN_STATUSES, field_name="run_status") == ()


# -- free text ---------------------------------------------------------------


def test_search_text_is_length_capped():
    with pytest.raises(InvalidFilterError):
        search_text("x" * (MAX_SEARCH_CHARS + 1))


def test_blank_search_is_no_search():
    assert search_text("   ") is None
    assert search_text(None) is None


@pytest.mark.parametrize(
    "term,expected",
    [
        ("100%", r"100\%"),
        ("a_b", r"a\_b"),
        ("back\\slash", "back\\\\slash"),
        ("plain", "plain"),
    ],
)
def test_like_metacharacters_are_escaped(term, expected):
    """A user typing `%` means a percent sign, not 'match everything' — which
    would also turn an indexed prefix match into a full scan."""
    assert escape_like(term) == expected


def test_a_search_term_is_kept_verbatim_for_binding():
    """No sanitising, no stripping of quotes: the term is bound as a
    parameter, so mangling it would only corrupt legitimate searches."""
    assert search_text("  it's a 'quote'  ") == "it's a 'quote'"


# -- time ranges -------------------------------------------------------------


def test_the_default_range_ends_at_the_start_of_tomorrow():
    """So today is included in full and a chart's last bucket is not a partial
    day whose value drops on every reload."""
    window = parse_time_range(default_days=7, now=NOW)
    assert window.end == datetime(2026, 9, 17, tzinfo=UTC)
    assert window.start == datetime(2026, 9, 10, tzinfo=UTC)
    assert window.days == 7


def test_an_explicit_range_is_honoured():
    window = parse_time_range("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", now=NOW)
    assert window.start == datetime(2026, 1, 1, tzinfo=UTC)
    assert window.end == datetime(2026, 2, 1, tzinfo=UTC)


def test_a_naive_timestamp_is_read_as_utc_not_as_server_local_time():
    """The server's timezone is a deployment accident and must never change
    what a query means."""
    window = parse_time_range("2026-01-01T00:00:00", "2026-01-02T00:00:00", now=NOW)
    assert window.start.tzinfo is UTC
    assert window.start.hour == 0


def test_an_offset_timestamp_is_converted_to_utc():
    window = parse_time_range("2026-01-01T05:00:00+05:00", "2026-01-02T00:00:00Z", now=NOW)
    assert window.start == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def test_an_inverted_range_is_rejected():
    with pytest.raises(InvalidFilterError):
        parse_time_range("2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z", now=NOW)


def test_an_absurd_range_is_rejected():
    with pytest.raises(InvalidFilterError):
        parse_time_range("2000-01-01T00:00:00Z", "2026-01-01T00:00:00Z", now=NOW)


def test_the_range_cap_is_stated_in_the_error():
    try:
        parse_time_range("2000-01-01T00:00:00Z", "2026-01-01T00:00:00Z", now=NOW)
    except InvalidFilterError as exc:
        assert str(MAX_RANGE_DAYS) in str(exc)


def test_a_malformed_timestamp_is_a_filter_error_not_a_crash():
    with pytest.raises(InvalidFilterError):
        parse_time_range("yesterday", None, now=NOW)


def test_the_range_is_half_open():
    """Adjacent ranges must not both contain the boundary instant."""
    first = parse_time_range("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", now=NOW)
    second = parse_time_range("2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z", now=NOW)
    assert first.end == second.start


# -- bucket snapping ---------------------------------------------------------


def test_snapping_aligns_a_mid_day_range_to_day_boundaries():
    """A series generated from an unaligned start has a first bucket that
    covers part of a day and reads as a dip that did not happen."""
    window = parse_time_range("2026-09-01T13:45:00Z", "2026-09-05T09:00:00Z", now=NOW)
    snapped = window.snapped("day")
    assert snapped.start == datetime(2026, 9, 1, tzinfo=UTC)
    assert snapped.end == datetime(2026, 9, 6, tzinfo=UTC)


def test_snapping_widens_and_never_narrows():
    window = parse_time_range("2026-09-01T13:45:00Z", "2026-09-05T09:00:00Z", now=NOW)
    snapped = window.snapped("day")
    assert snapped.start <= window.start and snapped.end >= window.end


def test_an_already_aligned_range_is_unchanged():
    window = parse_time_range("2026-09-01T00:00:00Z", "2026-09-05T00:00:00Z", now=NOW)
    assert window.snapped("day") == window


def test_week_snapping_starts_on_monday():
    # 2026-09-16 is a Wednesday.
    window = parse_time_range("2026-09-16T00:00:00Z", "2026-09-17T00:00:00Z", now=NOW)
    snapped = window.snapped("week")
    assert snapped.start.weekday() == 0


@pytest.mark.parametrize("value,expected", [(None, "day"), ("day", "day"), ("week", "week")])
def test_bucket_parsing(value, expected):
    assert parse_bucket(value) == expected


def test_an_unknown_bucket_is_rejected():
    with pytest.raises(InvalidFilterError):
        parse_bucket("fortnight")


def test_positive_int_rejects_zero_and_negatives():
    assert positive_int(None, field_name="repository_id") is None
    assert positive_int(3, field_name="repository_id") == 3
    for bad in (0, -1):
        with pytest.raises(InvalidFilterError):
            positive_int(bad, field_name="repository_id")
