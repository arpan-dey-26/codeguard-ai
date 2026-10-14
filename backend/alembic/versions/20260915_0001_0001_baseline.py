"""Baseline revision — establishes the migration chain, creates no tables.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-15

Why this migration exists, given that it creates nothing.

Phase 0 defines no domain tables — the CodeGuard schema belongs to the phases
that use it (architecture.md §16).  But a project with zero revisions cannot
demonstrate that its migration pipeline works: ``alembic upgrade head`` has
nothing to apply, the ``alembic_version`` table is never created, and
``/readyz`` therefore cannot distinguish "never migrated" from "migrated".

One empty baseline revision fixes all three for the cost of a file:

* ``upgrade`` creates ``alembic_version`` as a side effect, giving ``/readyz``
  a real revision to report;
* ``upgrade`` / ``downgrade`` are both exercisable, so the reversibility
  requirement (architecture.md §25.3) is verified from the first commit
  rather than discovered to be broken at migration number fifteen;
* every subsequent migration has a stable parent, so the chain never needs
  rewriting.

This is the only migration that is allowed to be empty.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema changes. See the module docstring."""


def downgrade() -> None:
    """No schema changes to reverse."""
