"""One model claim, persisted whether or not it was published.

**Every claim that passes schema validation gets a row, including the ones the
validator rejected.** This is the central persistence decision of Phase 3 and
it looks wrong at first glance — why store something we refused to publish?

Because the rejections are the measurement. "The model's line number was exact
in 71% of findings and within ±3 lines in 89%" is only answerable if the misses
were recorded. Keeping only the survivors would leave the platform unable to
say anything about its own accuracy, which is precisely the question a code
review tool must be able to answer about itself.

Three columns exist purely to preserve that measurement:

``claimed_line_start``   what the model said.
``resolved_line``        what we determined, or NULL if we could not.
``anchor_offset``        the distance between them.

``resolved_line`` never overwrites ``claimed_line_start``. Overwriting would
destroy the only record of how wrong the model was.

``mapping_status`` decides where a finding surfaces:

``inline``          verified; becomes a GitHub inline review comment.
``summary``         real but unanchorable; listed in the review body.
``dashboard_only``  rejected; visible in the dashboard, never on GitHub.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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

FINDING_CATEGORIES = ("bug", "security", "performance", "resource", "maintainability")
FINDING_SEVERITIES = ("critical", "high", "medium", "low", "info")
MAPPING_STATUSES = ("inline", "summary", "dashboard_only")
FINDING_STATUSES = ("open", "resolved", "dismissed", "false_positive")


class Finding(TimestampMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(
            "category IN ('bug','security','performance','resource','maintainability')",
            name="category_valid",
        ),
        CheckConstraint(
            "severity IN ('critical','high','medium','low','info')",
            name="severity_valid",
        ),
        CheckConstraint(
            "mapping_status IN ('inline','summary','dashboard_only')",
            name="mapping_status_valid",
        ),
        CheckConstraint(
            "status IN ('open','resolved','dismissed','false_positive')",
            name="status_valid",
        ),
        CheckConstraint("side IN ('LEFT','RIGHT')", name="side_valid"),
        CheckConstraint(
            "model_confidence >= 0 AND model_confidence <= 1",
            name="model_confidence_range",
        ),
        CheckConstraint(
            "effective_confidence >= 0 AND effective_confidence <= 1",
            name="effective_confidence_range",
        ),
        CheckConstraint(
            r"cwe_id IS NULL OR cwe_id ~ '^CWE-[0-9]{1,4}$'",
            name="cwe_id_format",
        ),
        # An inline finding must have an authoritative line. Enforced in the
        # database rather than only in Python: this is the invariant that
        # keeps an unanchored finding from reaching GitHub, and a single
        # forgotten branch in application code should not be able to break it.
        CheckConstraint(
            "mapping_status <> 'inline' OR (resolved_line IS NOT NULL AND anchor_verified)",
            name="inline_requires_verified_anchor",
        ),
        # Makes re-persisting a run idempotent. A worker that persisted its
        # findings and then crashed before publication retries the whole step;
        # without this the retry would duplicate every row. With it, the
        # insert is ON CONFLICT DO NOTHING and the retry is free.
        UniqueConstraint(
            "review_run_id", "fingerprint", name="uq_findings_review_run_id_fingerprint"
        ),
        Index(
            "ix_findings_repository_id_severity_created_at",
            "repository_id",
            "severity",
            "created_at",
        ),
        Index(
            "ix_findings_repository_id_status_severity",
            "repository_id",
            "status",
            "severity",
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_findings_pull_request_id_fingerprint", "pull_request_id", "fingerprint"),
        Index("ix_findings_review_run_id", "review_run_id"),
        Index("ix_findings_repository_id_created_at", "repository_id", "created_at"),
        # --- indexes created by migrations, declared here for parity -------
        # ``alembic/env.py`` sets ``compare_server_default=True`` and diffs
        # against ``Base.metadata``. An index that exists in the database but
        # not here is an index autogenerate proposes to DROP. Every index below
        # is created by a migration and is load-bearing for a dashboard query.
        # 0004: findings explorer, default order, no repository filter.
        Index("ix_findings_created_at_id", text("created_at DESC"), text("id DESC")),
        # 0004: rejection breakdown and the validation=rejected filter.
        Index(
            "ix_findings_rejected_reason",
            "rejection_reason",
            postgresql_where=text("rejection_reason IS NOT NULL"),
        ),
        # 0005: FK child column, ON DELETE SET NULL.
        Index(
            "ix_findings_changed_file_id",
            "changed_file_id",
            postgresql_where=text("changed_file_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    changed_file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("changed_files.id", ondelete="SET NULL"), nullable=True
    )

    model_finding_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    """The id the model gave this finding. Provenance only.

    Never used as an identity key: it is regenerated on every response, so two
    runs over identical code would produce different values for the same issue.
    ``fingerprint`` is the identity.
    """

    category: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    cwe_id: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # -- location: claimed vs resolved -------------------------------------
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    claimed_line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anchor_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """``resolved_line - claimed_line_start``. NULL when unresolved.

    Zero means the model was exactly right. This column is the dataset behind
    the accuracy claim the project can actually defend.
    """

    side: Mapped[str] = mapped_column(
        String(8), nullable=False, default="RIGHT", server_default="RIGHT"
    )

    anchor_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    anchor_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    model_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    effective_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    """Confidence after our own adjustment.

    Kept separate from ``model_confidence`` for the same reason
    ``resolved_line`` is kept separate from ``claimed_line_start``: conflating
    the model's claim with our judgement destroys the ability to evaluate
    either one.
    """

    mapping_status: Mapped[str] = mapped_column(String(20), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    declaration_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
