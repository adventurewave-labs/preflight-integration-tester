"""
Integration tests for the full diagnostic pipeline.

Tests the end-to-end flow: connect → analyze → calculate → report.
"""
import asyncio
import pytest
from preflight.analysis.schema_analyzer import SchemaAnalyzer
from preflight.analysis.middleware_analyzer import MiddlewareAnalyzer
from preflight.analysis.readiness_calculator import ReadinessCalculator
from preflight.reporting.html_reporter import HTMLReporter
from preflight.reporting.executive_summary import ExecutiveSummaryGenerator


class TestFullDiagnosticPipeline:
    """End-to-end diagnostic pipeline tests."""

    def test_schema_to_readiness_pipeline(self, multi_system_schemas, sample_scenario):
        """Test full flow from schema analysis to readiness score."""
        # Step 1: Schema Analysis
        analyzer = SchemaAnalyzer(similarity_threshold=0.7)
        schema_results = analyzer.analyze_all(multi_system_schemas)
        inconsistencies = analyzer.generate_inconsistency_report(schema_results)

        assert isinstance(inconsistencies, list)
        # The intentionally mismatched schemas should produce issues
        assert len(inconsistencies) > 0

        # Step 2: Middleware Analysis
        mw_analyzer = MiddlewareAnalyzer()
        connections = [
            {'name': sys_name, 'type': 'ERP' if 'sap' in sys_name else 'CRM'}
            for sys_name in multi_system_schemas.keys()
        ]
        gaps = mw_analyzer.analyze(
            connected_systems=connections,
            scenario=sample_scenario,
            schema_analysis={'inconsistencies': inconsistencies},
        )

        # Step 3: Calculate Readiness Score
        calc = ReadinessCalculator()
        breakdown = calc.calculate(
            schema_inconsistencies=inconsistencies,
            pipeline_results=[],
            middleware_gaps=gaps,
            data_quality_results=[],
        )

        assert breakdown.overall_score is not None
        assert 0 <= breakdown.overall_score <= 100
        assert breakdown.verdict in ('GO', 'NOT_YET', 'NOT_READY')

        # With intentionally mismatched schemas, score should not be perfect
        assert breakdown.overall_score < 100

        print(f"\nPipeline result: {breakdown.overall_score:.1f}% ({breakdown.verdict})")
        print(f"  Schema issues: {len(inconsistencies)}")
        print(f"  Middleware gaps: {len(gaps)}")

    def test_report_generation_from_analysis(self, sample_schema_inconsistencies, sample_middleware_gaps):
        """Test report generation from analysis results."""
        # Calculate score
        calc = ReadinessCalculator()
        breakdown = calc.calculate(
            schema_inconsistencies=sample_schema_inconsistencies,
            pipeline_results=[],
            middleware_gaps=sample_middleware_gaps,
            data_quality_results=[],
        )

        # Generate executive summary
        summary_gen = ExecutiveSummaryGenerator()
        summary = summary_gen.generate(
            verdict=breakdown.verdict,
            score=breakdown.overall_score,
            schema_issues=sample_schema_inconsistencies,
            pipeline_issues=[],
            middleware_gaps=sample_middleware_gaps,
            remediation_weeks=breakdown.estimated_remediation_weeks,
        )

        assert summary
        assert len(summary) > 100  # Non-trivial summary
        assert breakdown.verdict.replace('_', ' ') in summary or 'Readiness' in summary

    def test_html_report_generation(self, sample_schema_inconsistencies, sample_middleware_gaps):
        """Test full HTML report generation."""
        calc = ReadinessCalculator()
        breakdown = calc.calculate(
            schema_inconsistencies=sample_schema_inconsistencies,
            pipeline_results=[],
            middleware_gaps=sample_middleware_gaps,
            data_quality_results=[],
        )

        summary_gen = ExecutiveSummaryGenerator()
        summary = summary_gen.generate(
            verdict=breakdown.verdict,
            score=breakdown.overall_score,
            schema_issues=sample_schema_inconsistencies,
            pipeline_issues=[],
            middleware_gaps=sample_middleware_gaps,
        )

        report_data = {
            'readiness_score': breakdown.overall_score,
            'verdict': breakdown.verdict,
            'scenario_name': 'Customer Service AI',
            'executive_summary': summary,
            'schema_inconsistencies': [
                {
                    'entity_name': i.get('entity', ''),
                    'source_system': i.get('source', ''),
                    'target_system': i.get('target', ''),
                    'inconsistency_type': i.get('type', ''),
                    'severity': i.get('severity', ''),
                    'impact_description': i.get('detail', ''),
                }
                for i in sample_schema_inconsistencies
            ],
            'middleware_gaps': [
                {
                    'gap_type': g.get('type', ''),
                    'severity': g.get('severity', ''),
                    'blocking': g.get('blocking', False),
                    'description': g.get('description', ''),
                    'effort_min_days': g.get('effort_days', (5, 20))[0],
                    'effort_max_days': g.get('effort_days', (5, 20))[1],
                }
                for g in sample_middleware_gaps
            ],
            'remediation_plan': [],
            'critical_count': breakdown.critical_issues,
            'total_issues': breakdown.total_issues,
            'remediation_weeks': str(int(breakdown.estimated_remediation_weeks)) if breakdown.estimated_remediation_weeks else 'TBD',
        }

        reporter = HTMLReporter()
        html = reporter.generate(report_data)

        assert html
        assert '<!DOCTYPE html>' in html
        assert str(int(breakdown.overall_score)) in html or f"{breakdown.overall_score:.1f}" in html
        assert 'Preflight' in html

        # Check key sections present
        assert 'Readiness' in html
        assert 'Executive Summary' in html

    def test_consistent_results_on_same_input(self, multi_system_schemas):
        """Results should be consistent for same input."""
        analyzer = SchemaAnalyzer()
        calc = ReadinessCalculator()

        # Run twice
        results1 = analyzer.analyze_all(multi_system_schemas)
        issues1 = analyzer.generate_inconsistency_report(results1)
        breakdown1 = calc.calculate(issues1, [], [], [])

        results2 = analyzer.analyze_all(multi_system_schemas)
        issues2 = analyzer.generate_inconsistency_report(results2)
        breakdown2 = calc.calculate(issues2, [], [], [])

        assert breakdown1.overall_score == breakdown2.overall_score
        assert breakdown1.verdict == breakdown2.verdict
