"""The delivery ledger — the idempotency boundary of the whole system.

``github_delivery_id`` carries a UNIQUE constraint, and that constraint is the
idempotency mechanism. It is enforced by the storage engine, so two concurrent
deliveries of the same event cannot both pass; an application-level
``if exists()`` check would be a race that two workers can both win
(architecture.md §23.1).

**The full payload is not stored.** It contains PR titles, bodies and branch
names — third-party content with no retention value once the extracted fields
are persisted. ``payload_digest`` proves "we saw exactly these bytes" for audit
without retaining them (architecture.md §16.10).

Retry bookkeeping deliberately lives on ``jobs``, not here: a delivery is
received exactly once, whereas the *work* it triggers is what gets retried.
Duplicating an attempt counter across both tables would create two sources of
truth for one number.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PROCESSING_STATUSES = (
    "received",
    "queued",
    "processed",
    "ignored",
    "coalesced",
    "failed",
)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN ('received','queued','processed','ignored','coalesced','failed')",
            name="processing_status_valid",
        ),
        Index("ix_webhook_events_github_installation_id", "github_installation_id"),
        Index("ix_webhook_events_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    github_delivery_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    """GitHub's ``X-GitHub-Delivery``. The idempotency key."""

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)

    github_installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_repo_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    repository_full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    github_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    processing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    ignored_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Why an event was acknowledged but not queued — e.g. ``unsupported_event``.

    Recorded rather than dropped silently, so "why didn't anything happen?" has
    an answer in the database.
    """

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
