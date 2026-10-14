"""One attempt to review one commit of one pull request.

The row exists *before* the model is called and is updated as the run
progresses, so a crashed worker leaves evidence rather than silence.

**The funnel counters are stored, not derived**, and that is a deliberate
choice worth defending. ``findings_emitted`` counts claims the model made,
most of which have no row anywhere: a finding rejected by the validator is
counted and discarded. There is no table to aggregate later. A number that
describes the state of the world at the time of the run, and cannot be
reconstructed from surviving rows, must be stored. A number that is a current
aggregate of rows that still exist must be computed (architecture.md §16.15).

``error_message`` is capped and redacted before it lands here. It is rendered
in the dashboard, and an unredacted provider error can carry a URL containing
an API key.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import uuid7
from app.db.base import Base, TimestampMixin

RUN_TRIGGERS = ("opened", "synchronize", "reopened", "ready_for_review", "manual")

RUN_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "partial_success",
    "failed",
    "superseded",
    "skipped",
)
"""``partial_success`` is a first-class outcome, not a euphemism for failure.

Findings were produced and persisted, but publication to GitHub did not fully
succeed. The user still sees everything in the dashboard (NFR-R5). Collapsing
this into ``failed`` would throw away work that was completed.
"""

RUN_ERROR_CODES = (
    "github_access_lost",
    "ai_unavailable",
    "ai_schema_invalid",
    "ai_truncated",
    "diff_unavailable",
    "publish_failed",
    "stale_head",
    "internal",
)
"""Closed set, so "why do reviews fail?" is a ``GROUP BY`` rather than a
regex over free text.

``publish_failed`` and ``stale_head`` extend the architecture's baseline list:
both are outcomes Phase 3 can actually produce, and folding them into
``internal`` would hide the two most operationally interesting failures.
"""


class ReviewRun(TimestampMixin, Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        UniqueConstraint(
            "pull_request_id",
            "head_sha",
            "attempt",
            name="uq_review_runs_pull_request_id_head_sha_attempt",
        ),
        CheckConstraint(
            "trigger IN ('opened','synchronize','reopened','ready_for_review','manual')",
            name="trigger_valid",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','partial_success',"
            "'failed','superseded','skipped')",
            name="status_valid",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('github_access_lost','ai_unavailable',"
            "'ai_schema_invalid','ai_truncated','diff_unavailable','publish_failed',"
            "'stale_head','internal')",
            name="error_code_valid",
        ),
        Index(
            "ix_review_runs_repository_id_status_created_at",
            "repository_id",
            "status",
            "created_at",
        ),
        Index("ix_review_runs_pull_request_id_created_at", "pull_request_id", "created_at"),
        # --- indexes created by migrations, declared here for parity -------
        # ``alembic/env.py`` sets ``compare_server_default=True`` and diffs
        # against ``Base.metadata``. An index that exists in the database but
        # not here is an index autogenerate proposes to DROP. Every index below
        # is created by a migration and is load-bearing for a dashboard query.
        # 0004: run list and the recent-activity panel.
        Index("ix_review_runs_created_at_id", text("created_at DESC"), text("id DESC")),
        # 0005: FK child column, ON DELETE SET NULL.
        Index(
            "ix_review_runs_webhook_event_id",
            "webhook_event_id",
            postgresql_where=text("webhook_event_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)

    pull_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    """Denormalised from ``pull_requests`` (architecture.md §15.5).

    Justified by Q9: "run success rate for repository R over a window" would
    otherwise need a join before it could apply its own filter, and no index
    can serve that combined predicate.
    """

    webhook_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("webhook_events.id", ondelete="SET NULL"), nullable=True
    )

    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default="")
    attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="queued", server_default="queued"
    )
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- provider accounting ------------------------------------------------
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """Which prompt produced these findings.

    Without it, comparing this month's false-positive rate against last
    month's is meaningless: the prompt is an input to every finding, and a
    changed prompt is a changed experiment.
    """

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    """True when the model hit its output-token cap.

    A truncated response is *usable* — the parsed prefix is real findings — but
    it is not complete, and a run that silently reported "3 findings" when the
    model was cut off mid-array would be lying by omission."""

    # -- file funnel --------------------------------------------------------
    files_changed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    files_analysed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    files_skipped: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    context_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    """True when a budget stopped the context build before every file fit."""

    # -- finding funnel (§13.8) --------------------------------------------
    findings_emitted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_persisted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_inline: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_summary: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_dashboard_only: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    findings_dropped: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    """The review GitHub created, if publication succeeded.

    Used for reconciliation: a retry reads back the comments of this review
    rather than assuming none exist.
    """

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
