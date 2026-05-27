"""Alembic migrations environment."""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import models so Alembic can detect them
from preflight.core.infrastructure.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Resolve the database URL from the environment.

    Normalises ``postgres://`` (Heroku-style) to ``postgresql://`` and falls
    back to a local SQLite database when ``DATABASE_URL`` is not set.

    For async engine usage the returned URL uses an async driver prefix
    (``postgresql+asyncpg://`` or ``sqlite+aiosqlite://``).

    Returns:
        A database connection URL string with an async driver.
    """
    url = os.environ.get("DATABASE_URL", "sqlite:///preflight.db")
    # Heroku compat
    url = url.replace("postgres://", "postgresql://", 1)

    # Upgrade to async drivers
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("sqlite://"):
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required).

    This emits the migration SQL to stdout rather than executing it,
    useful for generating SQL scripts that a DBA will apply manually.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Execute pending migrations against an open DB connection.

    Args:
        connection: A synchronous SQLAlchemy connection object.
    """
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine (PostgreSQL + asyncpg)."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to the live database)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
