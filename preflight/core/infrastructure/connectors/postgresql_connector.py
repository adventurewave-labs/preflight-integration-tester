"""PostgreSQL connector for Preflight diagnostics.

Attempts to import psycopg2.  If unavailable it falls back to a mock
implementation that returns synthetic schema data so the rest of the system
can still be exercised without a real database.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from preflight.core.infrastructure.connectors.base import (
    BaseConnector,
    QueryResult,
    SystemMetadata,
    TableSchema,
)

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras

    _PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSYCOPG2_AVAILABLE = False
    logger.warning(
        "psycopg2 not available – PostgreSQLConnector will use MockPostgreSQLConnector"
    )

# ---------------------------------------------------------------------------
# SQL for metadata introspection
# ---------------------------------------------------------------------------

_SQL_LIST_TABLES = """
    SELECT table_name, table_type
    FROM information_schema.tables
    WHERE table_schema = %s
      AND table_type IN ('BASE TABLE', 'VIEW')
    ORDER BY table_name;
"""

_SQL_LIST_COLUMNS = """
    SELECT
        column_name,
        data_type,
        is_nullable,
        column_default,
        character_maximum_length,
        numeric_precision,
        numeric_scale
    FROM information_schema.columns
    WHERE table_schema = %s
      AND table_name = %s
    ORDER BY ordinal_position;
"""

_SQL_PRIMARY_KEYS = """
    SELECT kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
       AND tc.table_schema = kcu.table_schema
       AND tc.table_name = kcu.table_name
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.table_schema = %s
      AND tc.table_name = %s;
"""

_SQL_FOREIGN_KEYS = """
    SELECT
        kcu.column_name,
        ccu.table_name  AS foreign_table,
        ccu.column_name AS foreign_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
       AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = tc.constraint_name
       AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema = %s
      AND tc.table_name = %s;
"""

_SQL_ROW_ESTIMATES = """
    SELECT relname AS table_name, n_live_tup AS estimated_rows
    FROM pg_stat_user_tables
    ORDER BY relname;
