"""A repository visible to one installation.

``github_repo_id`` is the key that matters: repositories are renamed and
transferred, so ``full_name`` is mutable and must never be used as an
identity. Both are stored — the name is what humans and URLs use.

``ON DELETE RESTRICT`` on the installation FK is deliberate: uninstalling is a
soft delete, and a repository must never end up orphaned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (Index("ix_repositories_installation_id", "installation_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    installation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("installations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    owner_login: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)

    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    primary_language: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
