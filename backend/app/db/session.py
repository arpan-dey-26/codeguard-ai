"""Async engine and session management.

The engine is created lazily and held for the process lifetime; sessions are
created per unit of work and always closed.  ``get_session`` is the FastAPI
dependency used by routes — it is the only sanctioned way a request obtains a
database session, which keeps connection lifetime out of route code.

One rule inherited from architecture.md §22.4 and worth stating at the
foundation: **no external call inside an open transaction**.  Fetch first,
then open a short transaction to write.  A session held across a network call
holds a pooled connection for its whole duration and exhausts the pool under
concurrency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine from settings."""
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # a recycled/dead connection fails here, not mid-query
        future=True,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings())
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # objects stay usable after commit
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a database session.

    The session is rolled back on any exception and always closed.  Commits
    are explicit and belong to the service layer, never to a route.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database_connection() -> bool:
    """Cheap liveness probe for the database.

    Used by ``/health`` and ``/readyz``.  Returns a boolean rather than
    raising, because a health endpoint that raises is a health endpoint that
    returns 500 instead of a useful report.
    """
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    # Deliberately broad: a health check that raises is a health check that
    # returns 500 instead of a useful report.
    except Exception as exc:
        logger.warning("db.connection.failed", error_type=type(exc).__name__)
        return False


async def get_migration_revision() -> str | None:
    """Return the Alembic revision currently applied to the database.

    ``None`` means the database is reachable but has never been migrated —
    which is a distinct, actionable state from "database is down", and
    ``/readyz`` reports them differently.
    """
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            return str(row[0]) if row else None
    except Exception:
        return None


async def dispose_engine() -> None:
    """Close all pooled connections.  Called on application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
