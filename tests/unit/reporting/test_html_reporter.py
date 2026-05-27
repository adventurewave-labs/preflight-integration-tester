"""
Unit tests for HTMLReporter.
"""
import pytest
from preflight.reporting.html_reporter import HTMLReporter
from preflight.reporting.executive_summary import ExecutiveSummaryGenerator


class TestHTMLReporter:
    """Tests for HTML report generation."""

    def setup_method(self):
        self.reporter = HTMLReporter()
        self.base_report = {
            'readiness_score': 72.5,
            'verdict': 'NOT_YET',
            'scenario_name': 'Test AI Deployment',
            'executive_summary': 'Test summary content.',
            'schema_inconsistencies': [],
            'middleware_gaps': [],
            'pipeline_bottlenecks': [],
            'remediation_plan': [],
            'critical_count': 0,
            'total_issues': 0,
            'remediation_weeks': '8',
        }

    def test_generates_valid_html(self):
        html = self.reporter.generate(self.base_report)
        assert html.startswith('<!DOCTYPE html>') or '<!DOCTYPE html>' in html
        assert '</html>' in html

    def test_contains_score(self):
        html = self.reporter.generate(self.base_report)
        assert '72' in html

    def test_contains_verdict(self):
        html = self.reporter.generate(self.base_report)
        assert 'NOT YET' in html or 'NOT_YET' in html

    def test_contains_scenario_name(self):
        html = self.reporter.generate(self.base_report)
        assert 'Test AI Deployment' in html

    def test_contains_executive_summary(self):
        html = self.reporter.generate(self.base_report)
        assert 'Test summary content.' in html

    def test_go_verdict_green(self):
        report = {**self.base_report, 'readiness_score': 90.0, 'verdict': 'GO'}
        html = self.reporter.generate(report)
        assert 'GO' in html
        assert 'green' in html.lower()

    def test_not_ready_verdict_red(self):
        report = {**self.base_report, 'readiness_score': 30.0, 'verdict': 'NOT_READY'}
        html = self.reporter.generate(report)
        assert 'NOT READY' in html or 'NOT_READY' in html
        assert 'red' in html.lower()

    def test_schema_inconsistencies_rendered(self):
        report = {
            **self.base_report,
            'schema_inconsistencies': [
                {
                    'entity_name': 'Customer',
                    'source_system': 'salesforce',
                    'target_system': 'sap',
                    'inconsistency_type': 'key_mismatch',
                    'severity': 'CRITICAL',
                    'impact_description': 'Customer IDs are incompatible',
                }
            ]
        }
        html = self.reporter.generate(report)
        assert 'Customer' in html
        assert 'key_mismatch' in html or 'CRITICAL' in html

    def test_middleware_gaps_rendered(self):
        report = {
            **self.base_report,
            'middleware_gaps': [
                {
                    'gap_type': 'semantic_layer',
                    'severity': 'CRITICAL',
                    'blocking': True,
                    'description': 'Missing semantic layer',
                    'effort_min_days': 10,
                    'effort_max_days': 30,
                }
            ]
        }
        html = self.reporter.generate(report)
        assert 'semantic_layer' in html
        assert 'Missing semantic layer' in html

    def test_remediation_plan_rendered(self):
        report = {
            **self.base_report,
            'remediation_plan': [
                {
                    'id': 'rem_1',
                    'title': 'Fix Customer IDs',
                    'description': 'Align customer ID formats across systems',
                    'category': 'schema',
                    'priority': 10,
                    'severity': 'CRITICAL',
                    'effort_min_days': 5,
                    'effort_max_days': 15,
                    'recommended_sequence': 1,
                }
            ]
        }
        html = self.reporter.generate(report)
        assert 'Fix Customer IDs' in html

    def test_html_is_self_contained(self):
        """HTML should not reference external resources."""
        html = self.reporter.generate(self.base_report)
        # No external stylesheets or scripts
        assert 'href="http' not in html
        assert 'src="http' not in html

    def test_save_to_file(self, tmp_path):
        report_path = tmp_path / 'test-report.html'
        self.reporter.save(self.base_report, str(report_path))

        assert report_path.exists()
        content = report_path.read_text()
        assert '<!DOCTYPE html>' in content or 'html' in content

    def test_non_trivial_output_size(self):
        html = self.reporter.generate(self.base_report)
        assert len(html) > 1000  # Should be a substantial report


class TestExecutiveSummaryGenerator:
    """Tests for executive summary generation."""

    def setup_method(self):
        self.gen = ExecutiveSummaryGenerator()

    def test_go_verdict_summary(self):
        summary = self.gen.generate(
            verdict='GO',
            score=85.0,
            schema_issues=[],
            pipeline_issues=[],
            middleware_gaps=[],
        )
        assert summary
        assert '85' in summary or 'GO' in summary

    def test_not_ready_summary_mentions_issues(self):
        summary = self.gen.generate(
            verdict='NOT_READY',
            score=30.0,
            schema_issues=[
                {'severity': 'CRITICAL', 'type': 'key_mismatch'},
                {'severity': 'HIGH', 'type': 'missing_field'},
            ],
            pipeline_issues=[],
            middleware_gaps=[
                {'severity': 'CRITICAL', 'blocking': True},
            ],
        )
        assert summary
        assert len(summary) > 100

    def test_summary_mentions_schema_issues(self):
        summary = self.gen.generate(
            verdict='NOT_YET',
            score=60.0,
            schema_issues=[{'severity': 'HIGH'}, {'severity': 'MEDIUM'}],
            pipeline_issues=[],
            middleware_gaps=[],
        )
        assert 'Schema' in summary or 'schema' in summary or 'inconsistenc' in summary.lower()

    def test_summary_includes_remediation_timeline(self):
        summary = self.gen.generate(
            verdict='NOT_YET',
            score=65.0,
            schema_issues=[],
            pipeline_issues=[],
            middleware_gaps=[],
            remediation_weeks=8.0,
        )
        assert '8' in summary

    def test_all_verdict_types_produce_output(self):
        for verdict in ['GO', 'NOT_YET', 'NOT_READY']:
            summary = self.gen.generate(
                verdict=verdict,
                score=70.0,
                schema_issues=[],
                pipeline_issues=[],
                middleware_gaps=[],
            )
            assert summary
            assert len(summary) > 50
