"""Unit tests for the shared diagnostic pipeline runner."""
import pytest

from preflight.analysis import pipeline_runner
from preflight.analysis.pipeline_runner import (
    PipelineError,
    RealInputs,
    _bottleneck_severity,
    build_remediation_plan,
    collect_real_inputs,
    run_diagnostic,
)


SCENARIO = {
    "name": "Test Scenario",
    # Three systems, mirroring config.example.yml: the API-gateway gap only
    # fires for >2 connected systems, which is what freezes the demo at 97.0.
    "systems": ["salesforce", "sap", "postgresql"],
    "concurrent_users": 5,
    "queries_per_minute": 60,
    "peak_multiplier": 2.0,
}


class TestFrozenDemoPath:
    async def test_mock_run_reproduces_readme_demo(self):
        """The --mock path must stay frozen: 97.0 / GO / 3 gaps / 12-18 weeks."""
        config = {"scenario": SCENARIO, "analysis": {}, "reporting": {}}
        payload = await run_diagnostic(config, mock=True)
        assert payload["readiness_score"] == pytest.approx(97.0)
        assert payload["verdict"] == "GO"
        assert len(payload["middleware_gaps"]) == 3
        assert payload["remediation_weeks"] == "12"
        assert payload["schema_inconsistencies"] == []
        assert payload["mock"] is True

    async def test_mock_path_uses_same_name_analysis_only(self):
        config = {"scenario": SCENARIO, "analysis": {}, "reporting": {}}
        payload = await run_diagnostic(config, mock=True)
        # The demo dataset plants an Account/KUNNR mismatch that the frozen
        # (same-name) analysis deliberately does NOT detect.
        assert payload["entity_matches"] == []

    async def test_progress_events_fired_in_demo(self):
        config = {"scenario": SCENARIO, "analysis": {}, "reporting": {}}
        events = []
        await run_diagnostic(config, mock=True, progress_cb=events.append)
        stages = [e["stage"] for e in events]
        assert stages == ["connect", "schema", "pipeline", "middleware", "score", "report"]


class TestRealPath:
    async def test_mock_connector_through_real_pipeline(self):
        """A 'mock' connector runs through the REAL path end-to-end."""
        connections = [
            {"name": "sys_a", "connector_type": "mock", "config": {}},
            {"name": "sys_b", "connector_type": "mock", "config": {}},
        ]
        config = {
            "scenario": {**SCENARIO, "entities": []},
            "analysis": {"pipeline_stress_test": {"enabled": False}},
            "reporting": {},
        }
        payload = await run_diagnostic(config, mock=False, connections=connections)
        assert payload["mock"] is False
        assert payload["warnings"] == []
        # Two mock systems expose identical table names → exact entity groups
        assert any(m["method"] == "exact" for m in payload["entity_matches"])
        assert payload["readiness_score"] > 0
        assert payload["verdict"] in ("GO", "NOT_YET", "NOT_READY")

    async def test_unsupported_connector_type_warns(self):
        connections = [
            {"name": "sap_prod", "connector_type": "sap", "config": {"host": "x"}},
            {"name": "demo", "connector_type": "mock", "config": {}},
        ]
        config = {
            "scenario": SCENARIO,
            "analysis": {"pipeline_stress_test": {"enabled": False}},
            "reporting": {},
        }
        payload = await run_diagnostic(config, mock=False, connections=connections)
        assert any("no connector implemented" in w for w in payload["warnings"])

    async def test_failed_connection_raises_pipeline_error(self):
        connections = [
            {"name": "sap_prod", "connector_type": "sap", "config": {"host": "x"}},
        ]
        with pytest.raises(PipelineError):
            await collect_real_inputs(connections, SCENARIO)

    async def test_cross_name_matching_runs_on_real_path(self):
        """Real path detects the Account/KUNNR mismatch the demo freezes."""
        # Build two fake systems via the registry's mock connector with
        # custom schemas is heavy; instead exercise matcher + runner wiring
        # through the pipeline's schema stage contract.
        from preflight.analysis.entity_matcher import EntityMatcher
        from preflight.analysis.schema_analyzer import SchemaAnalyzer

        schemas = {
            "salesforce": {
                "Account": [
                    {"name": "Id", "type": "varchar(18)", "nullable": False},
                    {"name": "Name", "type": "varchar(255)", "nullable": False},
                ],
            },
            "sap": {
                "KUNNR": [
                    {"name": "KUNNR", "type": "varchar(10)", "nullable": False},
                    {"name": "NAME1", "type": "nvarchar(35)", "nullable": False},
                ],
            },
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        analyzer = SchemaAnalyzer()
        results = analyzer.analyze_entity_groups(schemas, groups)
        inconsistencies = analyzer.generate_inconsistency_report(results)
        assert inconsistencies, "cross-name mismatch should be detected on the real path"
        assert all(i["entity_match"]["method"] == "vocabulary" for i in inconsistencies)


class TestCollectRealInputs:
    async def test_mock_connector_collects_schemas(self):
        connections = [{"name": "demo", "connector_type": "mock", "config": {}}]
        inputs = await collect_real_inputs(connections, SCENARIO)
        assert len(inputs.systems) == 1
        assert inputs.systems[0].is_mock_fallback is False
        assert "demo" in inputs.schemas
        # The mock connector exposes 8 tables
        assert len(inputs.schemas["demo"]) == 8

    async def test_disabled_config_sections_skipped(self):
        config = {
            "connections": {
                "postgresql": {"enabled": False, "host": "x"},
                "sap": {"enabled": True, "host": "x"},
                "salesforce": {"enabled": False},
            }
        }
        conns = pipeline_runner._connections_from_config(config)
        assert [c["name"] for c in conns] == ["sap"]


class TestBottleneckSeverity:
    def test_severity_ladder(self):
        assert _bottleneck_severity(10.0, 100) == "HIGH"
        assert _bottleneck_severity(0.0, 1500) == "HIGH"
        assert _bottleneck_severity(0.0, 700) == "MEDIUM"
        assert _bottleneck_severity(2.5, 380) == "LOW"
        assert _bottleneck_severity(0.5, 180) == "INFO"


class TestRemediationPlan:
    def test_blocking_gap_and_critical_schema(self):
        gaps = [
            {
                "id": "g1",
                "type": "etl_pipeline",
                "severity": "HIGH",
                "blocking": True,
                "description": "d",
                "effort_days": (10, 40),
            }
        ]
        inconsistencies = [
            {"entity": "Account", "type": "key_mismatch", "severity": "CRITICAL",
             "source": "sf", "target": "sap", "detail": "keys differ"}
        ]
        plan = build_remediation_plan(inconsistencies, gaps)
        ids = [item["id"] for item in plan]
        assert "rem_g1" in ids
        assert "rem_schema_critical" in ids
        seqs = [item["recommended_sequence"] for item in plan]
        assert seqs == sorted(seqs)
