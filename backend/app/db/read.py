"""Executing a read query.

The dashboard's query builders are pure — they return SQL and parameters and
touch no session (see ``app.db.repositories.dashboard``). This module is the
other half: the only place those queries meet a database connection.

Three things happen here that must happen in exactly one place.

**Parameters are bound, never interpolated.** ``session.execute(text(sql),
params)`` hands the values to the driver as parameters. Nothing in the
dashboard path builds SQL by formatting a value into a string, and keeping
execution in one function is what makes that checkable.

**The session's time zone is pinned to UTC.** ``date_trunc`` on a
``timestamptz`` resolves in the session's ``TimeZone``, so a server whose
locale differs would silently bucket a time series by local midnight. The
bucket boundaries of a chart must not depend on where the container runs.

**Rows come back as plain dictionaries.** Routes then map them field by field
into response models. Returning ORM objects would make it one careless
``model_validate`` away from serialising a column nobody meant to expose.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.dashboard import Query

_UTC_GUARD = text("SET LOCAL TimeZone TO 'UTC'")


async def fetch_all(session: AsyncSession, query: Query) -> list[dict[str, Any]]:
    """Run a read query and return every row as a dictionary."""
    await session.execute(_UTC_GUARD)
    result = await session.execute(text(query.sql), query.params)
    return [dict(row) for row in result.mappings().all()]


async def fetch_one(session: AsyncSession, query: Query) -> dict[str, Any] | None:
    """Run a read query expected to return at most one row.

    ``None`` means "no row", which for a detail endpoint means 404 — including
    when the row exists but sits outside the caller's scope, because the scope
    clause is part of the query. Architecture.md §17.7: a 403 would confirm
    that the resource exists.
    """
    await session.execute(_UTC_GUARD)
    result = await session.execute(text(query.sql), query.params)
    row = result.mappings().first()
    return dict(row) if row else None


async def fetch_scalar(session: AsyncSession, query: Query) -> Any:
    await session.execute(_UTC_GUARD)
    result = await session.execute(text(query.sql), query.params)
    return result.scalar()


def counts_by_key(rows: list[dict[str, Any]], *, key: str, value: str = "count") -> dict[str, int]:
    """Turn ``[{severity: 'high', count: 3}]`` into ``{'high': 3}``.

    Grouped queries return only the keys that have rows. Callers that need a
    complete set — every severity, including the zeros — fill the gaps
    themselves against their own closed vocabulary, because this function has
    no way to know what "all of them" means.
    """
    return {str(row[key]): int(row[value]) for row in rows if row.get(key) is not None}


__all__ = ["counts_by_key", "fetch_all", "fetch_one", "fetch_scalar"]
