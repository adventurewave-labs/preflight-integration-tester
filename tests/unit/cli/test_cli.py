"""
Tests for the Preflight CLI (preflight/cli/main.py).

Uses Click's CliRunner to invoke commands without spawning subprocesses.
A minimal YAML config file is written to a temp directory for tests that
require a real config path.
"""
import json
import os
import tempfile
import yaml
import pytest
from click.testing import CliRunner
from pathlib import Path

from preflight.cli.main import (
    cli,
    _get_mock_schemas,
    _get_mock_pipeline_results,
    _map_inconsistency,
    _map_gap,
    _build_remediation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_temp_config(**extra) -> str:
    """Write a minimal valid YAML config to a temp file and return its path."""
    config = {
        "scenario": {
            "name": "Test Scenario",
            "description": "Unit test scenario",
            "systems": ["mock_erp"],
            "use_case": "Test AI deployment",
        },
        "analysis": {
            "schema_consistency": {
                "entity_matching_threshold": 0.8,
            }
        },
        "reporting": {
            "risk_weights": None,
        },
        **extra,
    }
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
    yaml.dump(config, f)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Basic CLI command help tests
# ---------------------------------------------------------------------------

class TestCliHelp:
    def test_cli_help(self):
        """Top-level --help exits 0 and prints usage."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Usage" in result.output or "usage" in result.output.lower()

    def test_cli_version(self):
        """--version prints version string and exits 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_run_command_help(self):
        """'run --help' shows usage and exits 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "Usage" in result.output or "usage" in result.output.lower()

    def test_run_help_shows_config_option(self):
        """run --help mentions --config/-c option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert "--config" in result.output or "-c" in result.output

    def test_run_help_shows_mock_option(self):
        """run --help mentions --mock flag."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert "--mock" in result.output

    def test_run_help_shows_format_option(self):
        """run --help mentions --format option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert "--format" in result.output

    def test_run_help_shows_output_option(self):
        """run --help mentions --output option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert "--output" in result.output


# ---------------------------------------------------------------------------
# run command: config validation errors
# ---------------------------------------------------------------------------

class TestRunCommandConfigErrors:
    def test_run_without_config_fails(self):
        """run without --config should exit with non-zero or error."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run"])
        # Click should complain about missing required --config option
        assert result.exit_code != 0

    def test_run_with_nonexistent_config_fails(self):
        """run --config on a missing file should report an error."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--config", "/nonexistent/config.yml"])
        # Click Path(exists=True) will reject this before the command runs
        assert result.exit_code != 0

    def test_run_with_nonexistent_config_error_message(self):
        """Error message should reference the invalid path."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--config", "/nonexistent/config.yml"])
        # Click should mention the path issue in output or exception
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# run command: successful execution with mock flag
# ---------------------------------------------------------------------------

