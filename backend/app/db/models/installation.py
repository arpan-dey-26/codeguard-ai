"""GitHub App installation.

A first-class entity, not a column on ``repositories``, because an
installation is the unit of *authentication*, *authorisation* and *rate
limiting*: every API call is made with an installation token, every rate-limit
budget is per installation, and suspension applies to the installation rather
than to individual repositories (architecture.md §15.4).

Note ``account_type``: a GitHub App can be installed on a user account as well
as an organisation. Assuming every installation belongs to an organisation is
a common and wrong simplification, so the account context is stored explicitly.

Deleted installations are **soft**-deleted (``uninstalled_at``) so that review
history survives an uninstall.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ACCOUNT_TYPES = ("User", "Organization")


class Installation(TimestampMixin, Base):
    __tablename__ = "installations"
    __table_args__ = (
        CheckConstraint(
            "account_type IN ('User', 'Organization')",
            name="account_type_valid",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    github_installation_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False, default="Organization")

    repository_selection: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Opaque snapshots of what GitHub granted: variable shape, read as a whole,
    # never filtered on. JSONB is the honest representation; normalising a
    # permission set into rows would add joins for no query benefit.
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    events: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    installed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    """Who installed the App, when we know (architecture.md §16.2).

    **Added to the model in Phase 6.** Migration 0005 created the column and
    the model was never updated to match — so ``Base.metadata`` did not describe
    the deployed schema, and the next ``alembic revision --autogenerate`` would
    have proposed **dropping** it. Caught by the Phase 6 schema-parity audit,
    which compares every migration column against every model column in both
    directions; the Phase 5 audit only checked one direction.

    Nullable with ``ON DELETE SET NULL``: an installation outlives the account
    that created it, and losing the installation because a person left would be
    a far worse outcome than losing the attribution.
    """

    @property
    def is_active(self) -> bool:
        return self.suspended_at is None and self.uninstalled_at is None
