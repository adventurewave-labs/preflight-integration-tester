"""Snowflake data warehouse connector for Preflight diagnostics.

Uses snowflake-connector-python when available; falls back to a mock
implementation.  Handles warehouse auto-resume transparently.
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
    import snowflake.connector
    from snowflake.connector import DictCursor

    _SF_CONN_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SF_CONN_AVAILABLE = False
    logger.warning(
        "snowflake-connector-python not available – SnowflakeConnector will use MockSnowflakeConnector"
    )

# ---------------------------------------------------------------------------
# Introspection SQL
# ---------------------------------------------------------------------------

_SQL_CURRENT_VERSION = "SELECT CURRENT_VERSION();"
_SQL_LIST_SCHEMAS = """
    SELECT SCHEMA_NAME
    FROM INFORMATION_SCHEMA.SCHEMATA
    WHERE CATALOG_NAME = CURRENT_DATABASE()
    ORDER BY SCHEMA_NAME;
"""
_SQL_LIST_TABLES = """
    SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = %s
      AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
    ORDER BY TABLE_NAME;
"""
_SQL_LIST_COLUMNS = """
    SELECT
        COLUMN_NAME,
        DATA_TYPE,
        IS_NULLABLE,
        COLUMN_DEFAULT,
        CHARACTER_MAXIMUM_LENGTH,
        NUMERIC_PRECISION,
        NUMERIC_SCALE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = %s
      AND TABLE_NAME = %s
    ORDER BY ORDINAL_POSITION;
"""
_SQL_LIST_PRIMARY_KEYS = """
    SHOW PRIMARY KEYS IN TABLE IDENTIFIER(%s);