"""

_SQL_SERVER_VERSION = "SELECT version();"
_SQL_PING = "SELECT 1;"


class PostgreSQLConnector(BaseConnector):
    """Read-only PostgreSQL connector using psycopg2.

    Sync psycopg2 calls are dispatched to a ThreadPoolExecutor so they do not
    block the asyncio event loop.
    """

    SYSTEM_NAME = "postgresql"

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        super().__init__(config, timeout_seconds)
        self._schema = config.get("schema", "public")
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pg_conn")
        self._dsn = self._build_dsn(config)

    @staticmethod
    def _build_dsn(config: Dict[str, Any]) -> str:
        parts = []
        for key, cfg_key in [
            ("host", "host"),
            ("port", "port"),
            ("dbname", "database"),
            ("user", "username"),
            ("password", "password"),
        ]:
            val = config.get(cfg_key) or config.get(key)
            if val is not None:
                parts.append(f"{key}={val}")
        parts.append("options=-c default_transaction_read_only=on")
        return " ".join(parts)

    def _run_sync(self, func, *args):
        """Run a blocking function in the thread pool."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(self._executor, func, *args)

    def _sync_connect(self):
        conn = psycopg2.connect(
            self._dsn,
            connect_timeout=self.timeout_seconds,
        )
        conn.autocommit = True
        # Enforce read-only at the session level
        with conn.cursor() as cur:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
        return conn

    async def connect(self) -> bool:
        if not _PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                "psycopg2 is not installed.  Use MockPostgreSQLConnector instead."
            )
        try:
            logger.info("Connecting to PostgreSQL at %s", self.config.get("host"))
            self._connection = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    self._executor, self._sync_connect
                ),
                timeout=self.timeout_seconds,
            )
            self._connected = True
            logger.info("PostgreSQL connection established")
            return True
        except Exception as exc:
            logger.error("PostgreSQL connection failed: %s", exc)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        if self._connection and not self._connection.closed:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    self._executor, self._connection.close
                )
            except Exception as exc:
                logger.warning("Error closing PostgreSQL connection: %s", exc)
        self._connected = False
        self._executor.shutdown(wait=False)

    async def ping(self) -> float:
        start = time.monotonic()

        def _ping():
            with self._connection.cursor() as cur:
                cur.execute(_SQL_PING)
                cur.fetchone()

        await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(self._executor, _ping),
            timeout=self.timeout_seconds,
        )
        return (time.monotonic() - start) * 1000.0

    def _sync_get_tables(self) -> List[Dict[str, Any]]:
        with self._connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_LIST_TABLES, (self._schema,))
            return [dict(r) for r in cur.fetchall()]

    def _sync_get_columns(self, table_name: str) -> List[Dict[str, Any]]:
        with self._connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_LIST_COLUMNS, (self._schema, table_name))
            return [dict(r) for r in cur.fetchall()]

    def _sync_get_pks(self, table_name: str) -> List[str]:
        with self._connection.cursor() as cur:
            cur.execute(_SQL_PRIMARY_KEYS, (self._schema, table_name))
            return [r[0] for r in cur.fetchall()]

    def _sync_get_fks(self, table_name: str) -> Dict[str, str]:
        with self._connection.cursor() as cur:
            cur.execute(_SQL_FOREIGN_KEYS, (self._schema, table_name))
            return {r[0]: f"{r[1]}.{r[2]}" for r in cur.fetchall()}

    def _sync_get_row_estimates(self) -> Dict[str, int]:
        with self._connection.cursor() as cur:
            cur.execute(_SQL_ROW_ESTIMATES)
            return {r[0]: r[1] for r in cur.fetchall()}

    def _sync_get_version(self) -> str:
        with self._connection.cursor() as cur:
            cur.execute(_SQL_SERVER_VERSION)
            return cur.fetchone()[0]

    async def get_metadata(self) -> SystemMetadata:
        if not self._connected:
            raise RuntimeError("Not connected")

        loop = asyncio.get_event_loop()
        run = lambda fn, *a: loop.run_in_executor(self._executor, fn, *a)  # noqa: E731

        latency = await self.ping()
        version = await run(self._sync_get_version)
        row_estimates = await run(self._sync_get_row_estimates)
        raw_tables = await run(self._sync_get_tables)

        tables: List[TableSchema] = []
        for t in raw_tables:
            table_name = t["table_name"]
            raw_cols = await run(self._sync_get_columns, table_name)
            pks = await run(self._sync_get_pks, table_name)
            fks = await run(self._sync_get_fks, table_name)

            columns = [
                {
                    "name": c["column_name"],
                    "type": c["data_type"],
                    "nullable": c["is_nullable"] == "YES",
                    "pk": c["column_name"] in pks,
                    "fk_to": fks.get(c["column_name"]),
                }
                for c in raw_cols
            ]
            tables.append(
                TableSchema(
                    name=table_name,
                    columns=columns,
                    row_count_estimate=row_estimates.get(table_name),
                )
            )

        total = sum(t.row_count_estimate or 0 for t in tables)
        return SystemMetadata(
            system_name=self.SYSTEM_NAME,
            system_version=version,
            tables=tables,
            entity_count=len(tables),
            total_row_estimate=total,
            connection_latency_ms=latency,
        )

    async def execute_read_query(
        self, query: str, params: Optional[Dict] = None
    ) -> QueryResult:
        start = time.monotonic()

        if not await self.validate_read_only(query):
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=0,
                execution_time_ms=elapsed,
                columns=[],
                rows=[],
                error="Query blocked: only SELECT statements are permitted",
            )

        def _run_query():
            with self._connection.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:
                cur.execute(query, params or {})
                rows = [dict(r) for r in cur.fetchall()]
                cols = [d[0] for d in cur.description] if cur.description else []
                return cols, rows

        try:
            loop = asyncio.get_event_loop()
            cols, rows = await asyncio.wait_for(
                loop.run_in_executor(self._executor, _run_query),
                timeout=self.timeout_seconds,
            )
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=len(rows),
                execution_time_ms=elapsed,
                columns=cols,
                rows=rows,
            )
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=0,
                execution_time_ms=elapsed,
                columns=[],
                rows=[],
                error=f"Query timed out after {self.timeout_seconds}s",
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=0,
                execution_time_ms=elapsed,
                columns=[],
                rows=[],
                error=str(exc),
            )

    async def get_table_stats(self) -> List[Dict[str, Any]]:
        """Return pg_stat_user_tables data for row counts and table sizes."""

        _SQL = """
            SELECT
                relname           AS table_name,
                n_live_tup        AS live_rows,
                n_dead_tup        AS dead_rows,
                last_vacuum,
                last_analyze,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size
            FROM pg_stat_user_tables
            ORDER BY n_live_tup DESC;
        """

        def _run():
            with self._connection.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:
                cur.execute(_SQL)
                return [dict(r) for r in cur.fetchall()]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, _run)


