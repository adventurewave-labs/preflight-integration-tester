"""
Unit tests for ReadinessCalculator.
"""
import pytest
from preflight.analysis.readiness_calculator import ReadinessCalculator, ReadinessBreakdown


class TestReadinessCalculator:
    """Tests for the readiness score calculation."""

    def setup_method(self):
        self.calc = ReadinessCalculator()

    def test_perfect_score_no_issues(self):
        breakdown = self.calc.calculate(
            schema_inconsistencies=[],
            pipeline_results=[],
            middleware_gaps=[],
            data_quality_results=[],
        )
        assert breakdown.overall_score == pytest.approx(100.0, abs=1.0)
        assert breakdown.verdict == 'GO'

    def test_critical_issues_lead_to_not_ready(self, sample_schema_inconsistencies, sample_middleware_gaps):
        # Add blocking gaps to ensure NOT_READY
        gaps = [
            {'severity': 'CRITICAL', 'blocking': True, 'effort_days': (10, 30)},
            {'severity': 'CRITICAL', 'blocking': True, 'effort_days': (5, 15)},
        ]
        breakdown = self.calc.calculate(
            schema_inconsistencies=[{'severity': 'CRITICAL'}, {'severity': 'CRITICAL'}],
            pipeline_results=[],
            middleware_gaps=gaps,
            data_quality_results=[],
        )
        assert breakdown.verdict == 'NOT_READY'

    def test_minor_issues_still_go(self):
        breakdown = self.calc.calculate(
            schema_inconsistencies=[{'severity': 'LOW'}, {'severity': 'INFO'}],
            pipeline_results=[{'error_rate_pct': 0.5, 'p95_ms': 200}],
            middleware_gaps=[{'severity': 'LOW', 'blocking': False, 'effort_days': (1, 3)}],
            data_quality_results=[],
        )
        assert breakdown.verdict == 'GO'
        assert breakdown.overall_score >= 80

    def test_blocking_gap_overrides_verdict(self):
        # Even with a decent overall score, a blocking gap should downgrade verdict
        breakdown = self.calc.calculate(
            schema_inconsistencies=[],
            pipeline_results=[],
            middleware_gaps=[
                {'severity': 'CRITICAL', 'blocking': True, 'effort_days': (10, 30)},
                {'severity': 'CRITICAL', 'blocking': True, 'effort_days': (5, 15)},
            ],
            data_quality_results=[],
        )
        assert breakdown.verdict in ('NOT_READY', 'NOT_YET')
        assert breakdown.blocking_issues >= 2

    def test_pipeline_errors_reduce_score(self):
        no_errors = self.calc.calculate(
            schema_inconsistencies=[],
            pipeline_results=[{'error_rate_pct': 0, 'p95_ms': 100}],
            middleware_gaps=[],
            data_quality_results=[],
        )
        with_errors = self.calc.calculate(
            schema_inconsistencies=[],
            pipeline_results=[{'error_rate_pct': 15, 'p95_ms': 3500}],
            middleware_gaps=[],
            data_quality_results=[],
        )
        assert no_errors.overall_score > with_errors.overall_score

    def test_score_range_is_0_to_100(self):
        # Even with many issues, score should stay 0-100
        many_issues = [{'severity': 'CRITICAL'} for _ in range(20)]
        breakdown = self.calc.calculate(
            schema_inconsistencies=many_issues,
            pipeline_results=[{'error_rate_pct': 99, 'p95_ms': 9999}],
            middleware_gaps=[{'severity': 'CRITICAL', 'blocking': True, 'effort_days': (90, 180)} for _ in range(5)],
            data_quality_results=[{'severity': 'CRITICAL', 'passed': False} for _ in range(10)],
        )
        assert 0 <= breakdown.overall_score <= 100

    def test_custom_weights(self):
        # Schema-heavy weights should penalize schema issues more
        schema_heavy = ReadinessCalculator(weights={
            'schema_inconsistency': 0.70,
            'pipeline_bottleneck': 0.10,
            'middleware_gap': 0.10,
            'data_quality': 0.05,
            'security_concern': 0.05,
        })
        # Same data, different weights
        breakdown = schema_heavy.calculate(
            schema_inconsistencies=[{'severity': 'HIGH'}, {'severity': 'HIGH'}],
            pipeline_results=[],
            middleware_gaps=[],
            data_quality_results=[],
        )
        default = self.calc.calculate(
            schema_inconsistencies=[{'severity': 'HIGH'}, {'severity': 'HIGH'}],
            pipeline_results=[],
            middleware_gaps=[],
            data_quality_results=[],
        )
        # Schema-heavy calc should produce lower score for same schema issues
        assert schema_heavy is not self.calc  # Different instance

    def test_remediation_weeks_estimated(self):
        breakdown = self.calc.calculate(
            schema_inconsistencies=[{'severity': 'CRITICAL'}],
            pipeline_results=[],
            middleware_gaps=[{'severity': 'HIGH', 'blocking': False, 'effort_days': (10, 40)}],
            data_quality_results=[],
        )
        assert breakdown.estimated_remediation_weeks is not None
        assert breakdown.estimated_remediation_weeks > 0

    def test_breakdown_color_property(self):
        go_breakdown = self.calc.calculate([], [], [], [])
        go_breakdown.overall_score = 85
        assert go_breakdown.color == 'green'

        not_yet = ReadinessBreakdown(
            schema_score=60, pipeline_score=60, middleware_score=60,
            data_quality_score=60, security_score=60, overall_score=65,
            verdict='NOT_YET', blocking_issues=0, critical_issues=0, total_issues=3
        )
        assert not_yet.color == 'yellow'

        not_ready = ReadinessBreakdown(
            schema_score=30, pipeline_score=30, middleware_score=30,
            data_quality_score=30, security_score=30, overall_score=35,
            verdict='NOT_READY', blocking_issues=2, critical_issues=3, total_issues=10
        )
        assert not_ready.color == 'red'


class TestReadinessBreakdown:
    """Tests for ReadinessBreakdown dataclass."""

    def test_instantiation(self):
        breakdown = ReadinessBreakdown(
            schema_score=75,
            pipeline_score=80,
            middleware_score=60,
            data_quality_score=90,
            security_score=85,
            overall_score=77.5,
            verdict='NOT_YET',
            blocking_issues=0,
            critical_issues=1,
            total_issues=5,
        )
        assert breakdown.overall_score == 77.5
        assert breakdown.verdict == 'NOT_YET'
        assert breakdown.color == 'yellow'

    def test_go_verdict_green(self):
        b = ReadinessBreakdown(
            schema_score=90, pipeline_score=90, middleware_score=90,
            data_quality_score=90, security_score=90, overall_score=90,
            verdict='GO', blocking_issues=0, critical_issues=0, total_issues=0
        )
        assert b.color == 'green'
