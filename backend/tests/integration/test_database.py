"""Database connectivity and migration state.

Marked ``integration`` and skipped automatically when no PostgreSQL is
reachable, so ``pytest`` stays green on a machine with nothing running while
still exercising the real database in Docker and in CI.

SQLite is deliberately not used as a substitute: the design depends on
``FOR UPDATE SKIP LOCKED``, partial indexes, ``JSONB`` and ``TIMESTAMPTZ``,
none of which SQLite has.  Testing against a different database than
production tests a different system (architecture.md §25.3).
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db.base import NAMING_CONVENTION, Base
from app.db.session import check_database_connection, create_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL, requires_database

pytestmark = pytest.mark.integration


@requires_database
async def test_engine_connects_and_executes(settings) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@requires_database
async def test_health_probe_reports_true_against_a_live_database() -> None:
    assert await check_database_connection() is True


@requires_database
async def test_server_is_postgresql() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version()"))).scalar_one()
        assert "PostgreSQL" in str(version)
    finally:
        await engine.dispose()


@requires_database
async def test_baseline_migration_has_been_applied() -> None:
    """``alembic upgrade head`` must have created the version table."""
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            revision = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
        assert revision == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    finally:
        await engine.dispose()


def test_naming_convention_is_installed_before_the_first_table() -> None:
    """Deterministic constraint names are only cheap to set up front."""
    assert Base.metadata.naming_convention == NAMING_CONVENTION


@requires_database
async def test_migrated_database_contains_all_model_tables() -> None:
    """The current schema must contain every table used by the application."""
    from app.db import models  # noqa: F401 - register the application models

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
        assert Base.metadata.tables
        assert set(Base.metadata.tables) <= tables
    finally:
        await engine.dispose()
