"""Salesforce CRM connector for Preflight diagnostics.

Uses simple_salesforce when available; falls back to MockSalesforceConnector
that generates plausible Salesforce schema data for testing.
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
    from simple_salesforce import Salesforce, SalesforceLogin, SFType

    _SF_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SF_AVAILABLE = False
    logger.warning(
        "simple_salesforce not available – SalesforceConnector will use MockSalesforceConnector"
    )

# ---------------------------------------------------------------------------
# Plausible Salesforce object definitions for the mock
# ---------------------------------------------------------------------------

_SF_OBJECTS: Dict[str, Dict[str, Any]] = {
    "Account": {
        "description": "Companies and organisations in Salesforce",
        "fields": [
            {"name": "Id", "type": "id", "nullable": False, "pk": True, "fk_to": None},
            {"name": "Name", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Type", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Industry", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "AnnualRevenue", "type": "currency", "nullable": True, "pk": False, "fk_to": None},
            {"name": "NumberOfEmployees", "type": "int", "nullable": True, "pk": False, "fk_to": None},
            {"name": "BillingCity", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "BillingCountry", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Phone", "type": "phone", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Website", "type": "url", "nullable": True, "pk": False, "fk_to": None},
            {"name": "OwnerId", "type": "reference", "nullable": False, "pk": False, "fk_to": "User.Id"},
            {"name": "CreatedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "LastModifiedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsDeleted", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
        ],
        "row_estimate": 28_400,
    },
    "Contact": {
        "description": "Individual people associated with Accounts",
        "fields": [
            {"name": "Id", "type": "id", "nullable": False, "pk": True, "fk_to": None},
            {"name": "FirstName", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "LastName", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Email", "type": "email", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Phone", "type": "phone", "nullable": True, "pk": False, "fk_to": None},
            {"name": "MobilePhone", "type": "phone", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Title", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Department", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "AccountId", "type": "reference", "nullable": True, "pk": False, "fk_to": "Account.Id"},
            {"name": "ReportsToId", "type": "reference", "nullable": True, "pk": False, "fk_to": "Contact.Id"},
            {"name": "LeadSource", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "OwnerId", "type": "reference", "nullable": False, "pk": False, "fk_to": "User.Id"},
            {"name": "CreatedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsDeleted", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
        ],
        "row_estimate": 94_700,
    },
    "Lead": {
        "description": "Prospective customers not yet converted to Contacts",
        "fields": [
            {"name": "Id", "type": "id", "nullable": False, "pk": True, "fk_to": None},
            {"name": "FirstName", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "LastName", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Company", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Email", "type": "email", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Phone", "type": "phone", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Status", "type": "picklist", "nullable": False, "pk": False, "fk_to": None},
            {"name": "LeadSource", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Industry", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Rating", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "IsConverted", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
            {"name": "ConvertedAccountId", "type": "reference", "nullable": True, "pk": False, "fk_to": "Account.Id"},
            {"name": "OwnerId", "type": "reference", "nullable": False, "pk": False, "fk_to": "User.Id"},
            {"name": "CreatedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsDeleted", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
        ],
        "row_estimate": 312_000,
    },
    "Opportunity": {
        "description": "Sales deals and revenue opportunities",
        "fields": [
            {"name": "Id", "type": "id", "nullable": False, "pk": True, "fk_to": None},
            {"name": "Name", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "AccountId", "type": "reference", "nullable": True, "pk": False, "fk_to": "Account.Id"},
            {"name": "StageName", "type": "picklist", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Amount", "type": "currency", "nullable": True, "pk": False, "fk_to": None},
            {"name": "CloseDate", "type": "date", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Probability", "type": "percent", "nullable": True, "pk": False, "fk_to": None},
            {"name": "ForecastCategory", "type": "picklist", "nullable": False, "pk": False, "fk_to": None},
            {"name": "LeadSource", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Type", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "IsClosed", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsWon", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
            {"name": "OwnerId", "type": "reference", "nullable": False, "pk": False, "fk_to": "User.Id"},
            {"name": "CreatedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "LastModifiedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsDeleted", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
        ],
        "row_estimate": 67_800,
    },
    "Case": {
        "description": "Customer support cases and service requests",
        "fields": [
            {"name": "Id", "type": "id", "nullable": False, "pk": True, "fk_to": None},
            {"name": "CaseNumber", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Subject", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Description", "type": "textarea", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Status", "type": "picklist", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Priority", "type": "picklist", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Origin", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "AccountId", "type": "reference", "nullable": True, "pk": False, "fk_to": "Account.Id"},
            {"name": "ContactId", "type": "reference", "nullable": True, "pk": False, "fk_to": "Contact.Id"},
            {"name": "OwnerId", "type": "reference", "nullable": False, "pk": False, "fk_to": "User.Id"},
            {"name": "IsClosed", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
            {"name": "CreatedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "LastModifiedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsDeleted", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
        ],
        "row_estimate": 198_500,
    },
    "Task": {
        "description": "Activity records: calls, emails, to-dos",
        "fields": [
            {"name": "Id", "type": "id", "nullable": False, "pk": True, "fk_to": None},
            {"name": "Subject", "type": "combobox", "nullable": True, "pk": False, "fk_to": None},
            {"name": "Status", "type": "picklist", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Priority", "type": "picklist", "nullable": False, "pk": False, "fk_to": None},
            {"name": "ActivityDate", "type": "date", "nullable": True, "pk": False, "fk_to": None},
            {"name": "WhoId", "type": "reference", "nullable": True, "pk": False, "fk_to": "Contact.Id"},
            {"name": "WhatId", "type": "reference", "nullable": True, "pk": False, "fk_to": None},
            {"name": "OwnerId", "type": "reference", "nullable": False, "pk": False, "fk_to": "User.Id"},
            {"name": "Description", "type": "textarea", "nullable": True, "pk": False, "fk_to": None},
            {"name": "IsClosed", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
            {"name": "CreatedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsDeleted", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
        ],
        "row_estimate": 1_420_000,
    },
    "User": {
        "description": "Salesforce platform users",
        "fields": [
            {"name": "Id", "type": "id", "nullable": False, "pk": True, "fk_to": None},
            {"name": "Username", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "FirstName", "type": "string", "nullable": True, "pk": False, "fk_to": None},
            {"name": "LastName", "type": "string", "nullable": False, "pk": False, "fk_to": None},
            {"name": "Email", "type": "email", "nullable": False, "pk": False, "fk_to": None},
            {"name": "IsActive", "type": "boolean", "nullable": False, "pk": False, "fk_to": None},
            {"name": "ProfileId", "type": "reference", "nullable": False, "pk": False, "fk_to": "Profile.Id"},
            {"name": "UserType", "type": "picklist", "nullable": True, "pk": False, "fk_to": None},
            {"name": "CreatedDate", "type": "datetime", "nullable": False, "pk": False, "fk_to": None},
        ],
        "row_estimate": 450,
    },
}


class SalesforceConnector(BaseConnector):
    """Read-only Salesforce CRM connector using simple_salesforce.

    Authenticates via username/password/security-token flow (or OAuth2
    instance URL + session ID when provided in config).  All queries are
    SOQL SELECT statements validated before execution.
    """

    SYSTEM_NAME = "salesforce"

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        super().__init__(config, timeout_seconds)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sf_conn")
        self._sf: Optional[Any] = None  # simple_salesforce.Salesforce instance

    async def connect(self) -> bool:
        if not _SF_AVAILABLE:
            raise RuntimeError(
                "simple_salesforce is not installed.  Use MockSalesforceConnector instead."
            )
        try:
            cfg = self.config

            def _do_connect():
                kwargs: Dict[str, Any] = {}
                if "instance_url" in cfg and "session_id" in cfg:
                    kwargs = {
                        "instance_url": cfg["instance_url"],
                        "session_id": cfg["session_id"],
                    }
                else:
                    kwargs = {
                        "username": cfg["username"],
                        "password": cfg["password"],
                        "security_token": cfg.get("security_token", ""),
                        "domain": cfg.get("domain", "login"),
                    }
                return Salesforce(**kwargs)

            loop = asyncio.get_event_loop()
            self._sf = await asyncio.wait_for(
                loop.run_in_executor(self._executor, _do_connect),
                timeout=self.timeout_seconds,
            )
            self._connected = True
            logger.info("Salesforce connection established")
            return True
        except Exception as exc:
            logger.error("Salesforce connection failed: %s", exc)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        self._sf = None
        self._connected = False
        self._executor.shutdown(wait=False)

    async def ping(self) -> float:
        if not self._connected or self._sf is None:
            raise RuntimeError("Not connected")
        start = time.monotonic()

        def _ping():
            # Lightweight describe call
            self._sf.describe()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, _ping)
        return (time.monotonic() - start) * 1000.0

    def _describe_object(self, object_name: str) -> Dict[str, Any]:
        sf_type = SFType(object_name, self._sf.session_id, self._sf.sf_instance)
        return sf_type.describe()

    async def get_entity_fields(self, object_name: str) -> List[Dict[str, Any]]:
        """Return full field definitions for a Salesforce sObject."""
        loop = asyncio.get_event_loop()
        described = await loop.run_in_executor(
            self._executor, self._describe_object, object_name
        )
        fields = []
        for f in described.get("fields", []):
            fk_to = None
            refs = f.get("referenceTo", [])
            if refs:
                fk_to = refs[0] + ".Id"
            fields.append(
                {
                    "name": f["name"],
                    "type": f["type"],
                    "nullable": f.get("nillable", True),
                    "pk": f.get("idLookup", False) and f["type"] == "id",
                    "fk_to": fk_to,
                    "label": f.get("label"),
                    "length": f.get("length"),
                    "createable": f.get("createable", False),
                    "updateable": f.get("updateable", False),
                }
            )
        return fields

    async def get_metadata(self) -> SystemMetadata:
        if not self._connected or self._sf is None:
            raise RuntimeError("Not connected")

        loop = asyncio.get_event_loop()
        latency = await self.ping()

        def _global_describe():
            return self._sf.describe()

        described = await loop.run_in_executor(self._executor, _global_describe)
        sobjects = described.get("sobjects", [])

        tables: List[TableSchema] = []
        for obj in sobjects:
            if not obj.get("queryable"):
                continue
            name = obj["name"]
            try:
                fields = await self.get_entity_fields(name)
            except Exception as exc:
                logger.warning("Could not describe %s: %s", name, exc)
                fields = []
            tables.append(
                TableSchema(
                    name=name,
                    columns=fields,
                    description=obj.get("label"),
                )
            )

        return SystemMetadata(
            system_name=self.SYSTEM_NAME,
            tables=tables,
            entity_count=len(tables),
            connection_latency_ms=latency,
        )

    async def execute_read_query(
        self, query: str, params: Optional[Dict] = None
    ) -> QueryResult:
        """Execute a SOQL SELECT query."""
        start = time.monotonic()

        # SOQL validation: must start with SELECT
        query_stripped = query.strip().upper()
        if not query_stripped.startswith("SELECT"):
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=0,
                execution_time_ms=elapsed,
                columns=[],
                rows=[],
                error="Only SOQL SELECT queries are permitted",
            )

        def _run_soql():
            return self._sf.query_all(query)

        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(self._executor, _run_soql),
                timeout=self.timeout_seconds,
            )
            rows = result.get("records", [])
            # Remove Salesforce metadata attributes
            for row in rows:
                row.pop("attributes", None)
            cols = list(rows[0].keys()) if rows else []
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
                error=f"SOQL query timed out after {self.timeout_seconds}s",
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


class MockSalesforceConnector(BaseConnector):
    """Mock Salesforce connector that generates plausible CRM schema data."""

    SYSTEM_NAME = "salesforce"

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        super().__init__(config, timeout_seconds)
        logger.info("MockSalesforceConnector: simple_salesforce not available, using mock data")

    async def connect(self) -> bool:
        await asyncio.sleep(0.08)
        self._connected = True
        return True

    async def disconnect(self) -> None:
        await asyncio.sleep(0.01)
        self._connected = False

    async def ping(self) -> float:
        start = time.monotonic()
        await asyncio.sleep(0.04)
        return (time.monotonic() - start) * 1000.0

    async def get_metadata(self) -> SystemMetadata:
        latency = await self.ping()
        tables: List[TableSchema] = []
        total_rows = 0

        for obj_name, obj_def in _SF_OBJECTS.items():
            row_est = obj_def.get("row_estimate", 1000)
            total_rows += row_est
            tables.append(
                TableSchema(
                    name=obj_name,
                    columns=obj_def["fields"],
                    row_count_estimate=row_est,
                    description=obj_def.get("description"),
                )
            )

        return SystemMetadata(
            system_name=self.SYSTEM_NAME,
            system_version="Salesforce Spring '24 (mock)",
            tables=tables,
            entity_count=len(tables),
            total_row_estimate=total_rows,
            connection_latency_ms=latency,
        )

    async def execute_read_query(
        self, query: str, params: Optional[Dict] = None
    ) -> QueryResult:
        start = time.monotonic()
        query_stripped = query.strip().upper()
        if not query_stripped.startswith("SELECT"):
            elapsed = (time.monotonic() - start) * 1000.0
            return QueryResult(
                query=query,
                row_count=0,
                execution_time_ms=elapsed,
                columns=[],
                rows=[],
                error="Only SOQL SELECT queries are permitted",
            )
        await asyncio.sleep(0.06)
        elapsed = (time.monotonic() - start) * 1000.0
        return QueryResult(
            query=query,
            row_count=2,
            execution_time_ms=elapsed,
            columns=["Id", "Name", "CreatedDate"],
            rows=[
                {"Id": "001000000000001", "Name": "Acme Corp", "CreatedDate": "2023-01-01T00:00:00.000+0000"},
                {"Id": "001000000000002", "Name": "Globex Inc", "CreatedDate": "2023-02-15T00:00:00.000+0000"},
            ],
        )

    async def get_entity_fields(self, object_name: str) -> List[Dict[str, Any]]:
        """Return field definitions for the given mock sObject."""
        obj_def = _SF_OBJECTS.get(object_name)
        if obj_def is None:
            return []
        return obj_def["fields"]


def get_salesforce_connector(
    config: Dict[str, Any], timeout_seconds: int = 30
) -> BaseConnector:
    """Return the real connector when simple_salesforce is available, mock otherwise."""
    if _SF_AVAILABLE:
        return SalesforceConnector(config, timeout_seconds)
    return MockSalesforceConnector(config, timeout_seconds)
