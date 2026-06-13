"""
Report generation application service.

Transforms raw :class:`AnalysisResults` into a human-readable
:class:`ReadinessReport` with an executive summary, a technical summary,
and a prioritised remediation plan.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from ...domain.aggregates import (
    AnalysisResults,
    DiagnosticRun,
    ReadinessReport,
    SimulationScenario,
)
from ...domain.entities import (
    MiddlewareGap,
    PipelineBottleneck,
    RemediationItem,
    SchemaInconsistency,
)
from ...domain.value_objects import (
    EffortEstimate,
    EffortLevel,
    ReadinessVerdict,
    SeverityLevel,
)

logger = logging.getLogger(__name__)


class ReportService:
    """Service for generating AI-readiness reports from diagnostic analysis.

    This service is stateless; all inputs are passed as method arguments.

    Usage::

        service = ReportService()
        report = service.generate_report(diagnostic_run)
    """

    def generate_report(self, diagnostic_run: DiagnosticRun) -> ReadinessReport:
        """Generate a complete :class:`ReadinessReport` for a diagnostic run.

        The run must have a completed :class:`AnalysisResults` attached.
        If ``diagnostic_run.analysis`` is ``None`` the report will be generated
        with a score of 100 and an empty remediation plan.

        Args:
            diagnostic_run: The completed (or reporting-phase) diagnostic run.

        Returns:
            A fully populated :class:`ReadinessReport`.
        """
        analysis = diagnostic_run.analysis or AnalysisResults()
        scenario = diagnostic_run.scenario

        report = ReadinessReport()
        report.generated_at = datetime.utcnow()

        # Score.
        report.calculate_score(analysis)

        # Summaries.
        report.executive_summary = self.generate_executive_summary(analysis, scenario)
        report.technical_summary = self._generate_technical_summary(analysis)

        # Remediation plan.
        report.remediation_plan = self.build_remediation_plan(analysis)
        report.total_effort_estimate = self.calculate_total_effort(
            report.remediation_plan
        )

        # Findings summary counts.
        report.findings_summary = {
            "schema_inconsistencies": len(analysis.schema_inconsistencies),
            "pipeline_bottlenecks": len(analysis.pipeline_bottlenecks),
            "middleware_gaps": len(analysis.middleware_gaps),
            "data_quality_issues": len(analysis.data_quality_issues),
            "critical_issues": analysis.critical_count,
            "blocking_gaps": len(analysis.blocking_gaps),
        }

        logger.info(
            "Report generated for run %s: score=%.1f verdict=%s items=%d",
            diagnostic_run.id,
            report.readiness_score.value if report.readiness_score else 0,
            report.verdict.value if report.verdict else "N/A",
            len(report.remediation_plan),
        )
        return report

    def generate_executive_summary(
        self,
        analysis: AnalysisResults,
        scenario: Optional[SimulationScenario],
    ) -> str:
        """Generate a non-technical executive summary.

        The summary is written for C-suite stakeholders who need to understand
        the overall AI readiness posture and the business impact of any gaps
        without diving into technical detail.

        Args:
            analysis: The completed analysis results.
            scenario: The simulation scenario (may be ``None``).

        Returns:
            A multi-sentence plain-English summary string.
        """
        verdict_phrases = {
            ReadinessVerdict.GO: (
                "systems are well-positioned for AI deployment"
            ),
            ReadinessVerdict.NOT_YET: (
                "systems require moderate remediation before AI deployment"
            ),
            ReadinessVerdict.NOT_READY: (
                "systems are not yet ready for AI deployment and require significant work"
            ),
        }

        # Build a temporary score to determine verdict for the summary text.
        temp_report = ReadinessReport()
        score = temp_report.calculate_score(analysis)
        verdict_text = verdict_phrases.get(
            score.verdict,
            "systems have been assessed for AI deployment readiness",
        )

        use_case = scenario.use_case if scenario else "the planned AI deployment"
        system_list = (
            ", ".join(scenario.target_systems) if scenario and scenario.target_systems
            else "the connected enterprise systems"
        )

        critical = analysis.critical_count
        blocking = len(analysis.blocking_gaps)

        parts = [
            f"The Preflight diagnostic assessment indicates that {system_list} {verdict_text} "
            f"with a readiness score of {score.value:.0f}/100.",
        ]

        if critical > 0:
            parts.append(
                f"The assessment identified {critical} critical issue(s) that require "
                "immediate attention prior to proceeding with any AI workload deployment."
            )

        if blocking > 0:
            parts.append(
                f"{blocking} middleware gap(s) are currently blocking and must be "
                "resolved before the AI workload can function correctly."
            )

        total_issues = (
            len(analysis.schema_inconsistencies)
            + len(analysis.pipeline_bottlenecks)
            + len(analysis.middleware_gaps)
        )
        if total_issues > 0:
            parts.append(
                f"A total of {total_issues} findings were recorded across schema consistency, "
                "pipeline performance, and middleware coverage dimensions."
            )

        parts.append(
            f"The recommended remediation plan contains {len(self.build_remediation_plan(analysis))} "
            f"action items to bring the environment to a GO state for {use_case}."
        )

        return " ".join(parts)

    def build_remediation_plan(
        self, analysis: AnalysisResults
    ) -> List[RemediationItem]:
        """Build a prioritised remediation plan from analysis findings.

        Items are derived from schema inconsistencies, pipeline bottlenecks,
        and middleware gaps.  They are sorted by priority (descending) then
        by severity weight.

        Args:
            analysis: The completed analysis results.

        Returns:
            An ordered list of :class:`RemediationItem` objects.
        """
        items: List[RemediationItem] = []

        # Schema inconsistencies → remediation items.
        for inconsistency in analysis.schema_inconsistencies:
            item = self._schema_inconsistency_to_item(inconsistency)
            items.append(item)

        # Pipeline bottlenecks → remediation items.
        for bottleneck in analysis.pipeline_bottlenecks:
            item = self._bottleneck_to_item(bottleneck)
            items.append(item)

        # Middleware gaps → remediation items.
        for gap in analysis.middleware_gaps:
            item = self._gap_to_item(gap)
            items.append(item)

        # Data quality issues → generic remediation items.
        for idx, dq_issue in enumerate(analysis.data_quality_issues):
            items.append(
                RemediationItem(
                    title=dq_issue.get("title", f"Data quality issue #{idx + 1}"),
                    description=dq_issue.get("description", ""),
                    category="data_quality",
                    priority=dq_issue.get("priority", 4),
                    severity=SeverityLevel(
                        dq_issue.get("severity", SeverityLevel.MEDIUM.value)
                    ),
                    effort_estimate=EffortEstimate.from_level(EffortLevel.LOW),
                )
            )

        # Sort: blocking/critical first, then by priority descending.
        items.sort(key=lambda x: (-x.priority, -self._severity_weight(x.severity)))

        # Assign recommended sequence.
        for seq, item in enumerate(items, start=1):
            item.recommended_sequence = seq

        return items

    def calculate_total_effort(
        self, items: List[RemediationItem]
    ) -> EffortEstimate:
        """Aggregate individual effort estimates into a total estimate.

        The aggregation is a simple sum of min/max day ranges with a slight
        parallelisation discount (assumes some items can run concurrently).

        Args:
            items: The remediation plan items.

        Returns:
            A single :class:`EffortEstimate` representing the total effort.
        """
        if not items:
            return EffortEstimate.from_level(EffortLevel.TRIVIAL)

        total_min = sum(i.effort_estimate.min_days for i in items)
        total_max = sum(i.effort_estimate.max_days for i in items)

        # Apply a 30% parallelisation discount to reflect that some items
        # can be worked on simultaneously by different team members.
        discount = 0.70
        adjusted_min = max(0, int(total_min * discount))
        adjusted_max = max(0, int(total_max * discount))

        # Derive effort level from adjusted max.
        if adjusted_max <= 1:
            level = EffortLevel.TRIVIAL
        elif adjusted_max <= 5:
            level = EffortLevel.LOW
        elif adjusted_max <= 21:
            level = EffortLevel.MEDIUM
        elif adjusted_max <= 90:
            level = EffortLevel.HIGH
        else:
            level = EffortLevel.CRITICAL

        # Average confidence across all items.
        avg_confidence = sum(i.effort_estimate.confidence for i in items) / len(items)

        return EffortEstimate(
            min_days=adjusted_min,
            max_days=adjusted_max,
            level=level,
            confidence=round(avg_confidence, 2),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate_technical_summary(self, analysis: AnalysisResults) -> str:
        """Generate a structured technical summary for engineering teams."""
        lines = ["=== Technical Readiness Summary ===", ""]

        lines.append(f"Schema Inconsistencies : {len(analysis.schema_inconsistencies)}")
        for sev in (
            SeverityLevel.CRITICAL,
            SeverityLevel.HIGH,
            SeverityLevel.MEDIUM,
            SeverityLevel.LOW,
        ):
            count = sum(
                1 for i in analysis.schema_inconsistencies if i.severity == sev
            )
            if count:
                lines.append(f"  {sev.value:8s}: {count}")

        lines.append(f"Pipeline Bottlenecks   : {len(analysis.pipeline_bottlenecks)}")
        for sev in (
            SeverityLevel.CRITICAL,
            SeverityLevel.HIGH,
            SeverityLevel.MEDIUM,
        ):
            count = sum(
                1 for b in analysis.pipeline_bottlenecks if b.severity == sev
            )
            if count:
                lines.append(f"  {sev.value:8s}: {count}")

        lines.append(f"Middleware Gaps        : {len(analysis.middleware_gaps)}")
        blocking = len(analysis.blocking_gaps)
        if blocking:
            lines.append(f"  Blocking            : {blocking}")

        lines.append(f"Data Quality Issues    : {len(analysis.data_quality_issues)}")

        return "\n".join(lines)

    def _severity_weight(self, severity: SeverityLevel) -> int:
        """Return a numeric weight for sorting by severity."""
        weights = {
            SeverityLevel.CRITICAL: 5,
            SeverityLevel.HIGH: 4,
            SeverityLevel.MEDIUM: 3,
            SeverityLevel.LOW: 2,
            SeverityLevel.INFO: 1,
        }
        return weights.get(severity, 3)

    def _schema_inconsistency_to_item(
        self, inconsistency: SchemaInconsistency
    ) -> RemediationItem:
        """Convert a :class:`SchemaInconsistency` into a :class:`RemediationItem`."""
        priority_map = {
            SeverityLevel.CRITICAL: 10,
            SeverityLevel.HIGH: 8,
            SeverityLevel.MEDIUM: 5,
            SeverityLevel.LOW: 3,
            SeverityLevel.INFO: 1,
        }
        effort_map = {
            SeverityLevel.CRITICAL: EffortLevel.HIGH,
            SeverityLevel.HIGH: EffortLevel.MEDIUM,
            SeverityLevel.MEDIUM: EffortLevel.LOW,
            SeverityLevel.LOW: EffortLevel.TRIVIAL,
            SeverityLevel.INFO: EffortLevel.TRIVIAL,
        }
        return RemediationItem(
            title=(
                f"Resolve {inconsistency.inconsistency_type.replace('_', ' ')} "
                f"for '{inconsistency.entity_name}'"
                + (f".{inconsistency.field_name}" if inconsistency.field_name else "")
            ),
            description=inconsistency.remediation_hint or inconsistency.impact_description,
            category="schema",
            priority=priority_map.get(inconsistency.severity, 5),
            severity=inconsistency.severity,
            effort_estimate=EffortEstimate.from_level(
                effort_map.get(inconsistency.severity, EffortLevel.MEDIUM)
            ),
            related_gap_ids=[inconsistency.id],
        )

    def _bottleneck_to_item(
        self, bottleneck: PipelineBottleneck
    ) -> RemediationItem:
        """Convert a :class:`PipelineBottleneck` into a :class:`RemediationItem`."""
        priority_map = {
            SeverityLevel.CRITICAL: 9,
            SeverityLevel.HIGH: 7,
            SeverityLevel.MEDIUM: 5,
            SeverityLevel.LOW: 2,
        }
        return RemediationItem(
            title=(
                f"Resolve {bottleneck.bottleneck_type.replace('_', ' ')} bottleneck "
                f"in '{bottleneck.pipeline_name}' on {bottleneck.system}"
            ),
            description=(
                f"{bottleneck.description} "
                f"(observed {bottleneck.observed_value}{bottleneck.unit}, "
                f"threshold {bottleneck.threshold_value}{bottleneck.unit})"
            ),
            category="pipeline",
            priority=priority_map.get(bottleneck.severity, 5),
            severity=bottleneck.severity,
            effort_estimate=EffortEstimate.from_level(EffortLevel.MEDIUM),
            related_gap_ids=[bottleneck.id],
        )

    def _gap_to_item(self, gap: MiddlewareGap) -> RemediationItem:
        """Convert a :class:`MiddlewareGap` into a :class:`RemediationItem`."""
        priority_map = {
            SeverityLevel.CRITICAL: 10,
            SeverityLevel.HIGH: 8,
            SeverityLevel.MEDIUM: 6,
            SeverityLevel.LOW: 3,
        }
        # Blocking gaps get +1 to priority.
        base_priority = priority_map.get(gap.severity, 6)
        priority = min(10, base_priority + (1 if gap.blocking else 0))

        return RemediationItem(
            title=(
                f"Implement {gap.gap_type.replace('_', ' ')} between "
                f"{gap.source_system} and {gap.target_system}"
            ),
            description=(
                f"{gap.description} "
                f"Recommended solution: {gap.recommended_solution}"
            ),
            category="middleware",
            priority=priority,
            severity=gap.severity,
            effort_estimate=gap.effort_estimate,
            related_gap_ids=[gap.id],
        )
