"""Alembic environment.

Two decisions worth noting:

1.  **The database URL comes from application settings**, not from
    ``alembic.ini``.  Duplicating a connection string means two places to
    update and one of them will be forgotten; worse, it usually means a
    credential committed to the repository.

2.  **Migrations run through the async engine** the application itself uses,
    via ``connection.run_sync``.  Running them through a second, synchronous
    driver would mean installing and configuring a driver that production
    never exercises — so a migration could pass locally and behave
    differently against the real engine.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from app.core.config import get_settings

# Import Base *and* the models package so that every mapped table is attached
# to the metadata before autogenerate compares it against the database.
from app.db.base import Base
from app.db import models  # noqa: F401  (registers models with Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live database using the async engine."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_database_url(), poolclass=None, future=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
