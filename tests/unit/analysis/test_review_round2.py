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
    def test_dsn_is_kwargs_dict_no_injection_surface(self):
        """Connection parameters must be a kwargs dict, not a conninfo
        string: unquoted DSN assembly allowed crafted values to inject
        extra parameters (e.g. database="x host=evil.example.com")."""
        params = pg_mod.PostgreSQLConnector._build_dsn(
            {"host": "h", "port": 5432, "database": "d",
             "username": "u", "password": "p"}
        )
        assert params == {
            "host": "h", "port": 5432, "dbname": "d",
            "user": "u", "password": "p",
        }

    def test_conninfo_injection_cannot_smuggle_host(self):
        """A crafted database value must land in ONE kwargs value, never
        re-parsed as connection keywords."""
        import unittest.mock as mock

        captured = {}

        def fake_connect(**kwargs):
            captured.update(kwargs)
            return FakePgConnection()

        connector = pg_mod.PostgreSQLConnector(
            {"host": "db.internal", "database": "x host=evil.example.com",
             "username": "u", "password": "p"}
        )
        with mock.patch("psycopg2.connect", side_effect=fake_connect):
            connector._sync_connect()
        # The hostile string survives intact as the dbname VALUE — libpq
        # never sees it as a keyword.
        assert captured["dbname"] == "x host=evil.example.com"
        assert captured["host"] == "db.internal"

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

    async def test_connect_returning_false_fails(self, monkeypatch):
        """Deterministic: factory returns a connector whose connect() is
        False — no network, no psycopg2 dependency."""
        from preflight.api.routes import connections as conn_routes

        class NoConnect:
            async def connect(self):
                return False

            async def disconnect(self):
                pass

        monkeypatch.setattr(
            conn_routes, "create_connector", lambda *a, **k: NoConnect()
        )
        result = await conn_routes._test_connection("postgresql", {})
        assert result["status"] == "failed"
        assert result["connection_latency_ms"] is None

    async def test_connector_creation_crash_is_failed_not_500(self, monkeypatch):
        from preflight.api.routes import connections as conn_routes

        def exploding(*a, **k):
            raise TypeError("bad config shape")

        monkeypatch.setattr(conn_routes, "create_connector", exploding)
        result = await conn_routes._test_connection("postgresql", {"host": 42})
        assert result["status"] == "failed"
        assert "could not be created" in result["error_message"]

    async def test_host_allowlist_enforced(self, monkeypatch):
        from preflight.api.routes import connections as conn_routes

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal,crm.internal")
        result = await conn_routes._test_connection(
            "postgresql", {"host": "evil.example.com"}
        )
        assert result["status"] == "failed"
        assert "not in the allowed hosts list" in result["error_message"]

        # Allowed host proceeds to the (mocked) handshake.
        class OkConnect:
            async def connect(self):
                return True

            async def ping(self):
                return 1.0

            async def get_metadata(self):
                meta = MagicMock()
                meta.entity_count = 3
                return meta

            async def disconnect(self):
                pass

        monkeypatch.setattr(
            conn_routes, "create_connector", lambda *a, **k: OkConnect()
        )
        result = await conn_routes._test_connection(
            "postgresql", {"host": "db.internal"}
        )
        assert result["status"] == "connected"



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

    async def test_declared_path_matches_sap_all_caps_fields(self):
        """Declared matching must link SAP all-caps tables WITHOUT the seed
        vocabulary filling in — regression for the duplicated normalizer:
        with the old regex, NAME1 normalizes to n_a_m_e1 and 'name' vs
        'n_a_m_e1' scores 0.67 < 0.8, so the ERP side never reaches the
        0.3 group threshold and no declared group forms."""
        matcher = EntityMatcher()
        # Neither table name is in the seed vocabulary.
        schemas = {
            "crm": {"Kundenstamm": _f("Name", "Phone")},
            "erp": {"DEBI_MST": _f("NAME1", "TELF1")},
        }
        declared = [{"name": "customer", "fields": ["name", "phone"]}]
        groups = matcher.build_entity_groups(schemas, declared)
        declared_groups = [g for g in groups if g.method == "declared"]
        assert len(declared_groups) == 1, (
            "declared pass must match SAP all-caps fields via shared "
            "normalization; got methods="
            + str([g.method for g in groups])
        )
        assert declared_groups[0].canonical_name == "customer"
        assert set(declared_groups[0].tables) == {"crm", "erp"}
        # NAME1<->name must have paired (0.889 with the shared normalizer),
        # so the ERP side matched 1 of 2 declared fields.
        assert declared_groups[0].confidence >= 0.5

    async def test_string_entries_in_declared_entities_ignored(self):
        schemas = {
            "crm": {"Account": _f("Id", "Name")},
            "erp": {"KUNNR": _f("KUNNR", "NAME1")},
        }
        groups = EntityMatcher().build_entity_groups(
            schemas, ["customer", {"name": "order", "fields": "not-a-list"}]
        )
        # vocabulary still links Account/KUNNR; nothing crashed on the strings
        assert any(g.canonical_name == "customer" for g in groups)

    async def test_bottleneck_severity_boundaries(self):
        assert _bottleneck_severity(5.1, 100) == "HIGH"
        assert _bottleneck_severity(5.0, 100) == "LOW"  # boundary is strictly >5
        assert _bottleneck_severity(0, 1000.5) == "HIGH"
        assert _bottleneck_severity(0, 501) == "MEDIUM"
        assert _bottleneck_severity(2.1, 100) == "LOW"
        assert _bottleneck_severity(2.0, 100) == "INFO"


