"""
Database session management.

Provides async SQLAlchemy session factory with connection pooling.
Falls back to SQLite for development/testing when PostgreSQL is not available.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base

logger = logging.getLogger(__name__)

_async_engine = None
_async_session_factory = None


def get_database_url(async_driver: bool = True) -> str:
    """Get database URL from environment, with SQLite fallback.

    Args:
        async_driver: If True, return a URL with an async driver prefix.

    Returns:
        A database connection URL string.
    """
    url = os.environ.get("DATABASE_URL", "")

    if not url:
        # SQLite fallback for development
        db_path = os.environ.get("SQLITE_PATH", "preflight.db")
        if async_driver:
            return f"sqlite+aiosqlite:///{db_path}"
        return f"sqlite:///{db_path}"

    # Convert postgres:// to postgresql:// (Heroku compatibility)
    url = url.replace("postgres://", "postgresql://", 1)

    if async_driver:
        # Add async driver
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("sqlite://"):
            return url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    return url


async def init_db(database_url: Optional[str] = None) -> None:
    """Initialize database engine and create all tables.

    Args:
        database_url: Optional explicit database URL. Falls back to
            :func:`get_database_url` if not provided.
    """
    global _async_engine, _async_session_factory

    url = database_url or get_database_url(async_driver=True)

    kwargs: dict = {}
    if "sqlite" in url:
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
        kwargs["pool_pre_ping"] = True

    _async_engine = create_async_engine(url, echo=False, **kwargs)
    _async_session_factory = async_sessionmaker(
        _async_engine, class_=AsyncSession, expire_on_commit=False
    )

    # Create all tables
    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info(
        "Database initialized: %s",
        url.split("@")[-1] if "@" in url else url,
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session.

    Commits on success, rolls back on any exception.

    Yields:
        An :class:`AsyncSession` for use within a request.
    """
    global _async_session_factory
    if _async_session_factory is None:
        await init_db()

    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions (non-FastAPI use).

    Commits on success, rolls back on any exception.

    Yields:
        An :class:`AsyncSession`.
    """
    global _async_session_factory
    if _async_session_factory is None:
        await init_db()

    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
