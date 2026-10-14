"""Phase 4 — indexes for the dashboard's list and analytics queries.

Revision ID: 0004_dashboard_indexes
Revises: 0003_phase3_review_entities
Create Date: 2026-09-16

**Four indexes, each tied to one query that exists.** Every index is a write
cost paid on every insert and a page that has to stay in cache, so "it might
help one day" is not a reason to create one. Phase 3 already provides most of
what the dashboard needs, and that was checked before adding anything:

| Dashboard need | Already served by |
|---|---|
| repositories ordered by name | ``uq_repositories_full_name`` |
| scope → repositories by installation | ``ix_repositories_installation_id`` |
| the current run for a PR | ``uq_review_runs_pull_request_id_head_sha_attempt`` |
| a run's changed files | ``ix_changed_files_pull_request_id_head_sha`` |
| open findings by severity | ``ix_findings_repository_id_status_severity`` (partial) |
| findings for one repository over time | ``ix_findings_repository_id_created_at`` |
| runs for one repository by status | ``ix_review_runs_repository_id_status_created_at`` |

What is genuinely missing is the **unfiltered, time-ordered list** — the
default state of every explorer screen, where there is no repository predicate
to lead with. Phase 3's indexes are all repository-first, so without these the
planner sorts the whole table to return twenty-five rows.

The fourth is partial. The rejection-reason breakdown and the
``validation=rejected`` filter only ever touch rows that *have* a rejection
reason, and validated rows are the majority. A partial index is smaller, stays
in cache, and is not maintained for the rows it will never return — the same
argument as the ``status = 'open'`` partial index in migration 0003.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_dashboard_indexes"
down_revision: str | None = "0003_phase3_review_entities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Findings explorer, default ordering, no repository filter.
    # Matches: ORDER BY f.created_at DESC, f.id DESC  +  keyset
    # `(created_at, id) < (:cursor_created_at, :cursor_id)`.
    op.create_index(
        "ix_findings_created_at_id",
        "findings",
        [sa.text("created_at DESC"), sa.text("id DESC")],
    )

    # Review-run list and the dashboard's recent-activity panel.
    op.create_index(
        "ix_review_runs_created_at_id",
        "review_runs",
        [sa.text("created_at DESC"), sa.text("id DESC")],
    )

    # Pull-request list across all repositories.
    op.create_index(
        "ix_pull_requests_updated_at_id",
        "pull_requests",
        [sa.text("updated_at DESC"), sa.text("id DESC")],
    )

    # Rejection-reason breakdown and the `validation=rejected` filter.
    # PARTIAL: validated rows have a NULL rejection_reason and are never
    # returned by either query, so they are not worth indexing.
    op.create_index(
        "ix_findings_rejected_reason",
        "findings",
        ["repository_id", "rejection_reason", sa.text("created_at DESC")],
        postgresql_where=sa.text("rejection_reason IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_findings_rejected_reason", table_name="findings")
    op.drop_index("ix_pull_requests_updated_at_id", table_name="pull_requests")
    op.drop_index("ix_review_runs_created_at_id", table_name="review_runs")
    op.drop_index("ix_findings_created_at_id", table_name="findings")