class TestStressProbeSql:
    def test_salesforce_gets_valid_soql(self):
        from preflight.analysis.pipeline_runner import _STRESS_PROBE_SQL

        soql = _STRESS_PROBE_SQL["salesforce"]
        assert " FROM " in soql and "SELECT" in soql.upper()
        assert _STRESS_PROBE_SQL["postgresql"] == "SELECT 1"

    def test_probe_present_for_every_registered_real_type(self):
        from preflight.analysis.pipeline_runner import (
            _STRESS_PROBE_SQL,
            resolve_connector_type,
        )
        from preflight.core.infrastructure.connectors.connector_factory import (
            list_connector_types,
        )

        for registered in list_connector_types():
            canonical = resolve_connector_type(registered) or registered
            if canonical == "mock":
                continue
            assert canonical in _STRESS_PROBE_SQL, registered


class TestNormalizedFieldShared:
    def test_single_source_of_truth(self):
        from preflight.analysis.entity_matcher import normalize_field_name
        from preflight.analysis.schema_analyzer import SchemaAnalyzer

        for name in ("NAME1", "KUNNR", "AnnualRevenue", "AccountId", "TELF1"):
            assert normalize_field_name(name) == SchemaAnalyzer().normalize_field_name(name)
        assert normalize_field_name("NAME1") == "name1"


