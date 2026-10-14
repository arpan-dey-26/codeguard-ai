"""Declarative base and shared column conventions.

No domain tables are defined in Phase 0 — the CodeGuard schema
(``installations``, ``repositories``, ``pull_requests``, ``review_runs``,
``changed_files``, ``findings``, ``review_comments``, ``webhook_events``,
``jobs``, ``finding_events``, ``processing_logs``) arrives in Phase 1 and
Phase 3 as described in architecture.md §16.

What Phase 0 fixes now, because retrofitting it later is painful:

*   A **constraint naming convention**.  Without one, PostgreSQL invents
    names like ``findings_repository_id_fkey1`` and Alembic autogenerate
    produces migrations that cannot reliably drop what they created.  Setting
    it before the first table is the only cheap moment to do it.
*   A ``TimestampMixin`` so every table gets ``created_at`` / ``updated_at``
    as ``TIMESTAMPTZ`` in UTC (architecture.md NFR-D3) without repetition.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic, Alembic-friendly constraint names.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the application."""

    metadata = metadata


class TimestampMixin:
    """``created_at`` / ``updated_at``, always timezone-aware, always UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
