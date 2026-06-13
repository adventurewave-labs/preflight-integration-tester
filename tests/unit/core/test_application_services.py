"""
Tests for application services: DiagnosticService and ReportService.
"""
import asyncio
import pytest
from preflight.core.application.services import (
    DiagnosticService,
    ReportService,
)
from preflight.core.infrastructure.repositories.diagnostic_repository import (
    InMemoryDiagnosticRunRepository,
)
from preflight.core.domain.aggregates import (
    AnalysisResults,
    DiagnosticRun,
    SimulationScenario,
    ReadinessReport,
)
from preflight.core.domain.entities import (
    ConnectionProfile,
    SchemaInconsistency,
    PipelineBottleneck,
    MiddlewareGap,
    RemediationItem,
)
from preflight.core.domain.value_objects import (
    ConnectorType,
    SeverityLevel,
    EffortLevel,
    EffortEstimate,
    SystemType,
)


# ---------------------------------------------------------------------------
# DiagnosticService Tests
# ---------------------------------------------------------------------------

class TestDiagnosticService:
    """Tests for DiagnosticService."""

    def setup_method(self):
        self.repo = InMemoryDiagnosticRunRepository()
        self.service = DiagnosticService(self.repo)

    @pytest.mark.asyncio
    async def test_create_run_basic(self):
        run = await self.service.create_run("My Run")
        assert run.name == "My Run"
        assert run.status == "pending"
        assert run.id is not None

    @pytest.mark.asyncio
    async def test_create_run_persists(self):
        run = await self.service.create_run("Persisted Run")
        found = await self.repo.find_by_id(run.id)
        assert found is not None
        assert found.name == "Persisted Run"

    @pytest.mark.asyncio
    async def test_create_run_with_scenario(self):
        scenario_config = {
            "name": "CRM AI Assistant",
            "description": "Test deployment",
            "target_systems": ["salesforce", "sap"],
            "concurrent_users": 20,
            "queries_per_minute": 120,
            "peak_multiplier": 3.0,
            "response_time_target_ms": 300,
            "business_entities": ["Customer", "Order"],
            "use_case": "Customer service automation",
        }
        run = await self.service.create_run("CRM Test", scenario_config)
        assert run.scenario is not None
        assert run.scenario.name == "CRM AI Assistant"
        assert run.scenario.concurrent_users == 20
        assert run.scenario.peak_multiplier == 3.0
        assert "Customer" in run.scenario.business_entities

    @pytest.mark.asyncio
    async def test_create_run_without_scenario(self):
        run = await self.service.create_run("No Scenario Run", scenario_config=None)
        assert run.scenario is None

    @pytest.mark.asyncio
    async def test_get_run_status(self):
        run = await self.service.create_run("Status Test")
        status = await self.service.get_run_status(run.id)
        assert status["run_id"] == run.id
        assert status["name"] == "Status Test"
        assert status["status"] == "pending"
        assert status["progress_pct"] == 0.0
        assert status["started_at"] is None
        assert status["completed_at"] is None
        assert status["error_message"] is None

    @pytest.mark.asyncio
    async def test_get_run_status_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            await self.service.get_run_status("nonexistent-id")

    @pytest.mark.asyncio
    async def test_start_run_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            await self.service.start_run("nonexistent-id")

    @pytest.mark.asyncio
    async def test_start_run_wrong_status(self):
        run = await self.service.create_run("Wrong Status")
        run.start()  # already started
        await self.repo.save(run)
        with pytest.raises(ValueError, match="Cannot start run"):
            await self.service.start_run(run.id)

    @pytest.mark.asyncio
    async def test_get_run_results_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            await self.service.get_run_results("nonexistent-id")

    @pytest.mark.asyncio
    async def test_get_run_results_not_completed(self):
        run = await self.service.create_run("Not Done Yet")
        with pytest.raises(RuntimeError, match="has not completed yet"):
            await self.service.get_run_results(run.id)

    @pytest.mark.asyncio
    async def test_get_run_results_failed(self):
        run = await self.service.create_run("Failed Run")
        run.fail("timeout")
        await self.repo.save(run)
        # "failed" should be accessible
        result = await self.service.get_run_results(run.id)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_list_runs_empty(self):
        runs = await self.service.list_runs()
        assert runs == []

    @pytest.mark.asyncio
    async def test_list_runs_returns_all(self):
        await self.service.create_run("Run A")
        await self.service.create_run("Run B")
        await self.service.create_run("Run C")
        runs = await self.service.list_runs()
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_list_runs_sorted_newest_first(self):
        import time
        r1 = await self.service.create_run("First")
        time.sleep(0.01)  # ensure distinct timestamps
        r2 = await self.service.create_run("Second")
        time.sleep(0.01)
        r3 = await self.service.create_run("Third")

        runs = await self.service.list_runs()
        assert runs[0].name == "Third"
        assert runs[2].name == "First"

    @pytest.mark.asyncio
    async def test_scenario_config_with_defaults(self):
        """Scenario defaults are filled correctly when not provided."""
        scenario_config = {"name": "Minimal"}
        run = await self.service.create_run("Minimal", scenario_config)
        assert run.scenario.concurrent_users == 10
        assert run.scenario.queries_per_minute == 100
        assert run.scenario.peak_multiplier == 2.0


