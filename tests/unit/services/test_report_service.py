"""
Unit tests for ReportService.
"""
import pytest

from preflight.core.application.services.report_service import ReportService
from preflight.core.domain.aggregates import (
    AnalysisResults,
    DiagnosticRun,
    ReadinessReport,
    SimulationScenario,
)
from preflight.core.domain.entities import (
    MiddlewareGap,
    PipelineBottleneck,
    SchemaInconsistency,
)
from preflight.core.domain.value_objects import (
    EffortEstimate,
    EffortLevel,
    ReadinessVerdict,
    SeverityLevel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(analysis: AnalysisResults = None, scenario: SimulationScenario = None) -> DiagnosticRun:
    run = DiagnosticRun(name="Test Run", scenario=scenario)
    if analysis is not None:
        run.analysis = analysis
    return run


def _critical_schema_issue() -> SchemaInconsistency:
    return SchemaInconsistency(
        entity_name="Customer",
        source_system="salesforce",
        target_system="sap",
        inconsistency_type="type_mismatch",
        field_name="id",
        severity=SeverityLevel.CRITICAL,
        impact_description="Type mismatch on primary key",
        remediation_hint="Align key types",
    )


def _blocking_gap() -> MiddlewareGap:
    return MiddlewareGap(
        gap_type="missing_api",
        source_system="salesforce",
        target_system="sap",
        description="No API endpoint for sync",
        severity=SeverityLevel.CRITICAL,
        blocking=True,
        recommended_solution="Build REST adapter",
        effort_estimate=EffortEstimate.from_level(EffortLevel.HIGH),
    )


def _high_bottleneck() -> PipelineBottleneck:
    return PipelineBottleneck(
        pipeline_name="Customer ETL",
        system="sap",
        bottleneck_type="latency",
        observed_value=1500.0,
        threshold_value=500.0,
        unit="ms",
        severity=SeverityLevel.HIGH,
        description="ETL latency exceeds threshold",
    )


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_generate_report_returns_readiness_report_instance(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert isinstance(report, ReadinessReport)

    def test_generate_report_empty_run(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert report.readiness_score is not None
        # No issues → perfect score → GO
        assert report.readiness_score.value == 100.0
        assert report.verdict == ReadinessVerdict.GO

    def test_generate_report_sets_generated_at(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert report.generated_at is not None

    def test_report_has_verdict(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert report.verdict in (
            ReadinessVerdict.GO,
            ReadinessVerdict.NOT_YET,
            ReadinessVerdict.NOT_READY,
        )

    def test_report_has_score_in_range(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert 0.0 <= report.readiness_score.value <= 100.0

    def test_report_has_executive_summary(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert isinstance(report.executive_summary, str)
        assert len(report.executive_summary) > 0

    def test_report_has_technical_summary(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert isinstance(report.technical_summary, str)
        assert "Technical" in report.technical_summary

    def test_report_has_findings_summary(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        for key in (
            "schema_inconsistencies",
            "pipeline_bottlenecks",
            "middleware_gaps",
            "data_quality_issues",
            "critical_issues",
            "blocking_gaps",
        ):
            assert key in report.findings_summary, f"Missing key: {key}"

    def test_high_score_yields_go_verdict(self):
        service = ReportService()
        run = _make_run(analysis=AnalysisResults())
        report = service.generate_report(run)
        assert report.verdict == ReadinessVerdict.GO
        assert report.readiness_score.value >= 80.0

    def test_generate_report_with_schema_issues(self):
        service = ReportService()
        analysis = AnalysisResults(
            schema_inconsistencies=[_critical_schema_issue()]
        )
        run = _make_run(analysis=analysis)
        report = service.generate_report(run)
        assert report.findings_summary["schema_inconsistencies"] == 1
        assert report.readiness_score.value < 100.0

    def test_generate_report_with_middleware_gaps(self):
        service = ReportService()
        analysis = AnalysisResults(middleware_gaps=[_blocking_gap()])
        run = _make_run(analysis=analysis)
        report = service.generate_report(run)
        assert report.findings_summary["middleware_gaps"] == 1
        assert report.findings_summary["blocking_gaps"] == 1

    def test_generate_report_with_pipeline_bottlenecks(self):
        service = ReportService()
        analysis = AnalysisResults(pipeline_bottlenecks=[_high_bottleneck()])
        run = _make_run(analysis=analysis)
        report = service.generate_report(run)
        assert report.findings_summary["pipeline_bottlenecks"] == 1

    def test_many_critical_issues_yields_not_ready(self):
        """Stacking many CRITICAL issues should drop score below 50."""
        service = ReportService()
        critical_issues = [_critical_schema_issue() for _ in range(10)]
        analysis = AnalysisResults(schema_inconsistencies=critical_issues)
        run = _make_run(analysis=analysis)
        report = service.generate_report(run)
        assert report.verdict == ReadinessVerdict.NOT_READY
        assert report.readiness_score.value < 50.0

    def test_generate_report_with_data_quality_issues(self):
        service = ReportService()
        analysis = AnalysisResults(
            data_quality_issues=[
                {"title": "Null values", "description": "Too many nulls", "priority": 5,
                 "severity": "MEDIUM"},
                {"title": "Duplicates", "description": "Duplicate records", "priority": 7,
                 "severity": "HIGH"},
            ]
        )
        run = _make_run(analysis=analysis)
        report = service.generate_report(run)
        assert report.findings_summary["data_quality_issues"] == 2

    def test_remediation_plan_generated_when_issues_exist(self):
        service = ReportService()
        analysis = AnalysisResults(
            schema_inconsistencies=[_critical_schema_issue()],
            middleware_gaps=[_blocking_gap()],
            pipeline_bottlenecks=[_high_bottleneck()],
        )
        run = _make_run(analysis=analysis)
        report = service.generate_report(run)
        assert len(report.remediation_plan) > 0

    def test_remediation_plan_empty_when_no_issues(self):
        service = ReportService()
        run = _make_run(analysis=AnalysisResults())
        report = service.generate_report(run)
        assert report.remediation_plan == []

    def test_total_effort_estimate_set(self):
        service = ReportService()
        analysis = AnalysisResults(schema_inconsistencies=[_critical_schema_issue()])
        run = _make_run(analysis=analysis)
        report = service.generate_report(run)
        assert report.total_effort_estimate is not None

    def test_total_effort_estimate_trivial_when_no_items(self):
        service = ReportService()
        run = _make_run()
        report = service.generate_report(run)
        assert report.total_effort_estimate is not None
        assert report.total_effort_estimate.level == EffortLevel.TRIVIAL

    def test_generate_report_with_scenario(self):
        service = ReportService()
        scenario = SimulationScenario(
            name="CRM AI",
            use_case="customer service",
            target_systems=["salesforce"],
        )
        run = _make_run(scenario=scenario)
        report = service.generate_report(run)
        assert "salesforce" in report.executive_summary
        assert "customer service" in report.executive_summary


# ---------------------------------------------------------------------------
# generate_executive_summary
# ---------------------------------------------------------------------------

class TestGenerateExecutiveSummary:
    def test_summary_mentions_score(self):
        service = ReportService()
        analysis = AnalysisResults()
        summary = service.generate_executive_summary(analysis, scenario=None)
        assert "100" in summary  # perfect score

    def test_summary_with_critical_issues_mentions_them(self):
        service = ReportService()
        analysis = AnalysisResults(
            schema_inconsistencies=[_critical_schema_issue()]
        )
        summary = service.generate_executive_summary(analysis, scenario=None)
        assert "critical" in summary.lower() or "1" in summary

    def test_summary_with_blocking_gaps_mentions_them(self):
        service = ReportService()
        analysis = AnalysisResults(middleware_gaps=[_blocking_gap()])
        summary = service.generate_executive_summary(analysis, scenario=None)
        assert "blocking" in summary.lower() or "middleware" in summary.lower()

    def test_summary_includes_use_case_when_scenario_provided(self):
        service = ReportService()
        scenario = SimulationScenario(use_case="inventory forecasting AI")
        analysis = AnalysisResults()
        summary = service.generate_executive_summary(analysis, scenario=scenario)
        assert "inventory forecasting AI" in summary

    def test_summary_includes_target_systems_from_scenario(self):
        service = ReportService()
        scenario = SimulationScenario(target_systems=["oracle", "snowflake"])
        analysis = AnalysisResults()
        summary = service.generate_executive_summary(analysis, scenario=scenario)
        assert "oracle" in summary and "snowflake" in summary

    def test_summary_without_scenario_uses_default_phrase(self):
        service = ReportService()
        analysis = AnalysisResults()
        summary = service.generate_executive_summary(analysis, scenario=None)
        assert "planned AI deployment" in summary or "enterprise" in summary.lower()


# ---------------------------------------------------------------------------
# build_remediation_plan
# ---------------------------------------------------------------------------

class TestBuildRemediationPlan:
    def test_empty_analysis_returns_empty_plan(self):
        service = ReportService()
        plan = service.build_remediation_plan(AnalysisResults())
        assert plan == []

    def test_schema_issues_produce_schema_items(self):
        service = ReportService()
        analysis = AnalysisResults(schema_inconsistencies=[_critical_schema_issue()])
        plan = service.build_remediation_plan(analysis)
        assert any(item.category == "schema" for item in plan)

    def test_bottleneck_produces_pipeline_items(self):
        service = ReportService()
        analysis = AnalysisResults(pipeline_bottlenecks=[_high_bottleneck()])
        plan = service.build_remediation_plan(analysis)
        assert any(item.category == "pipeline" for item in plan)

    def test_gap_produces_middleware_items(self):
        service = ReportService()
        analysis = AnalysisResults(middleware_gaps=[_blocking_gap()])
        plan = service.build_remediation_plan(analysis)
        assert any(item.category == "middleware" for item in plan)

    def test_data_quality_produces_data_quality_items(self):
        service = ReportService()
        analysis = AnalysisResults(
            data_quality_issues=[
                {"title": "Too many nulls", "description": "Desc", "priority": 5,
                 "severity": "MEDIUM"}
            ]
        )
        plan = service.build_remediation_plan(analysis)
        assert any(item.category == "data_quality" for item in plan)

    def test_items_have_sequential_recommended_sequence(self):
        service = ReportService()
        analysis = AnalysisResults(
            schema_inconsistencies=[_critical_schema_issue()],
            middleware_gaps=[_blocking_gap()],
        )
        plan = service.build_remediation_plan(analysis)
        for idx, item in enumerate(plan, start=1):
            assert item.recommended_sequence == idx

    def test_critical_items_sorted_before_low(self):
        service = ReportService()
        low_issue = SchemaInconsistency(
            entity_name="Order",
            source_system="erp",
            target_system="crm",
            inconsistency_type="missing_field",
            severity=SeverityLevel.LOW,
        )
        analysis = AnalysisResults(
            schema_inconsistencies=[low_issue, _critical_schema_issue()]
        )
        plan = service.build_remediation_plan(analysis)
        # Critical comes first (higher priority).
        assert plan[0].severity == SeverityLevel.CRITICAL


# ---------------------------------------------------------------------------
# calculate_total_effort
# ---------------------------------------------------------------------------

class TestCalculateTotalEffort:
    def test_empty_items_returns_trivial(self):
        service = ReportService()
        estimate = service.calculate_total_effort([])
        assert estimate.level == EffortLevel.TRIVIAL

    def test_single_low_item_returns_estimate(self):
        service = ReportService()
        from preflight.core.domain.entities import RemediationItem
        item = RemediationItem(
            effort_estimate=EffortEstimate.from_level(EffortLevel.LOW)
        )
        estimate = service.calculate_total_effort([item])
        assert estimate is not None
        assert isinstance(estimate, EffortEstimate)

    def test_many_high_effort_items_result_in_high_or_critical(self):
        service = ReportService()
        from preflight.core.domain.entities import RemediationItem
        items = [
            RemediationItem(effort_estimate=EffortEstimate.from_level(EffortLevel.HIGH))
            for _ in range(5)
        ]
        estimate = service.calculate_total_effort(items)
        assert estimate.level in (EffortLevel.HIGH, EffortLevel.CRITICAL)

    def test_confidence_is_average(self):
        service = ReportService()
        from preflight.core.domain.entities import RemediationItem
        items = [
            RemediationItem(effort_estimate=EffortEstimate(min_days=1, max_days=5,
                                                           level=EffortLevel.LOW, confidence=0.8)),
            RemediationItem(effort_estimate=EffortEstimate(min_days=1, max_days=5,
                                                           level=EffortLevel.LOW, confidence=0.6)),
        ]
        estimate = service.calculate_total_effort(items)
        assert abs(estimate.confidence - 0.7) < 0.01
