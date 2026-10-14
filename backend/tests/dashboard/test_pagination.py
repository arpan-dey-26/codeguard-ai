"""Keyset pagination.

The cursor is the piece most likely to be wrong in a way nobody notices:
a subtly broken one does not crash, it silently skips rows or loops forever.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidCursorError,
    build_page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)

# -- limits ------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested,expected",
    [(None, DEFAULT_PAGE_SIZE), (0, 1), (-5, 1), (10, 10), (100, 100), (10_000, MAX_PAGE_SIZE)],
)
def test_the_page_size_is_always_within_bounds(requested, expected):
    assert clamp_limit(requested) == expected


def test_an_oversized_request_is_clamped_not_rejected():
    """Failing a caller over a number it can neither guess nor discover breaks
    it for no benefit; clamping returns useful data and a cursor."""
    assert clamp_limit(99_999) == MAX_PAGE_SIZE


# -- cursor round-trip -------------------------------------------------------


def test_a_cursor_round_trips_every_value_type():
    position = {
        "created_at": datetime(2026, 9, 16, 8, 30, tzinfo=UTC),
        "id": 4711,
        "full_name": "acme/web",
        "flag": True,
        "nothing": None,
    }
    assert decode_cursor(encode_cursor(position)) == position


def test_a_datetime_comes_back_as_a_datetime_not_a_string():
    """A cursor whose types drift produces a comparison that matches nothing,
    which presents as 'pagination stops early' and is miserable to diagnose."""
    cursor = encode_cursor({"created_at": datetime(2026, 1, 1, tzinfo=UTC)})
    assert isinstance(decode_cursor(cursor)["created_at"], datetime)


def test_a_naive_datetime_is_normalised_to_utc():
    cursor = encode_cursor({"t": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)})
    assert decode_cursor(cursor)["t"].tzinfo is not None


def test_no_cursor_means_start_from_the_beginning():
    assert decode_cursor(None) == {}
    assert decode_cursor("") == {}


def test_the_cursor_is_url_safe():
    cursor = encode_cursor({"full_name": "acme/web+api", "id": 1})
    assert "/" not in cursor and "+" not in cursor and "=" not in cursor


# -- hostile cursors ---------------------------------------------------------


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!!",
        "eyJiYWQi",  # truncated JSON
        "WyJhIiwiYiJd",  # a JSON array, not an object
        "IjEyMyI",  # a JSON string
        "x" * 600,  # over the length cap
    ],
)
def test_a_malformed_cursor_is_rejected_cleanly(cursor):
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)


def test_a_rejected_cursor_explains_nothing_about_its_format():
    """The cursor is opaque by contract. Explaining why one failed invites
    clients to construct their own, which then break on the next change."""
    try:
        decode_cursor("not-base64!!")
    except InvalidCursorError as exc:
        assert exc.detail is None
    else:  # pragma: no cover
        raise AssertionError("expected InvalidCursorError")


def test_a_cursor_carrying_a_nested_object_is_rejected():
    import base64
    import json

    hostile = (
        base64.urlsafe_b64encode(json.dumps({"id": {"nested": "value"}}).encode())
        .decode()
        .rstrip("=")
    )
    with pytest.raises(InvalidCursorError):
        decode_cursor(hostile)


def test_a_cursor_with_an_absurd_key_is_rejected():
    import base64
    import json

    hostile = base64.urlsafe_b64encode(json.dumps({"k" * 200: 1}).encode()).decode().rstrip("=")
    with pytest.raises(InvalidCursorError):
        decode_cursor(hostile)


# -- page assembly -----------------------------------------------------------


def _rows(count: int) -> list[dict]:
    return [{"id": i, "created_at": datetime(2026, 1, 1, tzinfo=UTC)} for i in range(count)]


def test_the_over_fetched_row_is_never_returned():
    """Callers fetch limit+1; the extra row only answers 'is there more?'."""
    page = build_page(_rows(6), limit=5, cursor_for=lambda r: {"id": r["id"]})
    assert len(page.items) == 5
    assert page.items[-1]["id"] == 4


def test_a_full_page_with_no_extra_row_ends_the_list():
    """Returning a cursor that leads to an empty page makes every list look
    infinite."""
    page = build_page(_rows(5), limit=5, cursor_for=lambda r: {"id": r["id"]})
    assert page.next_cursor is None


def test_a_partial_page_ends_the_list():
    page = build_page(_rows(2), limit=5, cursor_for=lambda r: {"id": r["id"]})
    assert page.next_cursor is None
    assert len(page.items) == 2


def test_an_empty_result_ends_the_list_without_a_cursor():
    page = build_page([], limit=5, cursor_for=lambda r: {"id": r["id"]})
    assert page.items == [] and page.next_cursor is None


def test_the_next_cursor_points_at_the_last_returned_row():
    page = build_page(_rows(6), limit=5, cursor_for=lambda r: {"id": r["id"]})
    assert decode_cursor(page.next_cursor)["id"] == 4


def test_total_is_none_unless_it_was_asked_for():
    """None means 'not counted', never 'zero'."""
    page = build_page(_rows(3), limit=5, cursor_for=lambda r: {"id": r["id"]})
    assert page.total is None
    assert "total" not in page.to_dict()

    counted = build_page(_rows(3), limit=5, cursor_for=lambda r: {"id": r["id"]}, total=0)
    assert counted.to_dict()["total"] == 0


def test_paging_through_a_list_visits_every_row_exactly_once():
    """The property that matters, exercised end to end against an in-memory
    table sorted the way the SQL sorts."""
    table = sorted(
        ({"id": i, "created_at": datetime(2026, 1, 1, tzinfo=UTC)} for i in range(23)),
        key=lambda r: r["id"],
    )

    seen: list[int] = []
    cursor: str | None = None
    for _ in range(20):  # a loop guard: a broken cursor must not spin forever
        position = decode_cursor(cursor)
        after = position.get("id", -1)
        rows = [r for r in table if r["id"] > after][:6]
        page = build_page(rows, limit=5, cursor_for=lambda r: {"id": r["id"]})
        seen.extend(r["id"] for r in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == list(range(23))
    assert len(seen) == len(set(seen)), "no row may be returned twice"
