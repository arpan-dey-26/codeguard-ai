"""Keyset pagination.

Framework-free on purpose: this is pure encode/decode plus validation, it is
the piece most likely to be wrong in a subtle way, and keeping FastAPI out of
it means the whole thing is testable without an ASGI server.

**Keyset, not offset** (architecture.md §17.2). `OFFSET 10000` makes PostgreSQL
walk and discard ten thousand rows, so page 200 costs two hundred times page 1;
worse, a row inserted while the user pages shifts every subsequent page by one
and an item is silently skipped. A keyset cursor carries the sort key of the
last row seen, so the next query is a range scan the index already serves and
concurrent inserts cannot make a row vanish.

**The cursor is a position, not a capability.** It is base64url of a small JSON
object — deliberately *not* signed, because it grants nothing: every value it
carries is re-validated on decode and then bound as a query *parameter*, never
interpolated. A forged cursor can only ask for a different position in a result
set the caller was already allowed to read. Signing it would imply a security
property it does not have, which is worse than not signing it.

**Total counts are optional and separate.** `COUNT(*)` over a filtered findings
table is its own sequential scan; paying for it on every page to render "of
1,284" is the classic way a list endpoint becomes the slowest thing in an
application. Endpoints that need it ask for it explicitly.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.errors import PermanentInputError

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
"""Hard ceiling, enforced server-side.

A client that asks for 10,000 rows gets 100. The limit exists to bound the
memory and serialisation cost of a single request, so honouring a client's
larger number would defeat the only reason it exists.
"""

_MAX_CURSOR_CHARS = 512


class InvalidCursorError(PermanentInputError):
    """The cursor is not one we issued, or no longer decodes.

    Rendered as 422. Deliberately carries no detail about *why*: the cursor is
    opaque by contract, and explaining its internals invites clients to
    construct their own.
    """

    problem_type = "invalid-cursor"
    title = "Invalid pagination cursor"


@dataclass(frozen=True, slots=True)
class Page:
    """One page of results plus the position to resume from."""

    items: list[Any]
    next_cursor: str | None = None
    total: int | None = None
    """Present only when the endpoint was asked for it. ``None`` means "not
    counted", never "zero"."""

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"items": self.items, "next_cursor": self.next_cursor}
        if self.total is not None:
            body["total"] = self.total
        return body


def clamp_limit(limit: int | None) -> int:
    """Return a page size that is always within bounds.

    Clamps rather than rejects. A client asking for 500 has made a reasonable
    request that we simply will not serve at that size; failing it would break
    a caller over a number it can neither guess nor discover, while clamping
    returns useful data and a cursor.
    """
    if limit is None:
        return DEFAULT_PAGE_SIZE
    if limit < 1:
        return 1
    return min(limit, MAX_PAGE_SIZE)


def encode_cursor(values: dict[str, Any]) -> str:
    """Encode a sort-key position as an opaque string."""
    payload = json.dumps(_encode_values(values), separators=(",", ":"), sort_keys=True)
    raw = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any]:
    """Decode a cursor, or raise ``InvalidCursorError``.

    Returns ``{}`` for ``None`` — "start from the beginning" is a valid
    request, not a missing value.
    """
    if not cursor:
        return {}
    if len(cursor) > _MAX_CURSOR_CHARS:
        # A length check before base64 decoding, for the same reason the
        # webhook body is size-checked before HMAC: refuse the oversized input
        # without spending work on it.
        raise InvalidCursorError()
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        decoded = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError() from exc
    if not isinstance(decoded, dict):
        raise InvalidCursorError()
    return _decode_values(decoded)


def _encode_values(values: dict[str, Any]) -> dict[str, Any]:
    """Make the position JSON-safe without losing type information.

    Datetimes become tagged strings rather than bare ISO text so that decoding
    restores a ``datetime`` rather than a string that happens to look like one.
    A cursor whose types drift between encode and decode produces a comparison
    that silently matches nothing, which presents as "pagination stops early"
    and is miserable to diagnose.
    """
    out: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            out[key] = {"__t": "dt", "v": value.astimezone(UTC).isoformat()}
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = {"__t": "s", "v": str(value)}
    return out


def _decode_values(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if not isinstance(key, str) or len(key) > 64:
            raise InvalidCursorError()
        if isinstance(value, dict):
            kind, raw = value.get("__t"), value.get("v")
            if kind == "dt" and isinstance(raw, str):
                try:
                    out[key] = datetime.fromisoformat(raw)
                except ValueError as exc:
                    raise InvalidCursorError() from exc
            elif kind == "s" and isinstance(raw, str):
                out[key] = raw
            else:
                raise InvalidCursorError()
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            raise InvalidCursorError()
    return out


def build_page(
    rows: list[Any],
    *,
    limit: int,
    cursor_for: Any,
    total: int | None = None,
) -> Page:
    """Trim an over-fetched row list into a page and derive the next cursor.

    Callers fetch ``limit + 1`` rows. The extra row is never returned; its only
    job is to answer "is there more?" without a second query and without a
    ``COUNT``. When it is absent, ``next_cursor`` is ``None`` and the client
    stops — which is why the extra row matters: returning a cursor that leads
    to an empty page makes every list look infinite.
    """
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = encode_cursor(cursor_for(page_rows[-1])) if has_more and page_rows else None
    return Page(items=page_rows, next_cursor=next_cursor, total=total)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "InvalidCursorError",
    "Page",
    "build_page",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
]
