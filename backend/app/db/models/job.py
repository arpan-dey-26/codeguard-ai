"""The durable job queue.

PostgreSQL *is* the queue (architecture.md §5.2). The decisive reason is not
that it avoids running Redis — it is that a job can be inserted in the **same
transaction** as the webhook that caused it. Two systems would require an
outbox pattern to achieve the same guarantee; one system gets it for free.

States are exactly those in architecture.md §16.11:

    queued     waiting, eligible once ``run_after`` passes
    running    claimed by a worker; ``locked_at`` / ``locked_by`` set
    completed  finished successfully
    failed     attempts exhausted, or a permanent error
    cancelled  superseded by a newer job for the same pull request
    dead       dead-lettered; never retried automatically, a human decides

``cancelled`` is not decoration: a developer pushing three commits in thirty
seconds produces three events, and superseding the older ones is what stops
three overlapping syncs of the same pull request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JOB_STATUSES = ("queued", "running", "completed", "failed", "cancelled", "dead")
ACTIVE_STATUSES = ("queued", "running")

JOB_TYPES = (
    "review_pull_request",
    "sync_pull_request",
    "sync_installation",
    "sync_repositories",
)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled','dead')",
            name="status_valid",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        # The claim query's index. Partial, so it covers only claimable rows:
        # it stays small, fits in cache, and is maintained only for the rows
        # that matter (architecture.md §16.11).
        Index(
            "ix_jobs_claimable",
            "priority",
            "run_after",
            postgresql_where=text("status = 'queued'"),
        ),
        # "At most one ACTIVE job per dedupe key" as a database invariant.
        # Partial, so historical completed jobs for the same pull request can
        # coexist — a full unique index would forbid ever re-syncing a PR.
        Index(
            "uq_jobs_dedupe_key_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_webhook_event_id", "webhook_event_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")

    # Ids only, never bulk content. The worker re-reads what it needs from the
    # database; duplicating a webhook payload here would store third-party
    # source metadata twice and let the two copies drift.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    webhook_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    """Traceability from a job back to the delivery that caused it.

    Intentionally *not* a foreign key: jobs are a generic mechanism and later
    job types (scheduled syncs, manual re-reviews) have no originating
    delivery. Keeping the coupling loose is what lets the queue stay generic.
    """

    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=100)
    """Lower runs sooner. 100 is the default band, leaving room either side."""

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    """Earliest eligible time. Powers both backoff and the synchronize debounce."""

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
