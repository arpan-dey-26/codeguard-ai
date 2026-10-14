"""A comment we actually posted to GitHub.

This table is the platform's record of what exists *on GitHub*, as distinct
from what exists in our database. The two are different systems and can
disagree — a POST that timed out may or may not have created a comment — so
"we decided to comment" (``findings.mapping_status = 'inline'``) and "a comment
exists" (a row here) are deliberately separate facts.

The row is written **after** GitHub confirms, carrying the ids GitHub returned.
That ordering is what makes republication reconcilable: on a retry we read
GitHub's own comment list, match it against these rows, and post only what is
genuinely missing.

``body_hash`` rather than the body itself: it answers "would re-posting be
identical?" without storing a second copy of text that already lives in
``findings`` (architecture.md §16.9).
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
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewComment(Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        UniqueConstraint("finding_id", name="uq_review_comments_finding_id"),
        UniqueConstraint("github_comment_id", name="uq_review_comments_github_comment_id"),
        CheckConstraint("side IN ('LEFT','RIGHT')", name="side_valid"),
        Index("ix_review_comments_review_run_id", "review_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    finding_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    """Unique: one finding produces at most one comment.

    A database-level guarantee rather than an application convention, because
    the failure it prevents — the same finding posted twice after a partial
    failure — is exactly the kind that appears only under retry.
    """

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )

    github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    """Nullable because GitHub's create-review response does not always
    enumerate the comments it created. Absence means "we do not know the id",
    never "no comment exists" — reconciliation reads the live list rather than
    inferring from a NULL."""

    path: Mapped[str] = mapped_column(Text, nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    side: Mapped[str] = mapped_column(
        String(8), nullable=False, default="RIGHT", server_default="RIGHT"
    )

    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    """The commit the comment is anchored to.

    Stored because a comment is only meaningful relative to a snapshot: "line
    47 of session.py" means nothing without knowing which commit.
    """

    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    html_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