# ---------------------------------------------------------------------------
# Fallback mock when psycopg2 is not installed
# ---------------------------------------------------------------------------


class MockPostgreSQLConnector(BaseConnector):
    """Drop-in replacement for PostgreSQLConnector when psycopg2 is absent.

    Generates a synthetic PostgreSQL-style schema so that the rest of the
    diagnostic pipeline can function without a real database.
    """

    SYSTEM_NAME = "postgresql"

    _MOCK_TABLES = [
        TableSchema(
            name="public.users",
            columns=[
                {"name": "id", "type": "integer", "nullable": False, "pk": True, "fk_to": None},
                {"name": "username", "type": "character varying", "nullable": False, "pk": False, "fk_to": None},
                {"name": "email", "type": "character varying", "nullable": False, "pk": False, "fk_to": None},
                {"name": "created_at", "type": "timestamp without time zone", "nullable": False, "pk": False, "fk_to": None},
                {"name": "is_active", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
            ],
            row_count_estimate=85_000,
            description="Application user accounts",
        ),
        TableSchema(
            name="public.audit_log",
            columns=[
                {"name": "log_id", "type": "bigint", "nullable": False, "pk": True, "fk_to": None},
                {"name": "user_id", "type": "integer", "nullable": True, "pk": False, "fk_to": "public.users.id"},
                {"name": "action", "type": "character varying", "nullable": False, "pk": False, "fk_to": None},
                {"name": "table_name", "type": "character varying", "nullable": True, "pk": False, "fk_to": None},
                {"name": "record_id", "type": "character varying", "nullable": True, "pk": False, "fk_to": None},
                {"name": "occurred_at", "type": "timestamp with time zone", "nullable": False, "pk": False, "fk_to": None},
                {"name": "ip_address", "type": "inet", "nullable": True, "pk": False, "fk_to": None},
            ],
            row_count_estimate=4_200_000,
            description="System-wide audit trail",
        ),
        TableSchema(
            name="public.settings",
            columns=[
                {"name": "key", "type": "character varying", "nullable": False, "pk": True, "fk_to": None},
                {"name": "value", "type": "text", "nullable": True, "pk": False, "fk_to": None},
                {"name": "description", "type": "text", "nullable": True, "pk": False, "fk_to": None},
                {"name": "updated_at", "type": "timestamp without time zone", "nullable": True, "pk": False, "fk_to": None},
            ],
            row_count_estimate=350,
            description="Application configuration settings",
        ),
    ]

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        super().__init__(config, timeout_seconds)
        logger.info("MockPostgreSQLConnector: psycopg2 not available, using mock data")

    async def connect(self) -> bool:
        await asyncio.sleep(0.05)
        self._connected = True
        return True

    async def disconnect(self) -> None:
        await asyncio.sleep(0.01)
        self._connected = False

    async def ping(self) -> float:
        start = time.monotonic()
        await asyncio.sleep(0.02)
        return (time.monotonic() - start) * 1000.0

    async def get_metadata(self) -> SystemMetadata:
        latency = await self.ping()
        total = sum(t.row_count_estimate or 0 for t in self._MOCK_TABLES)
        return SystemMetadata(
            system_name=self.SYSTEM_NAME,
            system_version="PostgreSQL 15.3 (mock)",
            tables=list(self._MOCK_TABLES),
            entity_count=len(self._MOCK_TABLES),
            total_row_estimate=total,
            connection_latency_ms=latency,
        )

    async def execute_read_query(
        self, query: str, params: Optional[Dict] = None
    ) -> QueryResult:
        start = time.monotonic()
        if not await self.validate_read_only(query):
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=0,
                execution_time_ms=elapsed,
                columns=[],
                rows=[],
                error="Query blocked: only SELECT statements are permitted",
            )
        await asyncio.sleep(0.03)
        elapsed = (time.monotonic() - start) * 1000.0
        return QueryResult(
            query=query,
            row_count=1,
            execution_time_ms=elapsed,
            columns=["result"],
            rows=[{"result": 1}],
        )


def get_postgresql_connector(
    config: Dict[str, Any], timeout_seconds: int = 30
) -> BaseConnector:
    """Factory that returns the real connector when psycopg2 is available,
    otherwise the mock implementation."""
    if _PSYCOPG2_AVAILABLE:
        return PostgreSQLConnector(config, timeout_seconds)
    return MockPostgreSQLConnector(config, timeout_seconds)
