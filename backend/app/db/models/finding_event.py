"""Audit trail for a finding's status.

**Why a table rather than a ``status_changed_at`` column:** "who marked this a
false positive, and when" is an audit question. A single column answers only
the last change, and destroys the previous answer each time it is written.
Three rows of history cost nothing and answer everything — including the one
analytic that matters most for a tool like this: the false-positive rate over
time, per repository, per category.

``actor_user_id`` is nullable and the ``users`` table arrives in Phase 4, so
the column is declared without a foreign key for now; Phase 4 adds the
constraint. ``source`` distinguishes a human decision from a system transition
without needing the user row to exist.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EVENT_SOURCES = ("user", "system")


class FindingEvent(Base):
    __tablename__ = "finding_events"
    __table_args__ = (
        CheckConstraint("source IN ('user','system')", name="source_valid"),
        Index("ix_finding_events_finding_id", "finding_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )

    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)

    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="system", server_default="system"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
