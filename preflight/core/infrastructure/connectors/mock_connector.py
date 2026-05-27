"""Mock connector that generates realistic synthetic enterprise data for testing.

This connector never touches a real system.  It produces plausible schemas and
query results so that higher-level diagnostic logic can be tested without any
external dependencies.
"""

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional

from preflight.core.infrastructure.connectors.base import (
    BaseConnector,
    QueryResult,
    SystemMetadata,
    TableSchema,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column definitions for synthetic enterprise tables
# ---------------------------------------------------------------------------

_COLUMN = lambda name, dtype, nullable=True, pk=False, fk_to=None: {  # noqa: E731
    "name": name,
    "type": dtype,
    "nullable": nullable,
    "pk": pk,
    "fk_to": fk_to,
}

MOCK_SCHEMAS: Dict[str, List[Dict[str, Any]]] = {
    "erp_customers": [
        _COLUMN("customer_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("customer_number", "VARCHAR(20)", nullable=False),
        _COLUMN("name", "VARCHAR(200)", nullable=False),
        _COLUMN("account_group", "VARCHAR(10)"),
        _COLUMN("country_code", "CHAR(2)"),
        _COLUMN("region", "VARCHAR(50)"),
        _COLUMN("city", "VARCHAR(100)"),
        _COLUMN("postal_code", "VARCHAR(20)"),
        _COLUMN("street", "VARCHAR(200)"),
        _COLUMN("tax_number", "VARCHAR(30)"),
        _COLUMN("currency_code", "CHAR(3)"),
        _COLUMN("payment_terms", "VARCHAR(10)"),
        _COLUMN("credit_limit", "DECIMAL(18,2)"),
        _COLUMN("balance", "DECIMAL(18,2)"),
        _COLUMN("created_at", "TIMESTAMP", nullable=False),
        _COLUMN("updated_at", "TIMESTAMP"),
        _COLUMN("is_active", "BOOLEAN", nullable=False),
        _COLUMN("customer_class", "VARCHAR(20)"),
        _COLUMN("industry_code", "VARCHAR(10)"),
        _COLUMN("sales_rep_id", "INTEGER", fk_to="employees.employee_id"),
    ],
    "crm_contacts": [
        _COLUMN("contact_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("first_name", "VARCHAR(100)", nullable=False),
        _COLUMN("last_name", "VARCHAR(100)", nullable=False),
        _COLUMN("email", "VARCHAR(255)"),
        _COLUMN("phone", "VARCHAR(30)"),
        _COLUMN("mobile", "VARCHAR(30)"),
        _COLUMN("title", "VARCHAR(100)"),
        _COLUMN("department", "VARCHAR(100)"),
        _COLUMN("account_id", "INTEGER", fk_to="erp_customers.customer_id"),
        _COLUMN("lead_source", "VARCHAR(50)"),
        _COLUMN("lifecycle_stage", "VARCHAR(30)"),
        _COLUMN("owner_id", "INTEGER", fk_to="employees.employee_id"),
        _COLUMN("created_at", "TIMESTAMP", nullable=False),
        _COLUMN("last_modified_at", "TIMESTAMP"),
        _COLUMN("do_not_contact", "BOOLEAN"),
        _COLUMN("preferred_language", "CHAR(5)"),
        _COLUMN("timezone", "VARCHAR(50)"),
        _COLUMN("notes", "TEXT"),
    ],
    "orders": [
        _COLUMN("order_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("order_number", "VARCHAR(30)", nullable=False),
        _COLUMN("customer_id", "INTEGER", nullable=False, fk_to="erp_customers.customer_id"),
        _COLUMN("order_date", "DATE", nullable=False),
        _COLUMN("required_date", "DATE"),
        _COLUMN("shipped_date", "DATE"),
        _COLUMN("status", "VARCHAR(20)", nullable=False),
        _COLUMN("ship_via", "INTEGER"),
        _COLUMN("freight", "DECIMAL(10,2)"),
        _COLUMN("ship_name", "VARCHAR(200)"),
        _COLUMN("ship_address", "VARCHAR(300)"),
        _COLUMN("ship_city", "VARCHAR(100)"),
        _COLUMN("ship_country", "CHAR(2)"),
        _COLUMN("currency_code", "CHAR(3)"),
        _COLUMN("total_amount", "DECIMAL(18,2)"),
        _COLUMN("discount_amount", "DECIMAL(18,2)"),
        _COLUMN("tax_amount", "DECIMAL(18,2)"),
        _COLUMN("employee_id", "INTEGER", fk_to="employees.employee_id"),
        _COLUMN("created_at", "TIMESTAMP", nullable=False),
        _COLUMN("updated_at", "TIMESTAMP"),
    ],
    "order_items": [
        _COLUMN("item_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("order_id", "INTEGER", nullable=False, fk_to="orders.order_id"),
        _COLUMN("product_id", "INTEGER", nullable=False, fk_to="products.product_id"),
        _COLUMN("quantity", "DECIMAL(12,3)", nullable=False),
        _COLUMN("unit_price", "DECIMAL(12,4)", nullable=False),
        _COLUMN("discount_pct", "DECIMAL(5,2)"),
        _COLUMN("line_total", "DECIMAL(18,2)"),
        _COLUMN("unit_of_measure", "VARCHAR(10)"),
        _COLUMN("warehouse_id", "INTEGER"),
    ],
    "products": [
        _COLUMN("product_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("sku", "VARCHAR(50)", nullable=False),
        _COLUMN("product_name", "VARCHAR(300)", nullable=False),
        _COLUMN("description", "TEXT"),
        _COLUMN("category_id", "INTEGER"),
        _COLUMN("supplier_id", "INTEGER"),
        _COLUMN("unit_price", "DECIMAL(12,4)"),
        _COLUMN("cost_price", "DECIMAL(12,4)"),
        _COLUMN("units_in_stock", "DECIMAL(12,3)"),
        _COLUMN("units_on_order", "DECIMAL(12,3)"),
        _COLUMN("reorder_level", "DECIMAL(12,3)"),
        _COLUMN("is_discontinued", "BOOLEAN", nullable=False),
        _COLUMN("weight_kg", "DECIMAL(10,3)"),
        _COLUMN("length_cm", "DECIMAL(10,2)"),
        _COLUMN("width_cm", "DECIMAL(10,2)"),
        _COLUMN("height_cm", "DECIMAL(10,2)"),
        _COLUMN("created_at", "TIMESTAMP", nullable=False),
        _COLUMN("updated_at", "TIMESTAMP"),
    ],
    "employees": [
        _COLUMN("employee_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("employee_number", "VARCHAR(20)"),
        _COLUMN("first_name", "VARCHAR(100)", nullable=False),
        _COLUMN("last_name", "VARCHAR(100)", nullable=False),
        _COLUMN("email", "VARCHAR(255)", nullable=False),
        _COLUMN("job_title", "VARCHAR(150)"),
        _COLUMN("department_id", "INTEGER"),
        _COLUMN("manager_id", "INTEGER", fk_to="employees.employee_id"),
        _COLUMN("hire_date", "DATE"),
        _COLUMN("termination_date", "DATE"),
        _COLUMN("is_active", "BOOLEAN", nullable=False),
        _COLUMN("cost_center", "VARCHAR(20)"),
        _COLUMN("location_code", "VARCHAR(10)"),
        _COLUMN("created_at", "TIMESTAMP", nullable=False),
    ],
    "financial_transactions": [
        _COLUMN("transaction_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("transaction_date", "DATE", nullable=False),
        _COLUMN("posting_date", "DATE"),
        _COLUMN("document_number", "VARCHAR(30)"),
        _COLUMN("account_code", "VARCHAR(20)", nullable=False),
        _COLUMN("cost_center", "VARCHAR(20)"),
        _COLUMN("debit_amount", "DECIMAL(18,2)"),
        _COLUMN("credit_amount", "DECIMAL(18,2)"),
        _COLUMN("currency_code", "CHAR(3)", nullable=False),
        _COLUMN("exchange_rate", "DECIMAL(12,6)"),
        _COLUMN("reference", "VARCHAR(100)"),
        _COLUMN("description", "VARCHAR(500)"),
        _COLUMN("created_by", "INTEGER", fk_to="employees.employee_id"),
        _COLUMN("created_at", "TIMESTAMP", nullable=False),
    ],
    "inventory_movements": [
        _COLUMN("movement_id", "INTEGER", nullable=False, pk=True),
        _COLUMN("product_id", "INTEGER", nullable=False, fk_to="products.product_id"),
        _COLUMN("warehouse_id", "INTEGER", nullable=False),
        _COLUMN("movement_type", "VARCHAR(20)", nullable=False),
        _COLUMN("quantity", "DECIMAL(12,3)", nullable=False),
        _COLUMN("unit_cost", "DECIMAL(12,4)"),
        _COLUMN("movement_date", "TIMESTAMP", nullable=False),
        _COLUMN("reference_doc", "VARCHAR(50)"),
        _COLUMN("operator_id", "INTEGER", fk_to="employees.employee_id"),
    ],
}

# Row count estimates for each mock table
_ROW_ESTIMATES: Dict[str, int] = {
    "erp_customers": 45_230,
    "crm_contacts": 128_750,
    "orders": 892_100,
    "order_items": 3_450_800,
    "products": 12_340,
    "employees": 4_780,
    "financial_transactions": 6_200_000,
    "inventory_movements": 1_890_000,
}

_TABLE_DESCRIPTIONS: Dict[str, str] = {
    "erp_customers": "Master customer/account records from the ERP system",
    "crm_contacts": "Individual contact persons linked to customer accounts",
    "orders": "Sales order headers with status and shipping information",
    "order_items": "Line items for each sales order",
    "products": "Product/item master with pricing and inventory metadata",
    "employees": "Employee master data including org-chart relationships",
    "financial_transactions": "General ledger journal entries",
    "inventory_movements": "Stock movement records across warehouses",
}


def _make_sample_row(table_name: str, idx: int = 0) -> Dict[str, Any]:
    """Return a single anonymized sample row for the given table."""
    base: Dict[str, Any] = {
        "erp_customers": {
            "customer_id": 1000 + idx,
            "customer_number": f"C{1000 + idx:05d}",
            "name": f"Acme Corp {idx}",
            "country_code": "US",
            "currency_code": "USD",
            "is_active": True,
        },
        "crm_contacts": {
            "contact_id": 2000 + idx,
            "first_name": "Jane",
            "last_name": f"Doe {idx}",
            "email": f"jane.doe{idx}@example.com",
            "lifecycle_stage": "customer",
        },
        "orders": {
            "order_id": 3000 + idx,
            "order_number": f"ORD-{3000 + idx:06d}",
            "customer_id": 1000 + idx,
            "status": "completed",
            "total_amount": round(random.uniform(100, 50000), 2),
        },
        "order_items": {
            "item_id": 4000 + idx,
            "order_id": 3000 + idx,
            "product_id": 5000 + idx,
            "quantity": random.randint(1, 100),
            "unit_price": round(random.uniform(1, 500), 4),
        },
        "products": {
            "product_id": 5000 + idx,
            "sku": f"SKU-{5000 + idx:08d}",
            "product_name": f"Widget Model {idx}",
            "is_discontinued": False,
            "unit_price": round(random.uniform(5, 1000), 4),
        },
        "employees": {
            "employee_id": 6000 + idx,
            "first_name": "John",
            "last_name": f"Smith {idx}",
            "email": f"j.smith{idx}@corp.example",
            "is_active": True,
        },
        "financial_transactions": {
            "transaction_id": 7000 + idx,
            "account_code": f"4{idx:04d}",
            "debit_amount": round(random.uniform(0, 10000), 2),
            "currency_code": "USD",
        },
        "inventory_movements": {
            "movement_id": 8000 + idx,
            "product_id": 5000 + idx,
            "warehouse_id": random.randint(1, 5),
            "movement_type": random.choice(["IN", "OUT", "TRANSFER"]),
            "quantity": round(random.uniform(1, 500), 3),
        },
    }
    return base.get(table_name, {"id": idx})


class MockEnterpriseConnector(BaseConnector):
    """Generates realistic synthetic enterprise schemas for testing.

    No external connections are made.  All data is produced in-memory with
    a small artificial delay to simulate network latency.
    """

    SYSTEM_NAME = "mock_enterprise"

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        super().__init__(config, timeout_seconds)
        self._latency_ms: float = config.get("simulated_latency_ms", 50.0)
        self._system_version = "1.0.0-mock"

    async def connect(self) -> bool:
        """Simulate a connection handshake."""
        logger.info("MockEnterpriseConnector: simulating connection …")
        await asyncio.sleep(self._latency_ms / 1000.0)
        self._connected = True
        logger.info("MockEnterpriseConnector: connected")
        return True

    async def disconnect(self) -> None:
        """Simulate connection teardown."""
        await asyncio.sleep(0.01)
        self._connected = False
        logger.info("MockEnterpriseConnector: disconnected")

    async def ping(self) -> float:
        """Return the simulated round-trip latency in ms."""
        start = time.monotonic()
        await asyncio.sleep(self._latency_ms / 1000.0)
        return (time.monotonic() - start) * 1000.0

    async def get_metadata(self) -> SystemMetadata:
        """Return rich mock schema data covering all MOCK_SCHEMAS tables."""
        if not self._connected:
            raise RuntimeError("Not connected – call connect() first")

        latency = await self.ping()
        tables: List[TableSchema] = []
        total_rows = 0

        for table_name, columns in MOCK_SCHEMAS.items():
            row_estimate = _ROW_ESTIMATES.get(table_name, 1000)
            total_rows += row_estimate
            sample = [_make_sample_row(table_name, i) for i in range(3)]
            tables.append(
                TableSchema(
                    name=table_name,
                    columns=columns,
                    row_count_estimate=row_estimate,
                    description=_TABLE_DESCRIPTIONS.get(table_name),
                    sample_data=sample,
                )
            )

        return SystemMetadata(
            system_name=self.SYSTEM_NAME,
            system_version=self._system_version,
            tables=tables,
            entity_count=len(tables),
            total_row_estimate=total_rows,
            connection_latency_ms=latency,
        )

    async def execute_read_query(
        self, query: str, params: Optional[Dict] = None
    ) -> QueryResult:
        """Return mock query results with realistic timing."""
        start = time.monotonic()

        if not await self.validate_read_only(query):
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=0,
                execution_time_ms=elapsed,
                columns=[],
                rows=[],
                error="Query blocked: only read-only SELECT statements are permitted",
            )

        # Simulate query execution delay
        await asyncio.sleep(self._latency_ms / 1000.0 + random.uniform(0.01, 0.05))

        # Synthesize a plausible result set
        columns = ["id", "name", "value", "created_at"]
        rows = [
            {
                "id": i,
                "name": f"mock_row_{i}",
                "value": round(random.uniform(0, 9999), 2),
                "created_at": "2024-01-01T00:00:00Z",
            }
            for i in range(1, 6)
        ]
        elapsed = (time.monotonic() - start) * 1000.0
        return QueryResult(
            query=query,
            row_count=len(rows),
            execution_time_ms=elapsed,
            columns=columns,
            rows=rows,
        )

    async def get_table_schema(self, table_name: str) -> Optional[TableSchema]:
        """Return the mock schema for a specific table, or None if unknown."""
        columns = MOCK_SCHEMAS.get(table_name)
        if columns is None:
            return None
        return TableSchema(
            name=table_name,
            columns=columns,
            row_count_estimate=_ROW_ESTIMATES.get(table_name),
            description=_TABLE_DESCRIPTIONS.get(table_name),
            sample_data=[_make_sample_row(table_name, i) for i in range(3)],
        )
