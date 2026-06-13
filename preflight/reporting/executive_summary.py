"""
Executive Summary Generator

Creates clear, non-technical summaries for C-suite buyers.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime

class ExecutiveSummaryGenerator:
    """Generates clear executive summaries from analysis results."""

    VERDICT_INTROS = {
        'GO': "Your enterprise infrastructure appears well-positioned for the proposed AI deployment.",
        'NOT_YET': "Your enterprise infrastructure needs targeted improvements before AI deployment can succeed.",
        'NOT_READY': "Significant infrastructure gaps were identified that would prevent a successful AI deployment.",
    }

    def generate(
        self,
        verdict: str,
        score: float,
        schema_issues: List[Dict],
        pipeline_issues: List[Dict],
        middleware_gaps: List[Dict],
        scenario: Optional[Dict] = None,
        remediation_weeks: Optional[float] = None,
    ) -> str:
        """Generate a professional executive summary."""

        intro = self.VERDICT_INTROS.get(verdict, "Analysis complete.")
        critical_count = sum(1 for i in schema_issues + middleware_gaps if i.get('severity') == 'CRITICAL')
        blocking_count = sum(1 for g in middleware_gaps if g.get('blocking', False))

        lines = [
            f"**Readiness Assessment: {score:.0f}% — {verdict.replace('_', ' ')}**",
            "",
            intro,
            "",
        ]

        if schema_issues:
            critical_schema = sum(1 for i in schema_issues if i.get('severity') == 'CRITICAL')
            lines.append(
                f"**Schema Consistency:** {len(schema_issues)} inconsistencies found across connected systems"
                + (f", including {critical_schema} critical mismatches that would cause AI agent failures" if critical_schema else "")
                + "."
            )

        if pipeline_issues:
            lines.append(
                f"**Pipeline Performance:** {len(pipeline_issues)} performance bottleneck(s) detected under simulated AI agent load."
            )

        if middleware_gaps:
            lines.append(
                f"**Integration Gaps:** {len(middleware_gaps)} missing middleware component(s) identified"
                + (f", with {blocking_count} blocking deployment" if blocking_count else "")
                + "."
            )

        if remediation_weeks:
            lines.append(
                f"\n**Estimated Remediation Timeline:** {remediation_weeks:.0f}–{remediation_weeks*1.5:.0f} weeks to reach deployment readiness."
            )

        lines.extend([
            "",
            "This assessment was conducted using read-only access to your systems. No data was modified.",
            f"Assessment date: {datetime.now().strftime('%B %d, %Y')}",
        ])

        return "\n".join(lines)