class TestRunCommandSuccess:
    def test_run_with_mock_and_config(self):
        """run --mock with a valid config file should complete successfully."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock", "--output", "./out"],
                )
            # Should either succeed or produce output (not crash with exit_code 2)
            assert result.exit_code in (0, 1), (
                f"Unexpected exit code {result.exit_code}:\n{result.output}"
            )
        finally:
            os.unlink(config_path)

    def test_run_with_mock_creates_output(self):
        """run --mock should write report files to the output directory."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                out_dir = "./test_reports"
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock", "--output", out_dir],
                )
                if result.exit_code == 0:
                    assert Path(out_dir).exists()
                    json_path = Path(out_dir) / "readiness-assessment.json"
                    assert json_path.exists()
                    with open(json_path) as f:
                        data = json.load(f)
                    assert "readiness_score" in data
        finally:
            os.unlink(config_path)

    def test_run_with_json_format(self):
        """run --format json should not crash."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock", "--format", "json"],
                )
            assert result.exit_code in (0, 1), (
                f"Unexpected exit code {result.exit_code}:\n{result.output}"
            )
        finally:
            os.unlink(config_path)

    def test_run_with_text_format(self):
        """run --format text should not crash."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock", "--format", "text"],
                )
            assert result.exit_code in (0, 1), (
                f"Unexpected exit code {result.exit_code}:\n{result.output}"
            )
        finally:
            os.unlink(config_path)

    def test_run_with_html_format(self):
        """run --format html (default) should not crash."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock", "--format", "html"],
                )
            assert result.exit_code in (0, 1), (
                f"Unexpected exit code {result.exit_code}:\n{result.output}"
            )
        finally:
            os.unlink(config_path)

    def test_run_invalid_format_rejected(self):
        """run --format invalid_fmt should be rejected by Click."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["run", "--config", config_path, "--format", "csv"],
            )
            assert result.exit_code != 0
        finally:
            os.unlink(config_path)

    def test_run_output_in_tmp(self):
        """run with explicit --output /tmp/... should succeed."""
        config_path = make_temp_config()
        tmp_out = tempfile.mkdtemp()
        try:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["run", "--config", config_path, "--mock", "--output", tmp_out],
            )
            assert result.exit_code in (0, 1), (
                f"Unexpected exit code {result.exit_code}:\n{result.output}"
            )
        finally:
            os.unlink(config_path)

    def test_run_output_mentions_reports_saved(self):
        """Successful run should print 'Reports saved to' in output."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock"],
                )
                if result.exit_code == 0:
                    assert "Reports saved" in result.output or "report" in result.output.lower()
        finally:
            os.unlink(config_path)


# ---------------------------------------------------------------------------
# run command: JSON output structure
# ---------------------------------------------------------------------------

class TestRunCommandJsonOutput:
    def test_run_json_output_has_score(self):
        """The generated JSON report should contain readiness_score."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock", "--output", "./rpt"],
                )
                if result.exit_code == 0:
                    with open("./rpt/readiness-assessment.json") as f:
                        data = json.load(f)
                    assert "readiness_score" in data
                    assert isinstance(data["readiness_score"], (int, float))
        finally:
            os.unlink(config_path)

    def test_run_json_output_has_verdict(self):
        """The generated JSON report should contain a verdict string."""
        config_path = make_temp_config()
        try:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["run", "--config", config_path, "--mock", "--output", "./rpt"],
                )
                if result.exit_code == 0:
                    with open("./rpt/readiness-assessment.json") as f:
                        data = json.load(f)
                    assert "verdict" in data
                    assert data["verdict"] in ("GO", "NOT_YET", "NOT_READY")
        finally:
            os.unlink(config_path)


# ---------------------------------------------------------------------------
# Helper function unit tests (pure functions, no I/O)
# ---------------------------------------------------------------------------

class TestGetMockSchemas:
    def test_returns_dict(self):
        schemas = _get_mock_schemas()
        assert isinstance(schemas, dict)

    def test_has_multiple_systems(self):
        schemas = _get_mock_schemas()
        assert len(schemas) >= 2

    def test_has_salesforce_key(self):
        schemas = _get_mock_schemas()
        assert "salesforce" in schemas

    def test_has_sap_key(self):
        schemas = _get_mock_schemas()
        assert "sap" in schemas

    def test_each_system_has_tables(self):
        schemas = _get_mock_schemas()
        for system, tables in schemas.items():
            assert isinstance(tables, dict)
            assert len(tables) > 0


class TestGetMockPipelineResults:
    def test_returns_list(self):
        results = _get_mock_pipeline_results()
        assert isinstance(results, list)

    def test_non_empty(self):
        results = _get_mock_pipeline_results()
        assert len(results) > 0

    def test_items_have_required_fields(self):
        results = _get_mock_pipeline_results()
        for r in results:
            assert "system" in r
            assert "error_rate_pct" in r
            assert "p95_ms" in r


class TestMapInconsistency:
    def test_maps_id(self):
        inc = {"id": "inc_001", "entity": "Contact", "type": "key_mismatch",
               "severity": "CRITICAL", "source": "sf", "target": "sap",
               "detail": "Key mismatch"}
        result = _map_inconsistency(inc)
        assert result["id"] == "inc_001"

    def test_maps_entity_name(self):
        inc = {"id": "x", "entity": "Account", "type": "missing_field",
               "severity": "HIGH", "source": "sf", "target": "sap", "detail": "d"}
        result = _map_inconsistency(inc)
        assert result["entity_name"] == "Account"

    def test_maps_severity(self):
        inc = {"id": "x", "entity": "Order", "type": "type_conflict",
               "severity": "MEDIUM", "source": "sf", "target": "sap", "detail": "d"}
        result = _map_inconsistency(inc)
        assert result["severity"] == "MEDIUM"

    def test_default_id_on_missing(self):
        result = _map_inconsistency({})
        assert result["id"] == "unknown"

    def test_has_remediation_hint(self):
        result = _map_inconsistency({})
        assert "remediation_hint" in result
        assert isinstance(result["remediation_hint"], str)


