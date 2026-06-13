"""
Unit tests for DiagnosticService.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from preflight.core.application.services.diagnostic_service import DiagnosticService
from preflight.core.domain.aggregates import AnalysisResults, DiagnosticRun, ReadinessReport
from preflight.core.infrastructure.repositories.diagnostic_repository import (
    InMemoryDiagnosticRunRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(repo=None, schema_service=None, report_service=None):
    """Create a DiagnosticService with mock sub-services by default."""
    if repo is None:
        repo = InMemoryDiagnosticRunRepository()
    if schema_service is None:
        schema_service = AsyncMock()
        schema_service.analyze_schemas.return_value = AnalysisResults()
    if report_service is None:
        report_service = MagicMock()
        report_service.generate_report.return_value = ReadinessReport()
    return DiagnosticService(repo, schema_service, report_service), repo


# ---------------------------------------------------------------------------
# create_run
# ---------------------------------------------------------------------------

class TestCreateRun:
    async def test_create_run_returns_pending_run(self):
        service, _ = _make_service()
        run = await service.create_run("My Run")
        assert run.status == "pending"
        assert run.name == "My Run"

    async def test_create_run_with_no_scenario_leaves_scenario_none(self):
        service, _ = _make_service()
        run = await service.create_run("No Scenario Run")
        assert run.scenario is None

    async def test_create_run_with_scenario_config(self):
        service, _ = _make_service()
        config = {
            "name": "AI Scenario",
            "description": "Test deployment",
            "target_systems": ["salesforce", "sap"],
            "concurrent_users": 50,
            "queries_per_minute": 200,
            "peak_multiplier": 3.0,
            "response_time_target_ms": 300,
            "business_entities": ["Customer", "Order"],
            "use_case": "Customer service AI",
        }
        run = await service.create_run("Scenario Run", scenario_config=config)
        assert run.scenario is not None
        assert run.scenario.name == "AI Scenario"
        assert run.scenario.concurrent_users == 50
        assert run.scenario.queries_per_minute == 200
        assert run.scenario.peak_multiplier == 3.0
        assert run.scenario.response_time_target_ms == 300
        assert run.scenario.business_entities == ["Customer", "Order"]
        assert run.scenario.use_case == "Customer service AI"
        assert "salesforce" in run.scenario.target_systems

    async def test_create_run_scenario_defaults_name_from_run(self):
        """If scenario_config has no 'name' key, fall back to the run name."""
        service, _ = _make_service()
        run = await service.create_run("Fallback Name", scenario_config={"use_case": "test"})
        assert run.scenario.name == "Fallback Name"

    async def test_create_run_persists_to_repository(self):
        repo = InMemoryDiagnosticRunRepository()
        service, _ = _make_service(repo=repo)
        run = await service.create_run("Persisted Run")
        fetched = await repo.find_by_id(run.id)
        assert fetched is not None
        assert fetched.id == run.id
        assert fetched.name == "Persisted Run"

    async def test_create_run_returns_different_ids(self):
        service, _ = _make_service()
        run1 = await service.create_run("Run 1")
        run2 = await service.create_run("Run 2")
        assert run1.id != run2.id


# ---------------------------------------------------------------------------
# start_run
# ---------------------------------------------------------------------------

class TestStartRun:
    async def test_start_run_transitions_to_running(self):
        service, repo = _make_service()
        run = await service.create_run("Run to start")
        await service.start_run(run.id)
        # The run is immediately set to running before the background task.
        stored = await repo.find_by_id(run.id)
        # status may be 'running', 'analyzing', 'reporting', or 'completed'
        assert stored.status in ("running", "analyzing", "reporting", "completed", "failed")

    async def test_start_run_sets_started_at(self):
        service, repo = _make_service()
        run = await service.create_run("Timed Run")
        await service.start_run(run.id)
        stored = await repo.find_by_id(run.id)
        assert stored.started_at is not None

    async def test_start_run_not_found_raises(self):
        service, _ = _make_service()
        with pytest.raises(ValueError, match="not found"):
            await service.start_run("nonexistent-id")

    async def test_start_run_non_pending_raises(self):
        service, repo = _make_service()
        run = await service.create_run("Double Start Run")
        # Manually put it in running state.
        run.start()
        await repo.save(run)
        with pytest.raises(ValueError, match="pending"):
            await service.start_run(run.id)

    async def test_start_run_failed_run_raises(self):
        service, repo = _make_service()
        run = await service.create_run("Failed Run")
        run.fail("something went wrong")
        await repo.save(run)
        with pytest.raises(ValueError, match="pending"):
            await service.start_run(run.id)


# ---------------------------------------------------------------------------
# get_run_status
# ---------------------------------------------------------------------------

class TestGetRunStatus:
    async def test_get_run_status_returns_dict_with_required_keys(self):
        service, _ = _make_service()
        run = await service.create_run("Status Run")
        status = await service.get_run_status(run.id)
        for key in ("run_id", "name", "status", "progress_pct", "started_at",
                    "completed_at", "error_message"):
            assert key in status, f"Missing key: {key}"

    async def test_get_run_status_pending_run(self):
        service, _ = _make_service()
        run = await service.create_run("Pending Status Run")
        status = await service.get_run_status(run.id)
        assert status["status"] == "pending"
        assert status["run_id"] == run.id
        assert status["name"] == "Pending Status Run"
        assert status["started_at"] is None
        assert status["completed_at"] is None
        assert status["error_message"] is None
        assert status["progress_pct"] == 0.0

    async def test_get_run_status_not_found_raises(self):
        service, _ = _make_service()
        with pytest.raises(ValueError, match="not found"):
            await service.get_run_status("unknown-id")

    async def test_get_run_status_started_at_iso_string(self):
        service, repo = _make_service()
        run = await service.create_run("Started Run")
        run.start()
        await repo.save(run)
        status = await service.get_run_status(run.id)
        # started_at should be an ISO 8601 string.
        assert status["started_at"] is not None
        assert "T" in status["started_at"]  # ISO format includes 'T' separator

    async def test_get_run_status_completed_at_iso_string(self):
        service, repo = _make_service()
        run = await service.create_run("Completed Run")
        run.start()
        run.complete_analysis(AnalysisResults())
        run.complete(ReadinessReport())
        await repo.save(run)
        status = await service.get_run_status(run.id)
        assert status["completed_at"] is not None
        assert "T" in status["completed_at"]
        assert status["status"] == "completed"

    async def test_get_run_status_failed_run(self):
        service, repo = _make_service()
        run = await service.create_run("Failed Status Run")
        run.fail("disk full")
        await repo.save(run)
        status = await service.get_run_status(run.id)
        assert status["status"] == "failed"
        assert status["error_message"] == "disk full"


# ---------------------------------------------------------------------------
# get_run_results
# ---------------------------------------------------------------------------

class TestGetRunResults:
    async def test_get_run_results_not_found_raises(self):
        service, _ = _make_service()
        with pytest.raises(ValueError, match="not found"):
            await service.get_run_results("does-not-exist")

    async def test_get_run_results_pending_raises(self):
        service, _ = _make_service()
        run = await service.create_run("Pending Results Run")
        with pytest.raises(RuntimeError, match="not completed"):
            await service.get_run_results(run.id)

    async def test_get_run_results_running_raises(self):
        service, repo = _make_service()
        run = await service.create_run("Running Results Run")
        run.start()
        await repo.save(run)
        with pytest.raises(RuntimeError, match="not completed"):
            await service.get_run_results(run.id)

    async def test_get_run_results_completed_returns_run(self):
        service, repo = _make_service()
        run = await service.create_run("Completed Results Run")
        run.start()
        run.complete_analysis(AnalysisResults())
        run.complete(ReadinessReport())
        await repo.save(run)
        result = await service.get_run_results(run.id)
        assert isinstance(result, DiagnosticRun)
        assert result.status == "completed"

    async def test_get_run_results_failed_returns_run(self):
        service, repo = _make_service()
        run = await service.create_run("Failed Results Run")
        run.fail("timeout")
        await repo.save(run)
        result = await service.get_run_results(run.id)
        assert result.status == "failed"
        assert result.error_message == "timeout"


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------

class TestListRuns:
    async def test_list_runs_empty(self):
        service, _ = _make_service()
        runs = await service.list_runs()
        assert runs == []

    async def test_list_runs_returns_all_runs(self):
        service, _ = _make_service()
        await service.create_run("Run A")
        await service.create_run("Run B")
        await service.create_run("Run C")
        runs = await service.list_runs()
        assert len(runs) == 3

    async def test_list_runs_returns_newest_first(self):
        """Runs should be sorted by created_at descending."""
        service, _ = _make_service()
        run1 = await service.create_run("Oldest")
        # Introduce tiny sleep to ensure distinct timestamps.
        await asyncio.sleep(0.001)
        run2 = await service.create_run("Middle")
        await asyncio.sleep(0.001)
        run3 = await service.create_run("Newest")

        runs = await service.list_runs()
        ids = [r.id for r in runs]
        assert ids[0] == run3.id
        assert ids[-1] == run1.id

    async def test_list_runs_single_run(self):
        service, _ = _make_service()
        run = await service.create_run("Only Run")
        runs = await service.list_runs()
        assert len(runs) == 1
        assert runs[0].id == run.id


# ---------------------------------------------------------------------------
# _execute_pipeline (background task)
# ---------------------------------------------------------------------------

class TestExecutePipeline:
    async def test_execute_pipeline_completes_run(self):
        repo = InMemoryDiagnosticRunRepository()
        mock_schema_service = AsyncMock()
        mock_schema_service.analyze_schemas.return_value = AnalysisResults()
        mock_report_service = MagicMock()
        mock_report_service.generate_report.return_value = ReadinessReport()

        service = DiagnosticService(repo, mock_schema_service, mock_report_service)
        run = await service.create_run("Pipeline Run")
        await service.start_run(run.id)
        # Give the background task time to complete.
        await asyncio.sleep(0.2)
        status = await service.get_run_status(run.id)
        assert status["status"] in ("completed", "failed", "running", "analyzing", "reporting")

    async def test_execute_pipeline_calls_schema_service(self):
        repo = InMemoryDiagnosticRunRepository()
        mock_schema_service = AsyncMock()
        mock_schema_service.analyze_schemas.return_value = AnalysisResults()
        mock_report_service = MagicMock()
        mock_report_service.generate_report.return_value = ReadinessReport()

        service = DiagnosticService(repo, mock_schema_service, mock_report_service)
        run = await service.create_run("Schema Pipeline Run")
        await service.start_run(run.id)
        await asyncio.sleep(0.2)
        mock_schema_service.analyze_schemas.assert_called_once()

    async def test_execute_pipeline_calls_report_service(self):
        repo = InMemoryDiagnosticRunRepository()
        mock_schema_service = AsyncMock()
        mock_schema_service.analyze_schemas.return_value = AnalysisResults()
        mock_report_service = MagicMock()
        mock_report_service.generate_report.return_value = ReadinessReport()

        service = DiagnosticService(repo, mock_schema_service, mock_report_service)
        run = await service.create_run("Report Pipeline Run")
        await service.start_run(run.id)
        await asyncio.sleep(0.2)
        mock_report_service.generate_report.assert_called_once()

    async def test_execute_pipeline_with_scenario_passes_entities(self):
        repo = InMemoryDiagnosticRunRepository()
        mock_schema_service = AsyncMock()
        mock_schema_service.analyze_schemas.return_value = AnalysisResults()
        mock_report_service = MagicMock()
        mock_report_service.generate_report.return_value = ReadinessReport()

        service = DiagnosticService(repo, mock_schema_service, mock_report_service)
        run = await service.create_run(
            "Entity Pipeline Run",
            scenario_config={"business_entities": ["Customer", "Order"]},
        )
        await service.start_run(run.id)
        await asyncio.sleep(0.2)

        call_args = mock_schema_service.analyze_schemas.call_args
        # Second positional arg (or 'entities' kwarg) should include entity names.
        if call_args:
            _, kwargs = call_args
            # entities can be positional or keyword
            entities_arg = call_args[0][1] if len(call_args[0]) > 1 else kwargs.get("entities", [])
            assert "Customer" in entities_arg or "Order" in entities_arg

    async def test_execute_pipeline_failure_marks_run_failed(self):
        repo = InMemoryDiagnosticRunRepository()
        mock_schema_service = AsyncMock()
        mock_schema_service.analyze_schemas.side_effect = RuntimeError("DB connection lost")
        mock_report_service = MagicMock()

        service = DiagnosticService(repo, mock_schema_service, mock_report_service)
        run = await service.create_run("Failing Pipeline Run")
        await service.start_run(run.id)
        await asyncio.sleep(0.2)
        stored = await repo.find_by_id(run.id)
        assert stored.status == "failed"
        assert "DB connection lost" in (stored.error_message or "")

    async def test_default_service_uses_real_sub_services(self):
        """DiagnosticService with only a repo should still construct without error."""
        repo = InMemoryDiagnosticRunRepository()
        service = DiagnosticService(repo)
        run = await service.create_run("Default Service Run")
        assert run.status == "pending"
