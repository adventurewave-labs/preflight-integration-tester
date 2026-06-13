"""
Readiness Score Calculator

Aggregates all analysis results into a final readiness score and verdict.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

@dataclass
class ReadinessBreakdown:
    schema_score: float  # 0-100
    pipeline_score: float
    middleware_score: float
    data_quality_score: float
    security_score: float
    overall_score: float
    verdict: str  # GO, NOT_YET, NOT_READY
    blocking_issues: int
    critical_issues: int
    total_issues: int
    estimated_remediation_weeks: Optional[float] = None

    @property
    def color(self) -> str:
        if self.overall_score >= 80:
            return 'green'
        elif self.overall_score >= 50:
            return 'yellow'
        else:
            return 'red'

class ReadinessCalculator:
    """Calculates final AI deployment readiness score."""

    DEFAULT_WEIGHTS = {
        'schema_inconsistency': 0.30,
        'pipeline_bottleneck': 0.25,
        'middleware_gap': 0.20,
        'data_quality': 0.15,
        'security_concern': 0.10,
    }

    def __init__(self, weights: Optional[Dict] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS

    def calculate(
        self,
        schema_inconsistencies: List[Dict],
        pipeline_results: List[Dict],
        middleware_gaps: List[Dict],
        data_quality_results: List[Dict],
        security_issues: Optional[List[Dict]] = None,
    ) -> ReadinessBreakdown:
        """Calculate comprehensive readiness score."""

        # Schema score
        critical_schema = sum(1 for i in schema_inconsistencies if i.get('severity') == 'CRITICAL')
        high_schema = sum(1 for i in schema_inconsistencies if i.get('severity') == 'HIGH')
        schema_penalty = critical_schema * 15 + high_schema * 8 + len(schema_inconsistencies) * 2
        schema_score = max(0, 100 - schema_penalty)

        # Pipeline score
        pipeline_errors = [p for p in pipeline_results if p.get('error_rate_pct', 0) > 5]
        pipeline_latency = [p for p in pipeline_results if p.get('p95_ms', 0) > 1000]
        pipeline_penalty = len(pipeline_errors) * 20 + len(pipeline_latency) * 10
        pipeline_score = max(0, 100 - pipeline_penalty)

        # Middleware score
        blocking_gaps = [g for g in middleware_gaps if g.get('blocking', False)]
        critical_gaps = [g for g in middleware_gaps if g.get('severity') == 'CRITICAL']
        middleware_penalty = len(blocking_gaps) * 25 + len(critical_gaps) * 15 + len(middleware_gaps) * 5
        middleware_score = max(0, 100 - middleware_penalty)

        # Data quality score
        critical_dq = sum(1 for d in data_quality_results if d.get('severity') == 'CRITICAL' and not d.get('passed', True))
        dq_penalty = critical_dq * 20 + len([d for d in data_quality_results if not d.get('passed', True)]) * 5
        dq_score = max(0, 100 - dq_penalty)

        # Security score (default 80 if no issues found)
        security_issues = security_issues or []
        security_score = max(0, 100 - len(security_issues) * 10)

        # Weighted overall score
        overall = (
            schema_score * self.weights['schema_inconsistency'] +
            pipeline_score * self.weights['pipeline_bottleneck'] +
            middleware_score * self.weights['middleware_gap'] +
            dq_score * self.weights['data_quality'] +
            security_score * self.weights['security_concern']
        )

        # Verdict
        if overall >= 80:
            verdict = 'GO'
        elif overall >= 50:
            verdict = 'NOT_YET'
        else:
            verdict = 'NOT_READY'

        # Blocking override: any blocking gap → NOT_READY
        if blocking_gaps:
            verdict = 'NOT_READY' if len(blocking_gaps) >= 2 else 'NOT_YET'

        # Remediation estimate
        max_days = sum(g.get('effort_days', (0, 14))[1] for g in middleware_gaps)
        max_days += critical_schema * 7 + high_schema * 3
        remediation_weeks = max_days / 5.0 if max_days > 0 else None

        return ReadinessBreakdown(
            schema_score=schema_score,
            pipeline_score=pipeline_score,
            middleware_score=middleware_score,
            data_quality_score=dq_score,
            security_score=security_score,
            overall_score=round(overall, 1),
            verdict=verdict,
            blocking_issues=len(blocking_gaps),
            critical_issues=critical_schema + len(critical_gaps) + critical_dq,
            total_issues=len(schema_inconsistencies) + len(middleware_gaps) + len(data_quality_results),
            estimated_remediation_weeks=remediation_weeks,
        )
