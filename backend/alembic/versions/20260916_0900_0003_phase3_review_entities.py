"""Phase 3 — review runs, findings, posted comments and the finding audit trail.

Revision ID: 0003_phase3_review_entities
Revises: 0002_phase1_github_ingress
Create Date: 2026-09-16

Creates four tables and extends one:

    review_runs       one attempt to review one commit, with the funnel
    findings          every model claim, published or rejected
    review_comments   what actually exists on GitHub
    finding_events    status audit trail
    changed_files     + review_run_id and the Phase 2 analysis columns

Four constraints carry the architectural weight:

*   ``uq_review_runs_pull_request_id_head_sha_attempt`` — makes duplicate
    analysis of the same commit structurally impossible rather than merely
    unlikely.
*   ``ck_findings_inline_requires_verified_anchor`` — an inline finding must
    have a resolved line *and* a verified anchor. This is the "nothing
    unverified reaches GitHub" invariant expressed where a bug in application
    code cannot bypass it.
*   ``uq_findings_review_run_id_fingerprint`` — makes persisting a run's
    findings idempotent, so the persist step can be retried after a crash
    without duplicating rows.
*   ``uq_review_comments_finding_id`` — one finding, at most one comment,
    enforced by the database because the failure it prevents (a duplicate
    comment after a partial publication failure) appears only under retry.

The ``findings.ix_findings_repository_id_status_severity`` index is
**partial** (``WHERE status = 'open'``). The KPI tiles only ever count open
findings; a partial index is smaller, stays in cache, and is maintained only
for the rows that are actually queried.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase3_review_entities"
down_revision: str | None = "0002_phase1_github_ingress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- review_runs -------------------------------------------------------
    op.create_table(
        "review_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("webhook_event_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("ai_latency_ms", sa.Integer(), nullable=True),
        sa.Column("ai_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("files_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_analysed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("findings_emitted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_persisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_inline", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_summary", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_dashboard_only", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("github_review_id", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_runs"),
        sa.ForeignKeyConstraint(
            ["pull_request_id"],
            ["pull_requests.id"],
            name="fk_review_runs_pull_request_id_pull_requests",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_review_runs_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["webhook_event_id"],
            ["webhook_events.id"],
            name="fk_review_runs_webhook_event_id_webhook_events",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "pull_request_id",
            "head_sha",
            "attempt",
            name="uq_review_runs_pull_request_id_head_sha_attempt",
        ),
        sa.CheckConstraint(
            "trigger IN ('opened','synchronize','reopened','ready_for_review','manual')",
            name="ck_review_runs_trigger_valid",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','partial_success',"
            "'failed','superseded','skipped')",
            name="ck_review_runs_status_valid",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('github_access_lost','ai_unavailable',"
            "'ai_schema_invalid','ai_truncated','diff_unavailable','publish_failed',"
            "'stale_head','internal')",
            name="ck_review_runs_error_code_valid",
        ),
    )
    op.create_index(
        "ix_review_runs_repository_id_status_created_at",
        "review_runs",
        ["repository_id", "status", "created_at"],
    )
    op.create_index(
        "ix_review_runs_pull_request_id_created_at",
        "review_runs",
        ["pull_request_id", "created_at"],
    )

    # -- findings ----------------------------------------------------------
    op.create_table(
        "findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("changed_file_id", sa.BigInteger(), nullable=True),
        sa.Column("model_finding_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("cwe_id", sa.String(length=12), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("claimed_line_start", sa.Integer(), nullable=False),
        sa.Column("claimed_line_end", sa.Integer(), nullable=True),
        sa.Column("resolved_line", sa.Integer(), nullable=True),
        sa.Column("anchor_offset", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False, server_default="RIGHT"),
        sa.Column("anchor_snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("anchor_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("effective_confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("mapping_status", sa.String(length=20), nullable=False),
        sa.Column("rejection_reason", sa.String(length=40), nullable=True),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("declaration_id", sa.String(length=16), nullable=True),
        sa.Column("qualified_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_findings"),
        sa.ForeignKeyConstraint(
            ["review_run_id"],
            ["review_runs.id"],
            name="fk_findings_review_run_id_review_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name="fk_findings_repository_id_repositories",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pull_request_id"],
            ["pull_requests.id"],
            name="fk_findings_pull_request_id_pull_requests",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_file_id"],
            ["changed_files.id"],
            name="fk_findings_changed_file_id_changed_files",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "review_run_id", "fingerprint", name="uq_findings_review_run_id_fingerprint"
        ),
        sa.CheckConstraint(
            "category IN ('bug','security','performance','resource','maintainability')",
            name="ck_findings_category_valid",
        ),
        sa.CheckConstraint(
            "severity IN ('critical','high','medium','low','info')",
            name="ck_findings_severity_valid",
        ),
        sa.CheckConstraint(
            "mapping_status IN ('inline','summary','dashboard_only')",
            name="ck_findings_mapping_status_valid",
        ),
        sa.CheckConstraint(
            "status IN ('open','resolved','dismissed','false_positive')",
            name="ck_findings_status_valid",
        ),
        sa.CheckConstraint("side IN ('LEFT','RIGHT')", name="ck_findings_side_valid"),
        sa.CheckConstraint(
            "model_confidence >= 0 AND model_confidence <= 1",
            name="ck_findings_model_confidence_range",
        ),
        sa.CheckConstraint(
            "effective_confidence >= 0 AND effective_confidence <= 1",
            name="ck_findings_effective_confidence_range",
        ),
        sa.CheckConstraint(
            "cwe_id IS NULL OR cwe_id ~ '^CWE-[0-9]{1,4}$'",
            name="ck_findings_cwe_id_format",
        ),
        sa.CheckConstraint(
            "mapping_status <> 'inline' OR (resolved_line IS NOT NULL AND anchor_verified)",
            name="ck_findings_inline_requires_verified_anchor",
        ),
    )
    op.create_index(
        "ix_findings_repository_id_severity_created_at",
        "findings",
        ["repository_id", "severity", "created_at"],
    )
    op.create_index(
        "ix_findings_repository_id_status_severity",
        "findings",
        ["repository_id", "status", "severity"],
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_findings_pull_request_id_fingerprint",
        "findings",
        ["pull_request_id", "fingerprint"],
    )
    op.create_index("ix_findings_review_run_id", "findings", ["review_run_id"])
    op.create_index(
        "ix_findings_repository_id_created_at", "findings", ["repository_id", "created_at"]
    )

    # -- review_comments ---------------------------------------------------
    op.create_table(
        "review_comments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("github_review_id", sa.BigInteger(), nullable=True),
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False, server_default="RIGHT"),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("html_url", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_review_comments"),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name="fk_review_comments_finding_id_findings",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_run_id"],
            ["review_runs.id"],
            name="fk_review_comments_review_run_id_review_runs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("finding_id", name="uq_review_comments_finding_id"),
        sa.UniqueConstraint("github_comment_id", name="uq_review_comments_github_comment_id"),
        sa.CheckConstraint("side IN ('LEFT','RIGHT')", name="ck_review_comments_side_valid"),
    )
    op.create_index("ix_review_comments_review_run_id", "review_comments", ["review_run_id"])

    # -- finding_events ----------------------------------------------------
    op.create_table(
        "finding_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="system"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_finding_events"),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name="fk_finding_events_finding_id_findings",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("source IN ('user','system')", name="ck_finding_events_source_valid"),
    )
    op.create_index("ix_finding_events_finding_id", "finding_events", ["finding_id"])

    # -- changed_files: the ADR-0006 back-reference and Phase 2 outcomes ----
    op.add_column(
        "changed_files",
        sa.Column("review_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_changed_files_review_run_id_review_runs",
        "changed_files",
        "review_runs",
        ["review_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("changed_files", sa.Column("language", sa.String(length=32), nullable=True))
    op.add_column(
        "changed_files",
        sa.Column("is_analysed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("changed_files", sa.Column("skip_reason", sa.String(length=40), nullable=True))
    op.add_column("changed_files", sa.Column("parse_ok", sa.Boolean(), nullable=True))
    op.add_column("changed_files", sa.Column("declarations_touched", sa.Integer(), nullable=True))
    op.add_column("changed_files", sa.Column("patch_line_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("changed_files", "patch_line_count")
    op.drop_column("changed_files", "declarations_touched")
    op.drop_column("changed_files", "parse_ok")
    op.drop_column("changed_files", "skip_reason")
    op.drop_column("changed_files", "is_analysed")
    op.drop_column("changed_files", "language")
    op.drop_constraint(
        "fk_changed_files_review_run_id_review_runs", "changed_files", type_="foreignkey"
    )
    op.drop_column("changed_files", "review_run_id")

    op.drop_index("ix_finding_events_finding_id", table_name="finding_events")
    op.drop_table("finding_events")

    op.drop_index("ix_review_comments_review_run_id", table_name="review_comments")
    op.drop_table("review_comments")

    op.drop_index("ix_findings_repository_id_created_at", table_name="findings")
    op.drop_index("ix_findings_review_run_id", table_name="findings")
    op.drop_index("ix_findings_pull_request_id_fingerprint", table_name="findings")
    op.drop_index("ix_findings_repository_id_status_severity", table_name="findings")
    op.drop_index("ix_findings_repository_id_severity_created_at", table_name="findings")
    op.drop_table("findings")

    op.drop_index("ix_review_runs_pull_request_id_created_at", table_name="review_runs")
    op.drop_index("ix_review_runs_repository_id_status_created_at", table_name="review_runs")
    op.drop_table("review_runs")
