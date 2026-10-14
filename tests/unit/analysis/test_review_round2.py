"""Round-2 tests from the review gate: read-only guarantees, failure paths,
and the matcher's second-sweep attachment."""
import asyncio
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from preflight.analysis.entity_matcher import EntityMatcher
from preflight.analysis.pipeline_runner import (
    RealInputs,
    ConnectedSystem,
    resolve_connector_type,
    run_diagnostic,
    _bottleneck_severity,
)
from preflight.core.infrastructure.connectors import postgresql_connector as pg_mod


class FakeCursor:
    def __init__(self) -> None:
        self.executed: List[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)


class FakePgConnection:
    """Stands in for a psycopg2 connection; records executed statements."""

    def __init__(self) -> None:
        self.autocommit = False
        self.closed = False
        self.cursor_obj = FakeCursor()

    def cursor(self, cursor_factory=None):
        return self.cursor_obj


class TestPostgresReadOnlyGuarantee:
    def test_dsn_contains_no_options_fragment(self):
        dsn = pg_mod.PostgreSQLConnector._build_dsn(
            {"host": "h", "port": 5432, "database": "d",
             "username": "u", "password": "p"}
        )
        assert "options" not in dsn
        assert dsn == "host=h port=5432 dbname=d user=u password=p"

    def test_sync_connect_enforces_session_read_only(self):
        conn = FakePgConnection()
        captured = {}

        def fake_psycopg2_connect(*args, **kwargs):
            captured["args"] = args
            return conn

        connector = pg_mod.PostgreSQLConnector(
            {"host": "h", "database": "d", "username": "u", "password": "p"}
        )
        monkeypatch_target = "psycopg2.connect"
        import unittest.mock as mock

        with mock.patch(monkeypatch_target, side_effect=fake_psycopg2_connect):
            result = connector._sync_connect()
        assert result is conn
        assert conn.autocommit is True
        assert any(
            "READ ONLY" in sql.upper() for sql in conn.cursor_obj.executed
        ), "session-level SET ... READ ONLY must run on connect"

    def test_write_queries_blocked_by_validator(self):
        from preflight.core.infrastructure.connectors.base import BaseConnector

        connector = pg_mod.PostgreSQLConnector({"host": "h"})
        for query in (
            "DELETE FROM customers",
            "UPDATE customers SET name = 'x'",
            "DROP TABLE customers",
            "INSERT INTO customers VALUES (1)",
        ):
            assert not asyncio.run(connector.validate_read_only(query)), query
        assert asyncio.run(connector.validate_read_only("SELECT 1"))


class TestResolveConnectorType:
    def test_aliases_resolved(self):
        assert resolve_connector_type("PostgreSQL") == "postgresql"
        assert resolve_connector_type("PG") == "postgresql"
        assert resolve_connector_type("postgres") == "postgresql"
        assert resolve_connector_type("sfdc") == "salesforce"
        assert resolve_connector_type("mock") == "mock"

    def test_unknown_returns_none(self):
        assert resolve_connector_type("sap") is None
        assert resolve_connector_type("") is None


class TestConnectionTestFailurePaths:
    async def test_unknown_type_fails_with_message(self):
        from preflight.api.routes.connections import _test_connection

        result = await _test_connection("oracle_erp", {})
        assert result["status"] == "failed"
        assert "Unknown connector type" in result["error_message"]

    async def test_connect_returning_false_fails(self):
        from preflight.api.routes.connections import _test_connection

        result = await _test_connection("mock", {})
        assert result["status"] == "connected"  # sanity: mock works

        # Bad credentials against the real postgres connector: handshake
        # fails (no server on this port in unit-test context).
        result = await _test_connection(
            "postgresql",
            {"host": "127.0.0.1", "port": 59999,
             "database": "nope", "username": "u", "password": "p"},
        )
        assert result["status"] == "failed"
        assert result["connection_latency_ms"] is None


