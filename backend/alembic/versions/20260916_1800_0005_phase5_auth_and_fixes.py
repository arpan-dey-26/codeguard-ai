"""Phase 5 — identity, and the schema defects the Phase 5 audit found.

Revision ID: 0005_phase5_auth_and_fixes
Revises: 0004_dashboard_indexes
Create Date: 2026-09-16

Four groups of change, in this order.

**1. Identity.** ``users``, ``user_sessions``, ``user_installation_access`` and
``oauth_states``. ``users`` and ``user_installation_access`` are specified in
architecture.md §16.1 and §16.3; the other two are not, and the reasons they
exist are recorded on the models. ``installations.installed_by_user_id`` — an
§16.2 column that could not be added in Phase 1, because ``users`` did not
exist yet — is added here now that its target does.

**2. Foreign-key index coverage.** Three ``ON DELETE SET NULL`` child columns
had no index, so every parent delete sequentially scanned the child table to
apply the nulling. Repository and pull-request deletes cascade into exactly
those tables, so this fired on ordinary cleanup, not only on an unusual path.

**3. Model/migration parity.** Migration 0003 gave 24 columns a
``server_default`` that the corresponding models do not declare. Autogenerate
runs with ``compare_server_default=True``, so the next generated migration
would have emitted ``alter_column(server_default=None)`` for all of them — and
two live INSERT paths (``review_runs`` and ``changed_files``) omit those
columns entirely and depend on the default existing. The parity is restored
from the *model* side in this phase; nothing is dropped here. This migration
adds the one genuinely missing piece, an index on ``changed_files`` for the
coverage analytics, and the ``webhook_events`` status the enqueue fix needs.

**4. A wider check constraint.** ``webhook_events.processing_status`` gains
``coalesced``, for a delivery that was authentic and recorded but whose work an
already-active job covers. Before Phase 5 that situation raised an
``IntegrityError`` which rolled back the audit row along with the job.

Reversible: ``downgrade`` drops everything this migration creates and restores
the narrower constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_phase5_auth_and_fixes"
down_revision: str | None = "0004_dashboard_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------- users
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        # The identity key. Not ``login``: GitHub handles are mutable and, once
        # released, reusable by a different person.
        sa.UniqueConstraint("github_user_id", name=op.f("uq_users_github_user_id")),
    )

    # ------------------------------------------------------- user_sessions
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        # SHA-256 hex of the opaque cookie value. The plaintext is never stored,
        # so a database dump yields no usable session.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_sessions_token_hash")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_at IS NOT NULL",
            name=op.f("ck_user_sessions_revoked_reason_requires_revoked_at"),
        ),
    )
    op.create_index(
        "ix_user_sessions_user_id_expires_at",
        "user_sessions",
        ["user_id", "expires_at"],
    )
    # Partial: the sweeper and every session lookup only ever touch live rows,
    # and revoked rows accumulate as an audit trail. Indexing them would pay a
    # write cost for rows no query returns.
    op.create_index(
        "ix_user_sessions_expires_at_active",
        "user_sessions",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # -------------------------------------------- user_installation_access
    op.create_table(
        "user_installation_access",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "role", sa.String(length=16), server_default="member", nullable=False
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "user_id", "installation_id", name=op.f("pk_user_installation_access")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_installation_access_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name=op.f(
                "fk_user_installation_access_installation_id_installations"
            ),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('admin','member')",
            name=op.f("ck_user_installation_access_role_valid"),
        ),
    )
    # The composite PK leads with ``user_id``, which serves every session
    # lookup. This covers the other direction: "who can see this installation",
    # needed when access is revoked across users.
    op.create_index(
        "ix_user_installation_access_installation_id",
        "user_installation_access",
        ["installation_id"],
    )

    # -------------------------------------------------------- oauth_states
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("pkce_verifier", sa.String(length=128), nullable=True),
        sa.Column("redirect_path", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_states")),
        # Uniqueness is what makes ``DELETE ... RETURNING`` a single-use check.
        sa.UniqueConstraint("state_hash", name=op.f("uq_oauth_states_state_hash")),
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"])

    # ------------------------------------ installations.installed_by_user_id
    # architecture.md §16.2 specifies this column. Phase 1 could not add it:
    # ``users`` did not exist. Nullable and SET NULL, because an installation
    # outlives the account that created it.
    op.add_column(
        "installations",
        sa.Column("installed_by_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_installations_installed_by_user_id_users"),
        "installations",
        "users",
        ["installed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_installations_installed_by_user_id",
        "installations",
        ["installed_by_user_id"],
    )

    # ------------------------------------------- FK index coverage (audit 3)
    # PostgreSQL does not index the *referencing* side of a foreign key. All
    # three below are ON DELETE SET NULL, which is the case that needs it:
    # applying the null requires finding the children, and without an index
    # that is a sequential scan of the child table per parent row deleted.
    op.create_index(
        "ix_changed_files_review_run_id",
        "changed_files",
        ["review_run_id"],
        postgresql_where=sa.text("review_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_findings_changed_file_id",
        "findings",
        ["changed_file_id"],
        postgresql_where=sa.text("changed_file_id IS NOT NULL"),
    )
    op.create_index(
        "ix_review_runs_webhook_event_id",
        "review_runs",
        ["webhook_event_id"],
        postgresql_where=sa.text("webhook_event_id IS NOT NULL"),
    )

    # --------------------------------- coverage analytics index (audit 4)
    # ``skip_reason_breakdown`` filters ``is_analysed = false`` over a time
    # range with no repository predicate in its default state, and
    # ``changed_files`` is the highest-row-count table in the schema. Partial,
    # because the query never returns analysed rows.
    op.create_index(
        "ix_changed_files_created_at_unanalysed",
        "changed_files",
        [sa.text("created_at DESC")],
        postgresql_where=sa.text("is_analysed = false"),
    )

    # ------------------------------ webhook_events: the 'coalesced' status
    # A delivery that is authentic and recorded, but whose work an active job
    # already covers. Before Phase 5 this path raised an IntegrityError that
    # rolled the audit row back with it, losing the record of a delivery GitHub
    # will never send again.
    #
    # ``op.f()`` is load-bearing here, not decoration. The metadata naming
    # convention for a check constraint is
    # ``ck_%(table_name)s_%(constraint_name)s``, and Alembic applies it to any
    # plain string it is given — so passing the *already rendered* name asked
    # PostgreSQL for
    #
    #     ck_webhook_events_ck_webhook_events_processing_status_valid
    #
    # which does not exist, and `alembic upgrade head` died here on every fresh
    # database. ``op.f()`` wraps the name in ``conv()``, which means "this is
    # final, do not render it again" — matching what 0002 created with the same
    # call. The ``create_check_constraint`` below is the mirror image and is
    # deliberately *not* wrapped: it passes the logical name and lets the
    # convention render it, which is how 0002's name came about in the first
    # place. Create by logical name, drop by final name.
    op.drop_constraint(
        op.f("ck_webhook_events_processing_status_valid"),
        "webhook_events",
        type_="check",
    )
    op.create_check_constraint(
        "processing_status_valid",
        "webhook_events",
        "processing_status IN "
        "('received','queued','processed','ignored','coalesced','failed')",
    )


def downgrade() -> None:
    # ``op.f()`` for the same reason as in ``upgrade`` — and note that the name
    # is identical in both directions, because ``create_check_constraint``
    # renders the same logical name through the same convention every time.
    op.drop_constraint(
        op.f("ck_webhook_events_processing_status_valid"),
        "webhook_events",
        type_="check",
    )
    # Any row written while 0005 was applied could hold the new value, and the
    # narrower constraint would refuse to validate. Fold those rows into the
    # nearest pre-existing state first, so the downgrade succeeds rather than
    # failing halfway through.
    op.execute(
        "UPDATE webhook_events SET processing_status = 'ignored' "
        "WHERE processing_status = 'coalesced'"
    )
    op.create_check_constraint(
        "processing_status_valid",
        "webhook_events",
        "processing_status IN ('received','queued','processed','ignored','failed')",
    )

    op.drop_index("ix_changed_files_created_at_unanalysed", table_name="changed_files")
    op.drop_index("ix_review_runs_webhook_event_id", table_name="review_runs")
    op.drop_index("ix_findings_changed_file_id", table_name="findings")
    op.drop_index("ix_changed_files_review_run_id", table_name="changed_files")

    op.drop_index("ix_installations_installed_by_user_id", table_name="installations")
    op.drop_constraint(
        op.f("fk_installations_installed_by_user_id_users"),
        "installations",
        type_="foreignkey",
    )
    op.drop_column("installations", "installed_by_user_id")

    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states")
    op.drop_table("oauth_states")

    op.drop_index(
        "ix_user_installation_access_installation_id",
        table_name="user_installation_access",
    )
    op.drop_table("user_installation_access")

    op.drop_index("ix_user_sessions_expires_at_active", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id_expires_at", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_table("users")