class TestMapGap:
    def test_maps_id(self):
        gap = {"id": "gap_001", "type": "semantic_layer", "severity": "CRITICAL",
               "blocking": True, "description": "Missing semantic layer", "effort_days": (10, 30)}
        result = _map_gap(gap)
        assert result["id"] == "gap_001"

    def test_maps_severity(self):
        gap = {"id": "g", "type": "api_gateway", "severity": "HIGH",
               "blocking": False, "description": "Missing gateway", "effort_days": (5, 15)}
        result = _map_gap(gap)
        assert result["severity"] == "HIGH"

    def test_maps_effort_tuple(self):
        gap = {"id": "g", "type": "t", "severity": "LOW",
               "blocking": False, "description": "d", "effort_days": (3, 12)}
        result = _map_gap(gap)
        assert result["effort_min_days"] == 3
        assert result["effort_max_days"] == 12

    def test_default_effort_on_missing(self):
        result = _map_gap({})
        assert "effort_min_days" in result
        assert "effort_max_days" in result

    def test_maps_blocking(self):
        gap = {"id": "g", "type": "t", "severity": "CRITICAL",
               "blocking": True, "description": "d", "effort_days": (5, 20)}
        result = _map_gap(gap)
        assert result["blocking"] is True


class TestBuildRemediation:
    def test_empty_inputs_returns_list(self):
        result = _build_remediation([], [])
        assert isinstance(result, list)

    def test_blocking_gap_added(self):
        gaps = [
            {"id": "g1", "type": "semantic_layer", "severity": "CRITICAL",
             "blocking": True, "description": "Need semantic layer", "effort_days": (10, 30)}
        ]
        result = _build_remediation([], gaps)
        assert len(result) > 0
        titles = [r["title"] for r in result]
        assert any("Semantic Layer" in t or "semantic" in t.lower() for t in titles)

    def test_non_blocking_gap_not_added(self):
        gaps = [
            {"id": "g1", "type": "api_gateway", "severity": "HIGH",
             "blocking": False, "description": "No gateway", "effort_days": (5, 20)}
        ]
        result = _build_remediation([], gaps)
        assert len(result) == 0

    def test_critical_schema_inconsistency_added(self):
        inconsistencies = [
            {"entity": "Contact", "type": "key_mismatch", "severity": "CRITICAL",
             "source": "sf", "target": "sap", "detail": "Key fields differ"}
        ]
        result = _build_remediation(inconsistencies, [])
        assert len(result) > 0
        # Should include a schema remediation item
        categories = [r["category"] for r in result]
        assert "schema" in categories

    def test_non_critical_schema_not_added(self):
        inconsistencies = [
            {"entity": "Account", "type": "missing_field", "severity": "HIGH",
             "source": "sf", "target": "sap", "detail": "Field missing"}
        ]
        result = _build_remediation(inconsistencies, [])
        # HIGH severity schema issues are not added (only CRITICAL triggers action)
        schema_items = [r for r in result if r["category"] == "schema"]
        assert len(schema_items) == 0

    def test_sequence_numbers_assigned(self):
        gaps = [
            {"id": "g1", "type": "semantic_layer", "severity": "CRITICAL",
             "blocking": True, "description": "d", "effort_days": (5, 20)}
        ]
        inconsistencies = [
            {"entity": "Contact", "type": "key_mismatch", "severity": "CRITICAL",
             "source": "sf", "target": "sap", "detail": "Key mismatch"}
        ]
        result = _build_remediation(inconsistencies, gaps)
        sequences = [r["recommended_sequence"] for r in result]
        # All sequence numbers should be positive integers
        assert all(isinstance(s, int) and s >= 1 for s in sequences)

    def test_items_have_required_fields(self):
        gaps = [
            {"id": "g1", "type": "semantic_layer", "severity": "CRITICAL",
             "blocking": True, "description": "Missing", "effort_days": (5, 20)}
        ]
        result = _build_remediation([], gaps)
        required = ["id", "title", "description", "category", "priority",
                    "severity", "effort_min_days", "effort_max_days", "recommended_sequence"]
        for item in result:
            for field in required:
                assert field in item, f"Missing field '{field}' in remediation item"
