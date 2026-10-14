"""Phase 1 — GitHub ingress, synchronisation entities and the durable job queue.

Revision ID: 0002_phase1_github_ingress
Revises: 0001_baseline
Create Date: 2026-09-15

Creates six tables:

    installations      the unit of auth, authorisation and rate limiting
    repositories       one installation's repositories
    pull_requests      pull requests observed
    changed_files      the changed-file set at one commit
    webhook_events     the delivery ledger — the idempotency boundary
    jobs               the durable queue

Three constraints carry the architectural weight and are worth naming:

*   ``webhook_events.github_delivery_id`` UNIQUE — makes duplicate-delivery
    suppression a storage-engine guarantee rather than an application race.
*   ``uq_jobs_dedupe_key_active`` — a PARTIAL unique index that enforces "at
    most one active job per key" while still allowing historical completed
    jobs for the same pull request.
*   ``ix_jobs_claimable`` — a PARTIAL index covering only ``status='queued'``,
    which is the only set the claim query ever scans.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase1_github_ingress"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- installations ------------------------------------------------------
    op.create_table(
        "installations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("account_github_id", sa.BigInteger(), nullable=True),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("repository_selection", sa.String(length=16), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "events",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uninstalled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "account_type IN ('User', 'Organization')",
            name=op.f("ck_installations_account_type_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installations")),
        sa.UniqueConstraint(
            "github_installation_id", name=op.f("uq_installations_github_installation_id")
        ),
    )
    op.create_index(
        op.f("ix_installations_github_installation_id"),
        "installations",
        ["github_installation_id"],
    )

    # -- repositories -------------------------------------------------------
    op.create_table(
        "repositories",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_login", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=512), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column("primary_language", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # RESTRICT, not CASCADE: uninstalling is a soft delete and a repository
        # must never be orphaned by one.
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name=op.f("fk_repositories_installation_id_installations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("github_repo_id", name=op.f("uq_repositories_github_repo_id")),
        sa.UniqueConstraint("full_name", name=op.f("uq_repositories_full_name")),
    )
    op.create_index("ix_repositories_installation_id", "repositories", ["installation_id"])
    op.create_index(
        op.f("ix_repositories_github_repo_id"), "repositories", ["github_repo_id"]
    )

    # -- pull_requests ------------------------------------------------------
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("github_pr_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author_login", sa.String(length=255), nullable=False),
        sa.Column("author_github_id", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("base_ref", sa.String(length=255), nullable=False),
        sa.Column("head_ref", sa.String(length=255), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("html_url", sa.Text(), nullable=False),
        sa.Column("changed_files_count", sa.Integer(), nullable=False),
        sa.Column("changed_files_truncated", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_sha", sa.String(length=40), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("github_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('open', 'closed', 'merged')", name=op.f("ck_pull_requests_state_valid")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_pull_requests_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pull_requests")),
        sa.UniqueConstraint("github_pr_id", name=op.f("uq_pull_requests_github_pr_id")),
        sa.UniqueConstraint(
            "repository_id", "number", name="uq_pull_requests_repository_id_number"
        ),
    )
    op.create_index(
        "ix_pull_requests_repository_id_updated_at",
        "pull_requests",
        ["repository_id", "updated_at"],
    )

    # -- changed_files ------------------------------------------------------
    op.create_table(
        "changed_files",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("previous_path", sa.Text(), nullable=True),
        sa.Column("change_status", sa.String(length=16), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        sa.Column("changes", sa.Integer(), nullable=False),
        sa.Column("has_patch", sa.Boolean(), nullable=False),
        sa.Column("blob_sha", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "change_status IN ('added','removed','modified','renamed','copied','changed','unchanged')",
            name=op.f("ck_changed_files_change_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["pull_request_id"],
            ["pull_requests.id"],
            name=op.f("fk_changed_files_pull_request_id_pull_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_changed_files")),
        sa.UniqueConstraint(
            "pull_request_id",
            "head_sha",
            "file_path",
            name="uq_changed_files_pull_request_id_head_sha_file_path",
        ),
    )
    op.create_index(
        "ix_changed_files_pull_request_id_head_sha",
        "changed_files",
        ["pull_request_id", "head_sha"],
    )

    # -- webhook_events -----------------------------------------------------
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("github_delivery_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=True),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=True),
        sa.Column("repository_full_name", sa.String(length=512), nullable=True),
        sa.Column("github_pr_number", sa.Integer(), nullable=True),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("processing_status", sa.String(length=16), nullable=False),
        sa.Column("ignored_reason", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "processing_status IN ('received','queued','processed','ignored','failed')",
            name=op.f("ck_webhook_events_processing_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        # THE idempotency constraint.
        sa.UniqueConstraint(
            "github_delivery_id", name=op.f("uq_webhook_events_github_delivery_id")
        ),
    )
    op.create_index(
        "ix_webhook_events_github_installation_id",
        "webhook_events",
        ["github_installation_id"],
    )
    op.create_index("ix_webhook_events_received_at", "webhook_events", ["received_at"])

    # -- jobs ---------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("webhook_event_id", sa.BigInteger(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_type", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled','dead')",
            name=op.f("ck_jobs_status_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_jobs_attempts_non_negative")),
        sa.CheckConstraint("max_attempts >= 1", name=op.f("ck_jobs_max_attempts_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    # Partial index over claimable rows only — the claim query's whole scan set.
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["priority", "run_after"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    # "At most one ACTIVE job per dedupe key", as a database invariant.
    op.create_index(
        "uq_jobs_dedupe_key_active",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"])
    op.create_index("ix_jobs_webhook_event_id", "jobs", ["webhook_event_id"])


def downgrade() -> None:
    # Reverse creation order so foreign keys never block a drop.
    op.drop_index("ix_jobs_webhook_event_id", table_name="jobs")
    op.drop_index("ix_jobs_status_run_after", table_name="jobs")
    op.drop_index("uq_jobs_dedupe_key_active", table_name="jobs")
    op.drop_index("ix_jobs_claimable", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_webhook_events_received_at", table_name="webhook_events")
    op.drop_index("ix_webhook_events_github_installation_id", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index("ix_changed_files_pull_request_id_head_sha", table_name="changed_files")
    op.drop_table("changed_files")

    op.drop_index("ix_pull_requests_repository_id_updated_at", table_name="pull_requests")
    op.drop_table("pull_requests")

    op.drop_index(op.f("ix_repositories_github_repo_id"), table_name="repositories")
    op.drop_index("ix_repositories_installation_id", table_name="repositories")
    op.drop_table("repositories")

    op.drop_index(
        op.f("ix_installations_github_installation_id"), table_name="installations"
    )
    op.drop_table("installations")