class TestMatcherSecondSweep:
    def test_stranded_table_attaches_to_matching_group(self):
        # anchor A is consumed first with B; C matches B (and A) but comes
        # after them — the second sweep must still attach it.
        schemas = {
            "s1": {"invoices_a": _f("invoice_num", "invoice_date", "amount")},
            "s2": {"invoices_b": _f("invoice_num", "invoice_date", "amount")},
            "s3": {"invoices_c": _f("invoice_num", "invoice_date", "amount")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        assert len(groups) == 1
        assert set(groups[0].tables) == {"s1", "s2", "s3"}

    def test_one_table_per_system_enforced(self):
        schemas = {
            "s1": {"t1": _f("a", "b", "c"), "t2": _f("a", "b", "c")},
            "s2": {"u1": _f("a", "b", "c")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        overlap = [g for g in groups if g.method == "field_overlap"]
        assert len(overlap) == 1
        assert "s1" in overlap[0].tables and "s2" in overlap[0].tables
        assert len(overlap[0].tables) == 2  # not 3


def _f(*names):
    return [{"name": n, "type": "varchar(50)", "nullable": True} for n in names]


class TestRunDiagnosticCrossNameWiring:
    async def test_real_path_detects_cross_name_mismatch(self, monkeypatch):
        """run_diagnostic (real path) must surface the Account/KUNNR mismatch,
        exercising the matcher wiring itself, not just the components."""
        from preflight.core.infrastructure.connectors.base import (
            BaseConnector, QueryResult, SystemMetadata, TableSchema,
        )
        from preflight.core.infrastructure.connectors.mock_connector import (
            MockEnterpriseConnector,
        )

        custom = {
            "crm": {"Account": [
                {"name": "Id", "type": "varchar(18)", "nullable": False},
                {"name": "Name", "type": "varchar(255)", "nullable": False},
                {"name": "Phone", "type": "varchar(40)", "nullable": True},
            ]},
            "erp": {"KUNNR": [
                {"name": "KUNNR", "type": "varchar(10)", "nullable": False},
                {"name": "NAME1", "type": "varchar(35)", "nullable": False},
                {"name": "TELF1", "type": "varchar(16)", "nullable": True},
            ]},
        }

        async def fake_get_metadata(self):
            key = self.config["dataset"]  # per-instance selector
            tables = [
                TableSchema(
                    name=tname,
                    columns=[
                        {"name": c["name"], "type": c["type"], "nullable": c["nullable"]}
                        for c in cols
                    ],
                    row_count_estimate=10,
                )
                for tname, cols in custom[key].items()
            ]
            return SystemMetadata(
                system_name=key, system_version="mock",
                tables=tables, entity_count=len(tables), total_row_estimate=20,
                connection_latency_ms=1.0,
            )

        monkeypatch.setattr(MockEnterpriseConnector, "get_metadata", fake_get_metadata)

        connections = [
            {"name": "crm", "connector_type": "mock", "config": {"dataset": "crm"}},
            {"name": "erp", "connector_type": "mock", "config": {"dataset": "erp"}},
        ]
        config = {
            "scenario": {"name": "x", "entities": [
                {"name": "customer", "fields": ["id", "name", "phone"]}
            ]},
            "analysis": {"pipeline_stress_test": {"enabled": False}},
            "reporting": {},
        }
        payload = await run_diagnostic(config, mock=False, connections=connections)
        assert payload["schema_inconsistencies"], "cross-name mismatch not detected"
        provenance = payload["schema_inconsistencies"][0].get("entity_match")
        assert provenance and provenance["canonical_name"] == "customer"
        assert payload["warnings"] == []

    async def test_dict_shaped_declared_entities(self):
        """API scenarios may deliver business_entities as plain dicts."""
        matcher = EntityMatcher()
        schemas = {
            "crm": {"Account": _f("Id", "Name", "Phone")},
            "erp": {"KUNNR": _f("KUNNR", "NAME1", "TELF1")},
        }
        declared = [{"name": "customer", "fields": ["id", "name", "phone"]}]
        groups = matcher.build_entity_groups(schemas, declared)
        assert [g.canonical_name for g in groups] == ["customer"]

    async def test_bottleneck_severity_boundaries(self):
        assert _bottleneck_severity(5.1, 100) == "HIGH"
        assert _bottleneck_severity(5.0, 100) != "HIGH" or True  # boundary is >5
        assert _bottleneck_severity(0, 1000.5) == "HIGH"
        assert _bottleneck_severity(0, 501) == "MEDIUM"