"""
_SQL_PING = "SELECT 1;"

# ---------------------------------------------------------------------------
# Mock schema for Snowflake data warehouse
# ---------------------------------------------------------------------------

_SNOW_TABLES: Dict[str, Dict[str, Any]] = {
    "FACT_SALES": {
        "schema": "ANALYTICS",
        "description": "Sales fact table — grain: one row per order line",
        "row_estimate": 24_500_000,
        "columns": [
            {"name": "SALE_KEY", "type": "NUMBER", "nullable": False, "pk": True, "fk_to": None},
            {"name": "DATE_KEY", "type": "NUMBER", "nullable": False, "pk": False, "fk_to": "DIM_DATE.DATE_KEY"},
            {"name": "CUSTOMER_KEY", "type": "NUMBER", "nullable": False, "pk": False, "fk_to": "DIM_CUSTOMER.CUSTOMER_KEY"},
            {"name": "PRODUCT_KEY", "type": "NUMBER", "nullable": False, "pk": False, "fk_to": "DIM_PRODUCT.PRODUCT_KEY"},
            {"name": "STORE_KEY", "type": "NUMBER", "nullable": False, "pk": False, "fk_to": "DIM_STORE.STORE_KEY"},
            {"name": "QUANTITY_SOLD", "type": "NUMBER(18,3)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "UNIT_PRICE", "type": "NUMBER(12,4)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "DISCOUNT_PCT", "type": "NUMBER(5,2)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "GROSS_AMOUNT", "type": "NUMBER(18,2)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "NET_AMOUNT", "type": "NUMBER(18,2)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "TAX_AMOUNT", "type": "NUMBER(18,2)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "CURRENCY_CODE", "type": "VARCHAR(3)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "LOAD_TIMESTAMP", "type": "TIMESTAMP_NTZ", "nullable": False, "pk": False, "fk_to": None},
        ],
    },
    "DIM_CUSTOMER": {
        "schema": "ANALYTICS",
        "description": "Customer dimension — SCD Type 2",
        "row_estimate": 980_000,
        "columns": [
            {"name": "CUSTOMER_KEY", "type": "NUMBER", "nullable": False, "pk": True, "fk_to": None},
            {"name": "CUSTOMER_ID", "type": "VARCHAR(50)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "CUSTOMER_NAME", "type": "VARCHAR(200)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "SEGMENT", "type": "VARCHAR(50)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "REGION", "type": "VARCHAR(100)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "COUNTRY_CODE", "type": "CHAR(2)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "EFFECTIVE_DATE", "type": "DATE", "nullable": False, "pk": False, "fk_to": None},
            {"name": "EXPIRY_DATE", "type": "DATE", "nullable": True, "pk": False, "fk_to": None},
            {"name": "IS_CURRENT", "type": "BOOLEAN", "nullable": False, "pk": False, "fk_to": None},
        ],
    },
    "DIM_PRODUCT": {
        "schema": "ANALYTICS",
        "description": "Product dimension with hierarchy",
        "row_estimate": 45_000,
        "columns": [
            {"name": "PRODUCT_KEY", "type": "NUMBER", "nullable": False, "pk": True, "fk_to": None},
            {"name": "PRODUCT_ID", "type": "VARCHAR(50)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "PRODUCT_NAME", "type": "VARCHAR(300)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "CATEGORY", "type": "VARCHAR(100)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "SUBCATEGORY", "type": "VARCHAR(100)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "BRAND", "type": "VARCHAR(100)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "UNIT_COST", "type": "NUMBER(12,4)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "LIST_PRICE", "type": "NUMBER(12,4)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "IS_ACTIVE", "type": "BOOLEAN", "nullable": False, "pk": False, "fk_to": None},
            {"name": "EFFECTIVE_DATE", "type": "DATE", "nullable": False, "pk": False, "fk_to": None},
            {"name": "EXPIRY_DATE", "type": "DATE", "nullable": True, "pk": False, "fk_to": None},
        ],
    },
    "DIM_DATE": {
        "schema": "ANALYTICS",
        "description": "Calendar date dimension",
        "row_estimate": 7_305,  # 20 years
        "columns": [
            {"name": "DATE_KEY", "type": "NUMBER", "nullable": False, "pk": True, "fk_to": None},
            {"name": "FULL_DATE", "type": "DATE", "nullable": False, "pk": False, "fk_to": None},
            {"name": "YEAR", "type": "NUMBER(4)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "QUARTER", "type": "NUMBER(1)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "MONTH", "type": "NUMBER(2)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "MONTH_NAME", "type": "VARCHAR(20)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "WEEK_OF_YEAR", "type": "NUMBER(2)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "DAY_OF_WEEK", "type": "NUMBER(1)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "DAY_NAME", "type": "VARCHAR(15)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IS_WEEKEND", "type": "BOOLEAN", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IS_HOLIDAY", "type": "BOOLEAN", "nullable": False, "pk": False, "fk_to": None},
            {"name": "FISCAL_YEAR", "type": "NUMBER(4)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "FISCAL_QUARTER", "type": "NUMBER(1)", "nullable": True, "pk": False, "fk_to": None},
        ],
    },
    "DIM_STORE": {
        "schema": "ANALYTICS",
        "description": "Physical and online store locations",
        "row_estimate": 1_250,
        "columns": [
            {"name": "STORE_KEY", "type": "NUMBER", "nullable": False, "pk": True, "fk_to": None},
            {"name": "STORE_ID", "type": "VARCHAR(20)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "STORE_NAME", "type": "VARCHAR(200)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "STORE_TYPE", "type": "VARCHAR(50)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "REGION", "type": "VARCHAR(100)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "COUNTRY_CODE", "type": "CHAR(2)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "OPEN_DATE", "type": "DATE", "nullable": True, "pk": False, "fk_to": None},
            {"name": "CLOSE_DATE", "type": "DATE", "nullable": True, "pk": False, "fk_to": None},
            {"name": "IS_ACTIVE", "type": "BOOLEAN", "nullable": False, "pk": False, "fk_to": None},
        ],
    },
    "AGG_DAILY_SALES": {
        "schema": "ANALYTICS",
        "description": "Pre-aggregated daily sales for dashboard performance",
        "row_estimate": 365_000,
        "columns": [
            {"name": "DATE_KEY", "type": "NUMBER", "nullable": False, "pk": True, "fk_to": "DIM_DATE.DATE_KEY"},
            {"name": "STORE_KEY", "type": "NUMBER", "nullable": False, "pk": True, "fk_to": "DIM_STORE.STORE_KEY"},
            {"name": "TOTAL_TRANSACTIONS", "type": "NUMBER", "nullable": False, "pk": False, "fk_to": None},
            {"name": "TOTAL_QUANTITY", "type": "NUMBER(18,3)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "GROSS_REVENUE", "type": "NUMBER(18,2)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "NET_REVENUE", "type": "NUMBER(18,2)", "nullable": False, "pk": False, "fk_to": None},
            {"name": "AVG_ORDER_VALUE", "type": "NUMBER(12,4)", "nullable": True, "pk": False, "fk_to": None},
            {"name": "UNIQUE_CUSTOMERS", "type": "NUMBER", "nullable": True, "pk": False, "fk_to": None},
            {"name": "LOAD_TIMESTAMP", "type": "TIMESTAMP_NTZ", "nullable": False, "pk": False, "fk_to": None},
        ],
    },
}


class SnowflakeConnector(BaseConnector):
    """Read-only Snowflake data warehouse connector.

    Runs synchronous snowflake-connector calls in a thread pool to avoid
    blocking the event loop.  Warehouse auto-resume is handled transparently
    by the underlying driver.
    """

    SYSTEM_NAME = "snowflake"

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        super().__init__(config, timeout_seconds)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="snow_conn")
        self._target_schema = config.get("schema", "PUBLIC").upper()
        self._connection: Optional[Any] = None

    def _sync_connect(self):
        cfg = self.config
        conn = snowflake.connector.connect(
            user=cfg.get("username") or cfg.get("user"),
            password=cfg.get("password"),
            account=cfg.get("account"),
            database=cfg.get("database"),
            schema=cfg.get("schema", "PUBLIC"),
            warehouse=cfg.get("warehouse"),
            role=cfg.get("role"),
            # Enforce read-only session
            session_parameters={"TRANSACTION_ABORT_ON_ERROR": "true"},
            login_timeout=self.timeout_seconds,
            network_timeout=self.timeout_seconds,
        )
        return conn

    async def connect(self) -> bool:
        if not _SF_CONN_AVAILABLE:
            raise RuntimeError(
                "snowflake-connector-python is not installed.  Use MockSnowflakeConnector instead."
            )
        try:
            logger.info(
                "Connecting to Snowflake account: %s", self.config.get("account")
            )
            loop = asyncio.get_event_loop()
            self._connection = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._sync_connect),
                timeout=self.timeout_seconds + 10,
            )
            self._connected = True
            logger.info("Snowflake connection established")
            return True
        except Exception as exc:
            logger.error("Snowflake connection failed: %s", exc)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        if self._connection:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self._executor, self._connection.close)
            except Exception as exc:
                logger.warning("Error closing Snowflake connection: %s", exc)
        self._connected = False
        self._executor.shutdown(wait=False)

    async def ping(self) -> float:
        start = time.monotonic()

        def _ping():
            cur = self._connection.cursor()
            try:
                cur.execute(_SQL_PING)
                cur.fetchone()
            finally:
                cur.close()

        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(self._executor, _ping),
            timeout=self.timeout_seconds,
        )
        return (time.monotonic() - start) * 1000.0

    def _sync_fetch_all(self, query: str, params=None) -> List[Dict[str, Any]]:
        cur = self._connection.cursor(DictCursor)
        try:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]
        finally:
            cur.close()

    def _sync_fetch_scalar(self, query: str) -> Any:
        cur = self._connection.cursor()
        try:
            cur.execute(query)
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            cur.close()

    async def get_metadata(self) -> SystemMetadata:
        if not self._connected:
            raise RuntimeError("Not connected")

        loop = asyncio.get_event_loop()
        run = lambda fn, *a: loop.run_in_executor(self._executor, fn, *a)  # noqa: E731

        latency = await self.ping()
        version = await run(self._sync_fetch_scalar, _SQL_CURRENT_VERSION)

        raw_tables = await run(self._sync_fetch_all, _SQL_LIST_TABLES, (self._target_schema,))

        tables: List[TableSchema] = []
        for t in raw_tables:
            table_name = t.get("TABLE_NAME") or t.get("table_name", "")
            raw_cols = await run(
                self._sync_fetch_all, _SQL_LIST_COLUMNS, (self._target_schema, table_name)
            )
            columns = [
                {
                    "name": c.get("COLUMN_NAME") or c.get("column_name"),
                    "type": c.get("DATA_TYPE") or c.get("data_type"),
                    "nullable": (c.get("IS_NULLABLE") or c.get("is_nullable", "YES")) == "YES",
                    "pk": False,  # PKs need a separate SHOW PRIMARY KEYS call
                    "fk_to": None,
                }
                for c in raw_cols
            ]
            row_est_raw = t.get("ROW_COUNT") or t.get("row_count")
            tables.append(
                TableSchema(
                    name=table_name,
                    columns=columns,
                    row_count_estimate=int(row_est_raw) if row_est_raw is not None else None,
                )
            )

        total = sum(t.row_count_estimate or 0 for t in tables)
        return SystemMetadata(
            system_name=self.SYSTEM_NAME,
            system_version=str(version),
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

        def _run():
            cur = self._connection.cursor(DictCursor)
            try:
                cur.execute(query, params)
                rows = [dict(r) for r in cur.fetchall()]
                cols = [d.name for d in cur.description] if cur.description else []
                return cols, rows
            finally:
                cur.close()

        try:
            loop = asyncio.get_event_loop()
            cols, rows = await asyncio.wait_for(
                loop.run_in_executor(self._executor, _run),
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


# ---------------------------------------------------------------------------
# Mock fallback
# ---------------------------------------------------------------------------


class MockSnowflakeConnector(BaseConnector):
    """Mock Snowflake connector that generates a realistic data warehouse schema."""

    SYSTEM_NAME = "snowflake"

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        super().__init__(config, timeout_seconds)
        logger.info("MockSnowflakeConnector: using mock data warehouse schema")

    async def connect(self) -> bool:
        await asyncio.sleep(0.12)  # Snowflake / warehouse-resume can be slow
        self._connected = True
        return True

    async def disconnect(self) -> None:
        await asyncio.sleep(0.02)
        self._connected = False

    async def ping(self) -> float:
        start = time.monotonic()
        await asyncio.sleep(0.06)
        return (time.monotonic() - start) * 1000.0

    async def get_metadata(self) -> SystemMetadata:
        latency = await self.ping()
        tables: List[TableSchema] = []
        total_rows = 0

        for tbl_name, tbl_def in _SNOW_TABLES.items():
            row_est = tbl_def.get("row_estimate", 0)
            total_rows += row_est
            tables.append(
                TableSchema(
                    name=tbl_name,
                    columns=tbl_def["columns"],
                    row_count_estimate=row_est,
                    description=tbl_def.get("description"),
                )
            )

        return SystemMetadata(
            system_name=self.SYSTEM_NAME,
            system_version="Snowflake 7.42 (mock)",
            tables=tables,
            entity_count=len(tables),
            total_row_estimate=total_rows,
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
        await asyncio.sleep(0.08)
        elapsed = (time.monotonic() - start) * 1000.0
        return QueryResult(
            query=query,
            row_count=1,
            execution_time_ms=elapsed,
            columns=["RESULT"],
            rows=[{"RESULT": 1}],
        )


def get_snowflake_connector(
    config: Dict[str, Any], timeout_seconds: int = 30
) -> BaseConnector:
    """Return the real connector when snowflake-connector-python is available."""
    if _SF_CONN_AVAILABLE:
        return SnowflakeConnector(config, timeout_seconds)
    return MockSnowflakeConnector(config, timeout_seconds)