class TestRound4Fixes:
    def test_same_canonical_name_groups_not_dropped(self):
        """Exact 'customer' {A,B} + vocabulary 'customer' {C,D} must BOTH
        produce findings — keying by canonical name alone dropped one."""
        from preflight.analysis.entity_matcher import EntityMatcher
        from preflight.analysis.schema_analyzer import SchemaAnalyzer

        schemas = {
            "a": {"customer": _f("id", "name", "email")},
            "b": {"customers": _f("id", "name", "phone")},  # exact w/ a
            "c": {"Account": _f("Id", "Name")},
            "d": {"KUNNR": _f("KUNNR", "NAME1")},  # vocabulary w/ c
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        customer_groups = [g for g in groups if g.canonical_name == "customer"]
        # Since vocabulary groups extend same-name groups with per-link
        # provenance, all four systems form ONE group.
        assert len(customer_groups) == 1
        group = customer_groups[0]
        assert set(group.tables) == {"a", "b", "c", "d"}
        assert group.link_methods["a"] == "exact"
        assert group.link_methods["c"] == "vocabulary"
        assert group.method == "vocabulary"  # weakest link

        analyzer = SchemaAnalyzer()
        results = analyzer.analyze_entity_groups(schemas, groups)
        inconsistencies = analyzer.generate_inconsistency_report(results)
        # Every system pair contributes findings — nothing dropped.
        pairs = {(i["source"], i["target"]) for i in inconsistencies}
        assert ("a", "b") in pairs, f"exact-pair findings dropped: {pairs}"
        assert ("c", "d") in pairs, f"vocabulary-pair findings dropped: {pairs}"

    def test_field_overlap_first_wins_on_member_side(self):
        """s2 has two tables matching the anchor; the FIRST stays in the
        group and the second must not be silently lost to `used`."""
        schemas = {
            "s1": {"t1": _f("invoice_num", "invoice_date", "amount")},
            "s2": {
                "u1": _f("invoice_num", "invoice_date", "amount"),  # first
                "u2": _f("invoice_num", "invoice_date", "amount"),  # duplicate
            },
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        overlap = [g for g in groups if g.method == "field_overlap"]
        assert len(overlap) == 1
        assert overlap[0].tables == {"s1": "t1", "s2": "u1"}

    async def test_runner_warnings_never_leak_exception_text(self, monkeypatch):
        """Warnings reach API callers; they must be coarse, not raw driver
        errors (which can contain host/port details)."""
        from preflight.analysis import pipeline_runner as pr

        class ExplodingConnector:
            def __init__(self, *a, **k):
                pass

            async def connect(self):
                raise RuntimeError(
                    "connection to server at '10.0.0.5', port 5432 failed: "
                    "FATAL: password authentication failed for user 'prod'"
                )

            async def disconnect(self):
                pass

        real_factory = pr.create_connector

        def factory(ctype, config, **kwargs):
            if ctype == "postgresql":
                return ExplodingConnector()
            return real_factory(ctype, config, **kwargs)

        monkeypatch.setattr(pr, "create_connector", factory)
        connections = [
            {"name": "demo", "connector_type": "mock", "config": {}},
            {"name": "prod", "connector_type": "postgresql", "config": {}},
        ]
        inputs = await pr.collect_real_inputs(connections, {})
        joined = " | ".join(inputs.warnings)
        assert "10.0.0.5" not in joined and "prod'" not in joined
        assert "connection failed (check host/credentials)" in joined

    async def test_connect_raising_leads_to_pipeline_error(self, monkeypatch):
        """All connections fail at connect() (not 'unsupported type') ->
        PipelineError; distinct from the unsupported-type skip path."""
        from preflight.analysis import pipeline_runner as pr

        class FailingConnector:
            def __init__(self, *a, **k):
                pass

            async def connect(self):
                return False

            async def disconnect(self):
                pass

        monkeypatch.setattr(
            pr, "create_connector", lambda *a, **k: FailingConnector()
        )
        connections = [{"name": "prod", "connector_type": "postgresql", "config": {}}]
        with pytest.raises(pr.PipelineError):
            await pr.collect_real_inputs(connections, {})


class TestApiDiagnosticsMapping:
    async def test_pipeline_rows_mapped_to_response_shape(self, monkeypatch):
        """API-level: measured bottleneck rows survive the response model."""
        import json
        from fastapi.testclient import TestClient
        from preflight.api.dependencies import (
            _connections_store, _runs_store, _reports_store,
        )
        from preflight.api.app import create_app
        from preflight.analysis import pipeline_runner

        _connections_store.clear(); _runs_store.clear(); _reports_store.clear()
        app = create_app()

        async def fake_run_diagnostic(config, mock=False, progress_cb=None, connections=None):
            return {
                "readiness_score": 90.0, "verdict": "GO",
                "scenario_name": "t", "executive_summary": "s",
                "schema_inconsistencies": [], "middleware_gaps": [],
                "pipeline_bottlenecks": [
                    {"system": "db", "error_rate_pct": 0.0,
                     "p95_ms": 12.5, "actual_qps": 8.0, "severity": "INFO"}
                ],
                "remediation_plan": [], "critical_count": 0,
                "total_issues": 1, "remediation_weeks": "4",
                "entity_matches": [], "warnings": ["db: coarse warning"],
            }

        monkeypatch.setattr(
            pipeline_runner, "run_diagnostic", fake_run_diagnostic
        )

        with TestClient(app) as client:
            conn = client.post("/connections", json={
                "name": "db", "connector_type": "mock",
                "system_type": "DATABASE", "config": {},
            }).json()
            run = client.post("/diagnostics", json={
                "name": "mapping-test",
                "scenario": {"name": "t", "target_systems": ["db"]},
                "connection_ids": [conn["id"]],
            }).json()
            # BackgroundTasks in TestClient run before the response returns;
            # poll briefly in case of scheduling.
            for _ in range(20):
                report = client.get(f"/reports/{run['id']}")
                if report.status_code == 200:
                    break
                import time as _t; _t.sleep(0.1)
            assert report.status_code == 200, report.text
            rows = report.json()["pipeline_bottlenecks"]
            assert rows and rows[0]["system"] == "db"
            assert rows[0]["bottleneck_type"] == "stress_test"
            assert "p95" in rows[0]["description"]
            assert rows[0]["p95_latency_ms"] == 12.5


class TestRound5Fixes:
    def test_second_sweep_genuinely_stranded_case(self):
        """C matches member B but NOT anchor A: without the 4b sweep this
        test fails (C lands in no group)."""
        schemas = {
            "s1": {"anchor_t": _f("p", "q", "r")},
            "s2": {"member_u": _f("p", "q", "r", "s", "t")},
            "s3": {"stranded_v": _f("s", "t", "u", "v")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        big = [g for g in groups if "s3" in g.tables]
        assert big, "stranded table dropped"
        assert set(big[0].tables) == {"s1", "s2", "s3"}
        # attach score folded into confidence
        assert big[0].confidence > 0

    def test_sibling_tables_not_silently_skipped(self):
        """'customer' and 'customers' in one system: the sibling must stay
        available to the field-overlap pass instead of being swallowed by a
        normalized-name key collision."""
        schemas = {
            "s1": {
                "customer": _f("id", "name"),  # taken by vocabulary below
                "customers": _f("id", "name", "email", "phone"),
            },
            "s2": {
                "KUNNR": _f("KUNNR", "NAME1"),  # vocabulary partner for s1
                "clients": _f("id", "name", "email", "phone"),
            },
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        vocab = [g for g in groups if g.method == "vocabulary"]
        assert vocab and set(vocab[0].tables) == {"s1", "s2"}
        overlap = [g for g in groups if g.method == "field_overlap"]
        assert overlap, "sibling 'customers' was silently skipped"
        assert overlap[0].tables == {"s1": "customers", "s2": "clients"}

    def test_second_sweep_does_not_extend_vocabulary_groups(self):
        """Provenance: a field-overlap attachment to a vocabulary group
        must not masquerade as a vocabulary link."""
        schemas = {
            "s1": {"Account": _f("Id", "Name")},
            "s2": {"KUNNR": _f("KUNNR", "NAME1")},
            "s3": {"weird_tbl": _f("Id", "Name", "extra1", "extra2")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        vocab = [g for g in groups if g.method == "vocabulary"]
        assert vocab and "s3" not in vocab[0].tables

    def test_perf_guard_large_estate(self):
        """500 tables x 20 fields x 2 systems must finish fast (cached
        normalization + exact-field prefilter + candidate cap)."""
        import time as _time

        schemas = {}
        for sys_name in ("a", "b"):
            schemas[sys_name] = {
                f"t{i}": _f(*(f"shared_col_{j}" for j in range(20)))
                for i in range(250)
            }
        start = _time.monotonic()
        groups = EntityMatcher().build_entity_groups(schemas)
        elapsed = _time.monotonic() - start
        assert elapsed < 30, f"matcher too slow: {elapsed:.1f}s"
        assert groups, "large estate produced no groups"

    def test_candidate_cap_skips_quadratic_pass(self):
        # Unique table names per system (no exact-name groups) AND unique
        # field names (no shared exact fields) — the cap alone stands
        # between this and a 90k-pair quadratic comparison.
        schemas = {
            sys_name: {f"{sys_name}_t{i}": _f(*[f"c{j}_{i}" for j in range(5)])
                       for i in range(300)}
            for sys_name in ("a", "b")
        }
        matcher = EntityMatcher(max_field_overlap_candidates=10)
        groups = matcher.build_entity_groups(schemas)
        assert groups == []


class TestCacheLifecycle:
    def test_close_removes_private_tempdir(self):
        from preflight.core.infrastructure.cache import (
            _DiskCacheBackend, _OWNED_TEMPDIRS,
        )
        import os

        backend = _DiskCacheBackend()
        directory = backend._cache.directory
        assert directory in _OWNED_TEMPDIRS and os.path.isdir(directory)
        backend.close()
        assert directory not in _OWNED_TEMPDIRS
        assert not os.path.isdir(directory)

    def test_explicit_directory_survives_close(self):
        import tempfile
        from preflight.core.infrastructure.cache import _DiskCacheBackend
        import os

        with tempfile.TemporaryDirectory() as tmp:
            backend = _DiskCacheBackend(directory=tmp)
            backend.set("k", "v", ttl=None)
            backend.close()
            assert os.path.isdir(tmp)  # caller-owned directory untouched


class TestCancellationCleanup:
    async def test_cancel_during_collect_disconnects(self, monkeypatch):
        from preflight.analysis import pipeline_runner as pr

        disconnects = []

        class SlowConnector:
            async def connect(self):
                await asyncio.sleep(30)
                return True

            async def disconnect(self):
                disconnects.append("called")

        class FastConnector:
            async def connect(self):
                return True

            async def get_metadata(self):
                meta = MagicMock()
                meta.tables = []
                return meta

            async def disconnect(self):
                disconnects.append("fast")

        def factory(ctype, config, **kw):
            return SlowConnector() if ctype == "postgresql" else FastConnector()

        monkeypatch.setattr(pr, "create_connector", factory)
        connections = [
            {"name": "fast", "connector_type": "mock", "config": {}},
            {"name": "slow", "connector_type": "postgresql", "config": {}},
        ]
        task = asyncio.get_event_loop().create_task(
            pr.collect_real_inputs(connections, {})
        )
        await asyncio.sleep(0.2)  # let 'fast' complete, hit 'slow'
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert "fast" in disconnects, (
            "cancellation must disconnect already-connected systems"
        )
        assert "called" in disconnects, (
            "cancellation must disconnect the IN-FLIGHT connector too"
        )

    async def test_cancel_during_introspection_disconnects(self, monkeypatch):
        """Connected, cancelled inside get_metadata(): the per-connector
        guard must disconnect it even though it never reached
        inputs.systems."""
        from preflight.analysis import pipeline_runner as pr

        disconnects = []

        class ConnectedThenStuck:
            async def connect(self):
                return True

            async def get_metadata(self):
                await asyncio.sleep(30)

            async def disconnect(self):
                disconnects.append("stuck")

        monkeypatch.setattr(
            pr, "create_connector", lambda *a, **k: ConnectedThenStuck()
        )
        connections = [{"name": "x", "connector_type": "postgresql", "config": {}}]
        task = asyncio.get_event_loop().create_task(
            pr.collect_real_inputs(connections, {})
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert disconnects == ["stuck"]


class TestApiBusinessEntitiesShape:
    def test_scenario_accepts_entity_dicts_and_route_passes_dicts(self):
        """End-to-end shape check: API scenario -> route -> runner config."""
        import json
        from fastapi.testclient import TestClient
        from preflight.api.dependencies import (
            _connections_store, _runs_store, _reports_store,
        )
        from preflight.api.app import create_app
        from preflight.analysis import pipeline_runner

        _connections_store.clear(); _runs_store.clear(); _reports_store.clear()
        app = create_app()
        captured = {}

        async def fake_run_diagnostic(config, mock=False, progress_cb=None, connections=None):
            captured["entities"] = config.get("scenario", {}).get("entities")
            return {
                "readiness_score": 95.0, "verdict": "GO",
                "scenario_name": "t", "executive_summary": "s",
                "schema_inconsistencies": [], "middleware_gaps": [],
                "pipeline_bottlenecks": [], "remediation_plan": [],
                "critical_count": 0, "total_issues": 0,
                "remediation_weeks": "TBD", "entity_matches": [], "warnings": [],
            }

        import unittest.mock as mock_mod
        with mock_mod.patch.object(pipeline_runner, "run_diagnostic", fake_run_diagnostic):
            with TestClient(app) as client:
                conn = client.post("/connections", json={
                    "name": "db", "connector_type": "mock",
                    "system_type": "DATABASE", "config": {},
                }).json()
                client.post("/diagnostics", json={
                    "name": "shape-test",
                    "scenario": {
                        "name": "t",
                        "target_systems": ["db"],
                        "business_entities": [
                            {"name": "customer", "fields": ["id", "name"]}
                        ],
                    },
                    "connection_ids": [conn["id"]],
                })
        assert captured["entities"] == [
            {"name": "customer", "fields": ["id", "name"], "primary_system": None}
        ]


class TestRound6Fixes:
    def test_declared_pass_cannot_reclaim_exact_grouped_tables(self):
        """Mixed key styles let the declared pass re-claim tables already in
        an exact group, double-counting findings. Raw keys everywhere."""
        schemas = {
            "a": {"customer": _f("id", "name", "email")},
            "b": {"customer": _f("id", "name", "email")},
        }
        declared = [{"name": "client", "fields": ["id", "name", "email"]}]
        groups = EntityMatcher().build_entity_groups(schemas, declared)
        exact = [g for g in groups if g.method == "exact"]
        declared_groups = [g for g in groups if g.method == "declared"]
        assert len(exact) == 1
        assert declared_groups == [], (
            "declared pass re-claimed tables already grouped by exact pass: "
            + str([g.tables for g in declared_groups])
        )


class TestRound7Fixes:
    async def test_allowlist_enforced_on_diagnostic_path(self, monkeypatch):
        """The run — not just the probe — must honor PREFLIGHT_ALLOWED_HOSTS:
        a stored disallowed connection is skipped at collect time."""
        from preflight.analysis import pipeline_runner as pr

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal")
        connections = [
            # real type so the destination actually applies (mock never
            # dials out and is allowed by design)
            {"name": "evil", "connector_type": "postgresql",
             "config": {"host": "evil.example.com"}},
        ]
        with pytest.raises(pr.PipelineError):
            inputs = None
            try:
                inputs = await pr.collect_real_inputs(connections, {})
            finally:
                assert inputs is None or not inputs.systems
        # And the warning says why
        # (collect_real_inputs raises before returning warnings, so drive
        # it with one allowed mock system to observe the skip warning)
        connections.append(
            {"name": "ok", "connector_type": "mock", "config": {"host": "db.internal"}}
        )
        inputs = await pr.collect_real_inputs(connections, {})
        assert [s.name for s in inputs.systems] == ["ok"]
        assert any(
            "effective host 'evil.example.com'" in w for w in inputs.warnings
        )

    async def test_allowlist_fail_closed_without_host(self, monkeypatch):
        from preflight.analysis import pipeline_runner as pr

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal")
        assert pr.check_host_allowed({}) is not None
        assert "fail closed" in pr.check_host_allowed({})

    async def test_allowlist_case_insensitive_and_url_aware(self, monkeypatch):
        from preflight.analysis import pipeline_runner as pr

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal,crm.internal")
        assert pr.check_host_allowed({"host": "DB.INTERNAL"}) is None
        assert pr.check_host_allowed({"host": "db.internal:5432"}) is not None
        assert (
            pr.check_host_allowed({"instance_url": "https://CRM.internal/api"}) is None
        )
        assert (
            pr.check_host_allowed({"instance_url": "https://other.example.com"})
            is not None
        )

    def test_perf_guard_exercises_quadratic_pass(self):
        """Unique table names per system + SHARED field names: the exact
        pass groups nothing, so step 4 (prefilter -> cached fuzzy scoring)
        does the real 250x250 work."""
        import time as _time

        # 150 tables/system = 300 candidates (under the 400 default cap)
        # and a 150x150 pair space that only the caches keep fast.
        schemas = {
            sys_name: {f"{sys_name}_t{i}": _f(*(f"col_{j}" for j in range(20)))
                       for i in range(150)}
            for sys_name in ("a", "b")
        }
        start = _time.monotonic()
        groups = EntityMatcher().build_entity_groups(schemas)
        elapsed = _time.monotonic() - start
        overlap = [g for g in groups if g.method == "field_overlap"]
        assert overlap, "prefilter/caches broke grouping"
        assert elapsed < 30, f"matcher too slow: {elapsed:.1f}s"

    def test_cap_actually_prevents_grouping(self):
        """Shared exact fields + unique names: with a low cap the quadratic
        pass is skipped (no groups); with a high cap the same schemas group.
        Removing the cap check fails this test."""
        schemas = {
            sys_name: {f"{sys_name}_t{i}": _f("id", "name", "amount")
                       for i in range(50)}
            for sys_name in ("a", "b")
        }
        capped = EntityMatcher(max_field_overlap_candidates=10)
        assert capped.build_entity_groups(schemas) == []
        uncapped = EntityMatcher(max_field_overlap_candidates=10_000)
        assert [g for g in uncapped.build_entity_groups(schemas)
                if g.method == "field_overlap"]

    async def test_legacy_string_entities_accepted_by_api(self):
        """Pre-change clients sent ["customer"]; they must not 422."""
        from fastapi.testclient import TestClient
        from preflight.api.dependencies import (
            _connections_store, _runs_store, _reports_store,
        )
        from preflight.api.app import create_app
        from preflight.api.routes.diagnostics import _entities_as_dicts
        from preflight.api.schemas import CreateScenarioRequest

        req = CreateScenarioRequest(
            name="t", target_systems=["db"],
            business_entities=["customer", {"name": "order", "fields": ["id"]}],
        )
        shapes = _entities_as_dicts(req.business_entities)
        assert shapes == [
            {"name": "customer", "fields": []},
            {"name": "order", "fields": ["id"], "primary_system": None},
        ]
        _connections_store.clear(); _runs_store.clear(); _reports_store.clear()
        app = create_app()
        with TestClient(app) as client:
            conn = client.post("/connections", json={
                "name": "db", "connector_type": "mock",
                "system_type": "DATABASE", "config": {},
            })
            assert conn.status_code in (200, 201)
            resp = client.post("/diagnostics", json={
                "name": "legacy",
                "scenario": {"name": "t", "target_systems": ["db"],
                             "business_entities": ["customer"]},
                "connection_ids": [conn.json()["id"]],
            })
            assert resp.status_code in (200, 201, 202), resp.text


class TestRound8Fixes:
    async def test_allowlist_checks_all_host_keys(self, monkeypatch):
        from preflight.analysis.pipeline_runner import check_host_allowed

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal")
        assert check_host_allowed(
            {"host": "db.internal", "instance_url": "https://evil.example.com"}
        ) is not None
        assert check_host_allowed(
            {"host": "db.internal", "domain": "login"}
        ) is not None  # salesforce domain not in list
        assert check_host_allowed({"host": "db.internal"}) is None

    async def test_data_sources_reach_runner_and_reports(self):
        """End-to-end: a Mock-named connector under a real type is reported
        as mock in data_sources, and both report payloads carry it through."""
        from unittest.mock import patch
        from preflight.analysis import pipeline_runner as pr
        from preflight.core.infrastructure.connectors.mock_connector import (
            MockEnterpriseConnector,
        )

        connections = [
            {"name": "sf", "connector_type": "salesforce", "config": {}},
        ]
        config = {
            "scenario": {"name": "t", "entities": []},
            "analysis": {"pipeline_stress_test": {"enabled": False}},
            "reporting": {},
        }
        with patch.object(
            pr, "create_connector", lambda *a, **k: MockEnterpriseConnector({})
        ):
            payload = await pr.run_diagnostic(config, mock=False, connections=connections)
        assert payload["data_sources"] == {"sf": "mock"}, payload["data_sources"]

        # CLI mapping carries it through (report_data mirrors the payload)
        from preflight.cli import main as cli_main
        import inspect
        source = inspect.getsource(cli_main)
        assert "'data_sources': payload.get('data_sources')" in source
        # API payload carries it through
        from preflight.api.routes import diagnostics as diag_route
        source2 = inspect.getsource(diag_route)
        assert '"data_sources": payload.get("data_sources")' in source2


class TestRound9Fixes:
    async def test_duplicate_connection_names_deduplicated(self):
        from preflight.analysis import pipeline_runner as pr

        connections = [
            {"name": "prod", "connector_type": "mock", "config": {}},
            {"name": "prod", "connector_type": "mock", "config": {}},
        ]
        inputs = await pr.collect_real_inputs(connections, {})
        assert len(inputs.systems) == 2
        assert sorted(inputs.schemas) == ["prod", "prod (2)"]
        assert any("duplicate connection name" in w for w in inputs.warnings)

    async def test_entity_name_not_disambiguated_key(self):
        """Two same-canonical-name groups: findings report 'customer',
        never 'customer#2'."""
        from preflight.analysis.schema_analyzer import SchemaAnalyzer
        from preflight.analysis.entity_matcher import EntityMatcher

        schemas = {
            "a": {"customer": _f("id", "name", "email")},
            "b": {"customers": _f("id", "name", "phone")},
            "c": {"Account": _f("Id", "Name")},
            "d": {"KUNNR": _f("KUNNR", "NAME1")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        # exact {a,b} + vocabulary {c,d} now merge into one customer group
        assert len([g for g in groups if g.canonical_name == "customer"]) == 1
        analyzer = SchemaAnalyzer()
        results = analyzer.analyze_entity_groups(schemas, groups)
        inconsistencies = analyzer.generate_inconsistency_report(results)
        entities = {i["entity"] for i in inconsistencies}
        assert entities == {"customer"}, entities
        assert not any("#" in e for e in entities)

    def test_cap_skip_surfaces_payload_warning(self):
        """Over-budget matching must say so in warnings, not just INFO."""
        from preflight.analysis.entity_matcher import EntityMatcher

        schemas = {
            sys_name: {f"{sys_name}_t{i}": _f("id", "name")
                       for i in range(300)}
            for sys_name in ("a", "b")
        }
        matcher = EntityMatcher(max_field_overlap_candidates=10)
        matcher.build_entity_groups(schemas)
        assert matcher.matching_was_capped is True
        # under budget -> False
        small = {
            "a": {"orders": _f("id", "amount")},
            "b": {"orders_legacy": _f("id", "amount")},
        }
        matcher2 = EntityMatcher()
        matcher2.build_entity_groups(small)
        assert matcher2.matching_was_capped is False


class TestRound10Fixes:
    async def test_caller_declared_system_type_preferred(self):
        from preflight.analysis import pipeline_runner as pr

        connections = [
            {"name": "erp_replica", "connector_type": "postgresql",
             "system_type": "ERP", "config": {}},
            {"name": "plain_db", "connector_type": "mock",
             "system_type": None, "config": {}},
        ]
        real_factory = pr.create_connector

        def factory(ctype, config, **kw):
            if ctype == "postgresql":
                from preflight.core.infrastructure.connectors.mock_connector import (
                    MockEnterpriseConnector,
                )
                return MockEnterpriseConnector(config)
            return real_factory(ctype, config, **kw)

        import unittest.mock as mock
        with mock.patch.object(pr, "create_connector", factory):
            inputs = await pr.collect_real_inputs(connections, {})
        summaries = {s["name"]: s["type"] for s in inputs.connection_summaries()}
        assert summaries["erp_replica"] == "ERP"
        # undeclared mock derives to its own upper-cased type; a caller
        # CAN override it via system_type now
        assert summaries["plain_db"] == "MOCK"

    async def test_explicit_mock_labeled_mock_in_data_sources(self):
        from preflight.analysis import pipeline_runner as pr

        config = {
            "scenario": {"name": "t", "entities": []},
            "analysis": {},  # stress enabled — must be skipped for mock
            "reporting": {},
        }
        connections = [{"name": "demo", "connector_type": "mock", "config": {}}]
        payload = await pr.run_diagnostic(config, mock=False, connections=connections)
        assert payload["data_sources"] == {"demo": "mock"}
        assert payload["pipeline_bottlenecks"] == []


class TestRound11Fixes:
    def test_exact_group_extended_by_vocabulary(self):
        """A.customer + B.customer (exact) + C.Account: C must not be
        silently excluded — it extends the group with vocabulary provenance."""
        schemas = {
            "A": {"customer": _f("id", "name")},
            "B": {"customer": _f("id", "name", "email")},
            "C": {"Account": _f("Id", "Name")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        customer = [g for g in groups if g.canonical_name == "customer"]
        assert len(customer) == 1
        assert set(customer[0].tables) == {"A", "B", "C"}
        info = customer[0].as_match_info()
        assert info["link_methods"] == {"A": "exact", "B": "exact", "C": "vocabulary"}

    async def test_suffix_collision_with_caller_supplied_name(self):
        from preflight.analysis import pipeline_runner as pr

        connections = [
            {"name": "prod", "connector_type": "mock", "config": {}},
            {"name": "prod (2)", "connector_type": "mock", "config": {}},
            {"name": "prod", "connector_type": "mock", "config": {}},
        ]
        inputs = await pr.collect_real_inputs(connections, {})
        names = sorted(inputs.schemas)
        assert len(names) == 3, names
        assert "prod" in names and "prod (2)" in names
        # the third got a free suffix, not an overwrite of prod (2)
        third = [n for n in names if n not in ("prod", "prod (2)")]
        assert third == ["prod (3)"], third

    async def test_stress_failure_warning_reaches_payload(self):
        from unittest.mock import patch
        from preflight.analysis import pipeline_runner as pr
        from preflight.analysis.pipeline_tester import PipelineTester

        async def exploding(self, system_name, query_fn, scenario):
            raise RuntimeError("boom at 10.0.0.9:5432")

        class FakeRealConnector:  # name must NOT start with "Mock"
            async def connect(self):
                return True

            async def get_metadata(self):
                from preflight.core.infrastructure.connectors.base import (
                    SystemMetadata,
                )
                return SystemMetadata(
                    system_name="db", system_version="1", tables=[],
                    entity_count=0, total_row_estimate=0, connection_latency_ms=1.0,
                )

            async def execute_read_query(self, q):
                return None

            async def disconnect(self):
                pass

        connections = [{"name": "db", "connector_type": "postgresql", "config": {}}]
        config = {
            "scenario": {"name": "t", "entities": []},
            "analysis": {},  # stress enabled
            "reporting": {},
        }
        with patch.object(pr, "create_connector", lambda *a, **k: FakeRealConnector()):
            with patch.object(PipelineTester, "simulate_ai_agent_workload", exploding):
                payload = await pr.run_diagnostic(
                    config, mock=False, connections=connections
                )
        assert any("pipeline stress test failed" in w for w in payload["warnings"])
        assert not any("10.0.0.9" in w for w in payload["warnings"])
        assert payload["pipeline_bottlenecks"] == []


class TestRound12OpenItems:
    """The two open round-12 items, closed."""

    def test_effective_destination_postgresql(self, monkeypatch):
        from preflight.analysis.pipeline_runner import check_host_allowed

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal")
        # The codex case: 'account' is IGNORED by _build_dsn; the effective
        # destination is libpq's localhost default — must NOT pass just
        # because the ignored value is allowlisted.
        blocked = check_host_allowed(
            {"account": "db.internal"}, connector_type="postgresql"
        )
        assert blocked is not None and "localhost" in blocked
        assert check_host_allowed(
            {"host": "db.internal"}, connector_type="postgresql"
        ) is None
        # missing host -> localhost default, honored strictly
        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "localhost,db.internal")
        assert check_host_allowed({}, connector_type="postgresql") is None
        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal")
        assert "localhost" in check_host_allowed(
            {}, connector_type="postgresql"
        )

    def test_effective_destination_snowflake_and_salesforce(self, monkeypatch):
        from preflight.analysis.pipeline_runner import check_host_allowed

        monkeypatch.setenv(
            "PREFLIGHT_ALLOWED_HOSTS",
            "myorg-myaccount.snowflakecomputing.com,test.salesforce.com,login.salesforce.com",
        )
        assert check_host_allowed(
            {"account": "myorg-myaccount"}, connector_type="snowflake"
        ) is None
        assert check_host_allowed(
            {"domain": "test"}, connector_type="salesforce"
        ) is None
        # salesforce default when nothing is set
        assert check_host_allowed({}, connector_type="salesforce") is None
        # hostile account form
        blocked = check_host_allowed(
            {"account": "evil"}, connector_type="snowflake"
        )
        assert blocked is not None and "evil.snowflakecomputing.com" in blocked
        # snowflake with nothing -> fail closed
        assert "no destination" in check_host_allowed(
            {}, connector_type="snowflake"
        )

    def test_mock_connector_never_blocked(self, monkeypatch):
        from preflight.analysis.pipeline_runner import check_host_allowed

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal")
        assert check_host_allowed(
            {"host": "anything"}, connector_type="mock"
        ) is None

    def test_vocabulary_extends_exact_group_via_alias(self):
        """A.Account + B.Account (exact 'account' group) + C.KUNNR: the
        vocabulary pass must find the group through the alias set and
        extend it, or C is silently excluded from analysis."""
        schemas = {
            "A": {"Account": _f("Id", "Name")},
            "B": {"Account": _f("Id", "Name", "Phone")},
            "C": {"KUNNR": _f("KUNNR", "NAME1")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        assert len(groups) == 1
        group = groups[0]
        assert set(group.tables) == {"A", "B", "C"}
        assert group.link_methods == {"A": "exact", "B": "exact", "C": "vocabulary"}
        assert group.method == "vocabulary"  # weakest link wins

        from preflight.analysis.schema_analyzer import SchemaAnalyzer
        results = SchemaAnalyzer().analyze_entity_groups(schemas, groups)
        inconsistencies = SchemaAnalyzer().generate_inconsistency_report(results)
        pairs = {(i["source"], i["target"]) for i in inconsistencies}
        assert ("C", "A") in pairs or ("A", "C") in pairs, (
            f"C excluded from analysis: {pairs}"
        )
        provenance = inconsistencies[0]["entity_match"]
        assert provenance["link_methods"]["C"] == "vocabulary"


class TestRound13Fixes:
    def test_compatible_alias_exact_groups_merge(self):
        """A.customer+B.customer AND C.Account+D.Account: two exact groups
        for the same business entity must merge (and E.KUNNR extend)."""
        schemas = {
            "A": {"customer": _f("id", "name")},
            "B": {"customer": _f("id", "name", "email")},
            "C": {"Account": _f("Id", "Name")},
            "D": {"Account": _f("Id", "Name", "Phone")},
            "E": {"KUNNR": _f("KUNNR", "NAME1")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        assert len(groups) == 1
        assert set(groups[0].tables) == {"A", "B", "C", "D", "E"}
        assert groups[0].link_methods["A"] == "exact"
        assert groups[0].link_methods["E"] == "vocabulary"
        # all four exact members analyzed together now
        from preflight.analysis.schema_analyzer import SchemaAnalyzer
        results = SchemaAnalyzer().analyze_entity_groups(schemas, groups)
        incons = SchemaAnalyzer().generate_inconsistency_report(results)
        pairs = {(i["source"], i["target"]) for i in incons}
        cross = [p for p in pairs if {p[0], p[1]} == {"A", "C"} or {p[0], p[1]} == {"B", "D"}]
        assert cross, f"cross-group pairs missing: {pairs}"

    def test_declared_attaches_to_existing_exact_group(self):
        schemas = {
            "A": {"invoice": _f("invoice_num", "amount")},
            "B": {"invoice": _f("invoice_num", "amount", "date")},
            "C": {"legacy_billing": _f("invoice_num", "amount", "currency")},
        }
        declared = [{"name": "invoice", "fields": ["invoice_num", "amount"]}]
        groups = EntityMatcher().build_entity_groups(schemas, declared)
        assert len(groups) == 1
        assert set(groups[0].tables) == {"A", "B", "C"}
        assert groups[0].link_methods == {"A": "exact", "B": "exact", "C": "declared"}

    async def test_malformed_host_url_structured_failure(self, monkeypatch):
        """A malformed destination URL must fail closed with a structured
        error, never raise past validation (both enforcement points)."""
        from preflight.analysis.pipeline_runner import check_host_allowed

        monkeypatch.setenv("PREFLIGHT_ALLOWED_HOSTS", "db.internal")
        result = check_host_allowed(
            {"host": "https://[invalid"}, connector_type="postgresql"
        )
        assert result is not None  # blocked, not raised
        assert "not in the allowed hosts list" in result

        from preflight.api.routes.connections import _test_connection
        api_result = await _test_connection(
            "postgresql", {"host": "https://[invalid"}
        )
        assert api_result["status"] == "failed"
        assert "allowed hosts list" in api_result["error_message"]


class TestRound14Fixes:
    def test_overlapping_alias_groups_not_lossy_merged(self):
        """A and B each have BOTH customer and Account: merging the two
        exact islands would drop a table per shared system. Both islands
        must survive with all their comparisons."""
        schemas = {
            "A": {"customer": _f("id", "name", "email"),
                  "Account": _f("id", "name", "phone")},
            "B": {"customer": _f("id", "name", "phone"),
                  "Account": _f("id", "name", "email")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        assert len(groups) == 2, [(g.canonical_name, g.tables) for g in groups]
        from preflight.analysis.schema_analyzer import SchemaAnalyzer
        results = SchemaAnalyzer().analyze_entity_groups(schemas, groups)
        incons = SchemaAnalyzer().generate_inconsistency_report(results)
        pairs = {(i["source"], i["target"]) for i in incons}
        assert ("A", "B") in pairs  # both islands still compare A vs B

    def test_disjoint_alias_merge_records_vocabulary_join(self):
        """Without an extra vocabulary table, the merged group must still
        report the JOIN as vocabulary-strength, not exact/1.0."""
        schemas = {
            "A": {"customer": _f("id", "name")},
            "B": {"customer": _f("id", "name", "email")},
            "C": {"Account": _f("Id", "Name")},
            "D": {"Account": _f("Id", "Name", "Phone")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        assert len(groups) == 1
        group = groups[0]
        assert group.method == "vocabulary"  # the join is an inference
        assert group.confidence == 0.9
        assert group.link_methods == {"A": "exact", "B": "exact",
                                      "C": "exact", "D": "exact"}
