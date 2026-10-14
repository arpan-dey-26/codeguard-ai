"""A pull request observed on a repository.

Two identities are kept because both are needed: ``github_pr_id`` is the
global id webhooks carry, and ``(repository_id, number)`` is what humans,
URLs and the GitHub API path use. Both are unique.

``head_sha`` is the anchor for everything downstream — the changed-file set,
the review run, and the commit a review comment attaches to. A PR row is
*updated in place* on every event; a new push changes ``head_sha`` rather than
creating a second row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

PR_STATES = ("open", "closed", "merged")


class PullRequest(TimestampMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_pull_requests_repository_id_number"),
        CheckConstraint("state IN ('open', 'closed', 'merged')", name="state_valid"),
        Index("ix_pull_requests_repository_id_updated_at", "repository_id", "updated_at"),
        # --- indexes created by migrations, declared here for parity -------
        # ``alembic/env.py`` sets ``compare_server_default=True`` and diffs
        # against ``Base.metadata``. An index that exists in the database but
        # not here is an index autogenerate proposes to DROP. Every index below
        # is created by a migration and is load-bearing for a dashboard query.
        # 0004: PR list across every repository.
        Index(
            "ix_pull_requests_updated_at_id",
            text("updated_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )

    github_pr_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Untrusted, attacker-influenceable text. Length-capped on write; never
    # rendered as HTML and never treated as instructions.
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")

    author_login: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    author_github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    base_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    head_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    html_url: Mapped[str] = mapped_column(Text, nullable=False, default="")

    changed_files_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_files_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """True when GitHub capped the files response.

    Recorded rather than ignored: a caller who does not know the file list was
    truncated will over-trust a later "clean" result.
    """

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