# ---------------------------------------------------------------------------
# ReportService Tests
# ---------------------------------------------------------------------------

class TestReportService:
    """Tests for ReportService."""

    def setup_method(self):
        self.service = ReportService()

    def test_generate_report_basic(self):
        run = DiagnosticRun(name="Test Run")
        report = self.service.generate_report(run)
        assert isinstance(report, ReadinessReport)
        assert report.readiness_score is not None
        assert report.verdict is not None
        assert report.executive_summary != ""
        assert report.generated_at is not None

    def test_generate_report_no_analysis(self):
        """With no analysis, score should be 100 (perfect)."""
        run = DiagnosticRun(name="Clean Run")
        report = self.service.generate_report(run)
        assert report.readiness_score.value == 100.0

    def test_generate_report_with_issues(self):
        run = DiagnosticRun(name="Problematic Run")
        analysis = AnalysisResults()
        analysis.schema_inconsistencies.append(
            SchemaInconsistency(
                entity_name="Customer",
                inconsistency_type="key_mismatch",
                severity=SeverityLevel.CRITICAL,
            )
        )
        run.complete_analysis(analysis)
        report = self.service.generate_report(run)
        assert report.readiness_score.value < 100.0

    def test_generate_report_findings_summary(self):
        run = DiagnosticRun(name="Summary Test")
        analysis = AnalysisResults()
        analysis.schema_inconsistencies.append(
            SchemaInconsistency(entity_name="Account", severity=SeverityLevel.HIGH)
        )
        analysis.data_quality_issues.append({"title": "Null IDs"})
        run.complete_analysis(analysis)
        report = self.service.generate_report(run)
        assert report.findings_summary["schema_inconsistencies"] == 1
        assert report.findings_summary["data_quality_issues"] == 1

    def test_build_remediation_plan_empty(self):
        analysis = AnalysisResults()
        plan = self.service.build_remediation_plan(analysis)
        assert plan == []

    def test_build_remediation_plan_with_schema_issues(self):
        analysis = AnalysisResults()
        analysis.schema_inconsistencies.append(
            SchemaInconsistency(
                entity_name="Customer",
                inconsistency_type="type_mismatch",
                severity=SeverityLevel.HIGH,
                remediation_hint="Align data types",
            )
        )
        plan = self.service.build_remediation_plan(analysis)
        assert len(plan) == 1
        assert plan[0].category == "schema"
        assert plan[0].recommended_sequence == 1

    def test_build_remediation_plan_with_bottleneck(self):
        analysis = AnalysisResults()
        analysis.pipeline_bottlenecks.append(
            PipelineBottleneck(
                pipeline_name="CRM Pipeline",
                system="salesforce",
                bottleneck_type="latency",
                severity=SeverityLevel.HIGH,
                description="High latency under load",
                observed_value=2500.0,
                threshold_value=1000.0,
                unit="ms",
            )
        )
        plan = self.service.build_remediation_plan(analysis)
        assert len(plan) == 1
        assert plan[0].category == "pipeline"

    def test_build_remediation_plan_with_middleware_gap(self):
        analysis = AnalysisResults()
        analysis.middleware_gaps.append(
            MiddlewareGap(
                gap_type="missing_api",
                source_system="sap",
                target_system="salesforce",
                description="No API integration exists",
                severity=SeverityLevel.CRITICAL,
                blocking=True,
            )
        )
        plan = self.service.build_remediation_plan(analysis)
        assert len(plan) == 1
        assert plan[0].category == "middleware"
        # Blocking + CRITICAL should get highest priority
        assert plan[0].priority == 10

    def test_build_remediation_plan_with_dq_issues(self):
        analysis = AnalysisResults()
        analysis.data_quality_issues.append({
            "title": "Null Primary Keys",
            "description": "IDs are null in 5% of records",
            "priority": 8,
            "severity": "CRITICAL",
        })
        plan = self.service.build_remediation_plan(analysis)
        assert len(plan) == 1
        assert plan[0].category == "data_quality"

    def test_build_remediation_plan_sorted_by_priority(self):
        analysis = AnalysisResults()
        # Low severity schema issue
        analysis.schema_inconsistencies.append(
            SchemaInconsistency(
                entity_name="A",
                severity=SeverityLevel.LOW,
            )
        )
        # High severity gap
        analysis.middleware_gaps.append(
            MiddlewareGap(
                gap_type="missing_api",
                source_system="a",
                target_system="b",
                severity=SeverityLevel.CRITICAL,
                blocking=True,
            )
        )
        plan = self.service.build_remediation_plan(analysis)
        assert len(plan) == 2
        # CRITICAL blocking gap should come first (higher priority)
        assert plan[0].priority >= plan[1].priority

    def test_calculate_total_effort_empty(self):
        effort = self.service.calculate_total_effort([])
        assert effort.level == EffortLevel.TRIVIAL

    def test_calculate_total_effort_multiple_items(self):
        items = [
            RemediationItem(
                effort_estimate=EffortEstimate.from_level(EffortLevel.MEDIUM)
            ),
            RemediationItem(
                effort_estimate=EffortEstimate.from_level(EffortLevel.LOW)
            ),
        ]
        effort = self.service.calculate_total_effort(items)
        assert effort.min_days >= 0
        assert effort.max_days >= effort.min_days
        assert 0.0 < effort.confidence <= 1.0

    def test_generate_executive_summary_no_issues(self):
        analysis = AnalysisResults()
        summary = self.service.generate_executive_summary(analysis, scenario=None)
        assert isinstance(summary, str)
        assert len(summary) > 50
        assert "100" in summary  # Score should be 100

    def test_generate_executive_summary_with_critical_issues(self):
        analysis = AnalysisResults()
        analysis.schema_inconsistencies.append(
            SchemaInconsistency(
                entity_name="Customer",
                severity=SeverityLevel.CRITICAL,
            )
        )
        summary = self.service.generate_executive_summary(analysis, scenario=None)
        assert "critical" in summary.lower()

    def test_generate_executive_summary_with_scenario(self):
        analysis = AnalysisResults()
        scenario = SimulationScenario(
            name="CRM AI",
            use_case="customer service automation",
            target_systems=["salesforce"],
        )
        summary = self.service.generate_executive_summary(analysis, scenario)
        assert "salesforce" in summary.lower() or "customer" in summary.lower()

    def test_generate_executive_summary_with_blocking_gaps(self):
        analysis = AnalysisResults()
        analysis.middleware_gaps.append(
            MiddlewareGap(
                gap_type="missing_api",
                source_system="a",
                target_system="b",
                blocking=True,
                severity=SeverityLevel.CRITICAL,
            )
        )
        summary = self.service.generate_executive_summary(analysis, scenario=None)
        assert "blocking" in summary.lower() or "middleware" in summary.lower()

    def test_report_remediation_plan_assigned(self):
        run = DiagnosticRun(name="Remediation Test")
        analysis = AnalysisResults()
        analysis.schema_inconsistencies.append(
            SchemaInconsistency(
                entity_name="Order",
                severity=SeverityLevel.MEDIUM,
            )
        )
        run.complete_analysis(analysis)
        report = self.service.generate_report(run)
        assert len(report.remediation_plan) > 0
        assert all(r.recommended_sequence > 0 for r in report.remediation_plan)

    def test_report_total_effort_computed(self):
        run = DiagnosticRun(name="Effort Test")
        analysis = AnalysisResults()
        analysis.middleware_gaps.append(
            MiddlewareGap(
                gap_type="missing_etl",
                source_system="a",
                target_system="b",
                severity=SeverityLevel.HIGH,
            )
        )
        run.complete_analysis(analysis)
        report = self.service.generate_report(run)
        assert report.total_effort_estimate is not None
        assert report.total_effort_estimate.max_days >= 0
