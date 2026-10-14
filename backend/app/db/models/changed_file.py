"""The set of files a pull request changes, at one commit.

**Deviation from architecture.md §16.7, recorded as ADR-0006.** The baseline
keys this table on ``review_run_id``, but ``review_runs`` is a Phase 3 table.
Rather than invent it early, Phase 1 keys the snapshot on
``(pull_request_id, head_sha, file_path)``.

That is arguably the better key regardless: the changed-file set is a property
of a *commit*, not of a review run. Keying it on ``head_sha`` means a re-run
over the same commit reuses the rows instead of re-fetching from GitHub, and
two runs over the same commit cannot disagree. Phase 3 adds a nullable
``review_run_id`` column; nothing here needs rewriting.

Phase 1 stores *metadata about* the diff and deliberately **not the patch
text**. The patch is reproducible from GitHub, it is the bulkiest data in the
system, and — decisively — it is third-party source code. Storing customer
source creates a data-protection obligation with no reason to take it on
(architecture.md §16.7). ``has_patch`` records only whether one was available.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# GitHub's documented vocabulary for a file's status in a pull request.
CHANGE_STATUSES = (
    "added",
    "removed",
    "modified",
    "renamed",
    "copied",
    "changed",
    "unchanged",
)


class ChangedFile(TimestampMixin, Base):
    __tablename__ = "changed_files"
    __table_args__ = (
        UniqueConstraint(
            "pull_request_id",
            "head_sha",
            "file_path",
            name="uq_changed_files_pull_request_id_head_sha_file_path",
        ),
        CheckConstraint(
            "change_status IN ('added','removed','modified','renamed','copied','changed','unchanged')",
            name="change_status_valid",
        ),
        Index("ix_changed_files_pull_request_id_head_sha", "pull_request_id", "head_sha"),
        # --- indexes created by migrations, declared here for parity -------
        # ``alembic/env.py`` sets ``compare_server_default=True`` and diffs
        # against ``Base.metadata``. An index that exists in the database but
        # not here is an index autogenerate proposes to DROP. Every index below
        # is created by a migration and is load-bearing for a dashboard query.
        # 0005: FK child column, ON DELETE SET NULL.
        Index(
            "ix_changed_files_review_run_id",
            "review_run_id",
            postgresql_where=text("review_run_id IS NOT NULL"),
        ),
        # 0005: coverage analytics — unanalysed files over a time range.
        Index(
            "ix_changed_files_created_at_unanalysed",
            text("created_at DESC"),
            postgresql_where=text("is_analysed = false"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    pull_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)

    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    previous_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Set only for renames, from GitHub's ``previous_filename``."""

    change_status: Mapped[str] = mapped_column(String(16), nullable=False)

    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    has_patch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether GitHub returned a unified-diff patch for this file.

    GitHub omits the patch for binary files and for diffs that are too large.
    Absence is not an error — it is a classification signal, and a file with no
    patch can never receive an inline comment because no line map can be built
    for it. Phase 2 consumes this flag; Phase 1 only records it.
    """

    blob_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    review_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("review_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The run that most recently analysed this file, as ADR-0006 promised.

    **Nullable, and SET NULL on delete, on purpose.** The row's identity is
    still ``(pull_request_id, head_sha, file_path)`` — the changed-file set is
    a property of a commit, not of a run. This column is a *back-reference*
    for the dashboard's per-run drill-down, not a key. Making it the key, as
    architecture.md §16.7 originally did, would mean re-fetching the file list
    from GitHub for every re-run of the same commit and would allow two runs
    over one commit to disagree about what changed.
    """

    # -- Phase 2 analysis outcome, recorded per commit ---------------------
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_analysed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    skip_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parse_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    declarations_touched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    patch_line_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
