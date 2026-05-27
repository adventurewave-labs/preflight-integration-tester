"""
Tests for database session management.
"""
import os
import pytest
from unittest.mock import patch, AsyncMock


class TestGetDatabaseUrl:
    """Tests for get_database_url()."""

    def test_sqlite_fallback_no_env(self, monkeypatch):
        """Without DATABASE_URL env, falls back to SQLite."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SQLITE_PATH", raising=False)
        from preflight.core.infrastructure.database.session import get_database_url
        url = get_database_url(async_driver=True)
        assert "sqlite" in url
        assert "aiosqlite" in url

    def test_sqlite_fallback_sync(self, monkeypatch):
        """Without DATABASE_URL, sync driver uses plain sqlite://."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SQLITE_PATH", raising=False)
        from preflight.core.infrastructure.database.session import get_database_url
        url = get_database_url(async_driver=False)
        assert url.startswith("sqlite:///")
        assert "aiosqlite" not in url

    def test_custom_sqlite_path(self, monkeypatch):
        """SQLITE_PATH env var customizes the SQLite file path."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("SQLITE_PATH", "/tmp/custom.db")
        from preflight.core.infrastructure.database.session import get_database_url
        url = get_database_url(async_driver=True)
        assert "custom.db" in url

    def test_postgres_url_gets_asyncpg_prefix(self, monkeypatch):
        """PostgreSQL URLs get asyncpg prefix for async driver."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        from preflight.core.infrastructure.database.session import get_database_url
        url = get_database_url(async_driver=True)
        assert "asyncpg" in url
        assert "postgresql+asyncpg://" in url

    def test_postgres_shorthand_converted(self, monkeypatch):
        """postgres:// (Heroku style) is converted to postgresql://."""
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host/db")
        from preflight.core.infrastructure.database.session import get_database_url
        url = get_database_url(async_driver=True)
        assert "postgres://" not in url
        assert "postgresql" in url

    def test_sqlite_url_from_env_gets_async_prefix(self, monkeypatch):
        """sqlite:// env URL gets aiosqlite prefix."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///mydb.db")
        from preflight.core.infrastructure.database.session import get_database_url
        url = get_database_url(async_driver=True)
        assert "aiosqlite" in url

    def test_non_async_postgres_returns_unchanged(self, monkeypatch):
        """When async_driver=False, PostgreSQL URL returned as-is after fixes."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        from preflight.core.infrastructure.database.session import get_database_url
        url = get_database_url(async_driver=False)
        assert "asyncpg" not in url


class TestInitDb:
    """Tests for init_db() with SQLite (in-memory for tests)."""

    @pytest.mark.asyncio
    async def test_init_db_creates_tables(self, monkeypatch):
        """init_db() with SQLite in-memory creates all tables."""
        import preflight.core.infrastructure.database.session as session_mod
        # Reset globals
        session_mod._async_engine = None
        session_mod._async_session_factory = None

        await session_mod.init_db("sqlite+aiosqlite:///:memory:")
        assert session_mod._async_engine is not None
        assert session_mod._async_session_factory is not None

        # Cleanup
        await session_mod._async_engine.dispose()
        session_mod._async_engine = None
        session_mod._async_session_factory = None

    @pytest.mark.asyncio
    async def test_init_db_idempotent(self, monkeypatch):
        """init_db() can be called twice without error."""
        import preflight.core.infrastructure.database.session as session_mod
        session_mod._async_engine = None
        session_mod._async_session_factory = None

        await session_mod.init_db("sqlite+aiosqlite:///:memory:")
        engine1 = session_mod._async_engine
        await session_mod.init_db("sqlite+aiosqlite:///:memory:")
        # Engine is recreated, that's OK
        assert session_mod._async_engine is not None

        await session_mod._async_engine.dispose()
        session_mod._async_engine = None
        session_mod._async_session_factory = None


class TestGetSession:
    """Tests for get_session() FastAPI dependency."""

    @pytest.mark.asyncio
    async def test_get_session_yields_async_session(self):
        """get_session() yields an AsyncSession."""
        import preflight.core.infrastructure.database.session as session_mod
        session_mod._async_engine = None
        session_mod._async_session_factory = None

        await session_mod.init_db("sqlite+aiosqlite:///:memory:")

        from sqlalchemy.ext.asyncio import AsyncSession
        gen = session_mod.get_session()
        session = await gen.__anext__()
        assert isinstance(session, AsyncSession)

        try:
            await gen.aclose()
        except StopAsyncIteration:
            pass

        await session_mod._async_engine.dispose()
        session_mod._async_engine = None
        session_mod._async_session_factory = None


class TestGetDbSession:
    """Tests for get_db_session() context manager."""

    @pytest.mark.asyncio
    async def test_get_db_session_yields_session(self):
        """get_db_session() context manager yields AsyncSession."""
        import preflight.core.infrastructure.database.session as session_mod
        session_mod._async_engine = None
        session_mod._async_session_factory = None

        await session_mod.init_db("sqlite+aiosqlite:///:memory:")

        from sqlalchemy.ext.asyncio import AsyncSession
        async with session_mod.get_db_session() as session:
            assert isinstance(session, AsyncSession)

        await session_mod._async_engine.dispose()
        session_mod._async_engine = None
        session_mod._async_session_factory = None

    @pytest.mark.asyncio
    async def test_get_db_session_rollback_on_error(self):
        """get_db_session() rolls back on exception."""
        import preflight.core.infrastructure.database.session as session_mod
        session_mod._async_engine = None
        session_mod._async_session_factory = None

        await session_mod.init_db("sqlite+aiosqlite:///:memory:")

        with pytest.raises(RuntimeError):
            async with session_mod.get_db_session() as session:
                raise RuntimeError("test error")

        await session_mod._async_engine.dispose()
        session_mod._async_engine = None
        session_mod._async_session_factory = None
