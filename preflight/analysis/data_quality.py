"""
Data Quality Analyzer

Checks data quality issues that would break AI agents.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

@dataclass
class DataQualityCheck:
    check_name: str
    description: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW

@dataclass
class DataQualityResult:
    check_name: str
    system: str
    table: str
    column: Optional[str]
    severity: str
    passed: bool
    score: float  # 0-1
    details: str
    sample_issues: List[str] = field(default_factory=list)

class DataQualityAnalyzer:
    """Analyzes data quality issues in connected systems."""

    QUALITY_CHECKS = [
        DataQualityCheck('completeness', 'Check for NULL values in key fields', 'HIGH'),
        DataQualityCheck('uniqueness', 'Check for duplicate primary keys', 'CRITICAL'),
        DataQualityCheck('referential_integrity', 'Check foreign key consistency', 'HIGH'),
        DataQualityCheck('format_consistency', 'Check for consistent data formats', 'MEDIUM'),
        DataQualityCheck('null_rate', 'Check null rates in important fields', 'MEDIUM'),
    ]

    def analyze_sample(self, system_name: str, table_name: str, sample_data: List[Dict]) -> List[DataQualityResult]:
        """Analyze a sample of data for quality issues."""
        if not sample_data:
            return []

        results = []
        columns = list(sample_data[0].keys()) if sample_data else []

        # Completeness check
        for col in columns:
            null_count = sum(1 for row in sample_data if row.get(col) is None or row.get(col) == '')
            null_rate = null_count / len(sample_data)

            if 'id' in col.lower() or 'key' in col.lower():
                severity = 'CRITICAL' if null_rate > 0 else 'INFO'
            elif null_rate > 0.3:
                severity = 'HIGH'
            elif null_rate > 0.1:
                severity = 'MEDIUM'
            else:
                severity = 'INFO'

            if null_rate > 0.05:
                results.append(DataQualityResult(
                    check_name='completeness',
                    system=system_name,
                    table=table_name,
                    column=col,
                    severity=severity,
                    passed=null_rate == 0,
                    score=1.0 - null_rate,
                    details=f"{null_rate:.1%} null rate in {col}",
                ))

        # Uniqueness check for ID fields
        id_fields = [c for c in columns if 'id' in c.lower() and 'foreign' not in c.lower()]
        for id_field in id_fields[:3]:
            values = [row.get(id_field) for row in sample_data if row.get(id_field)]
            duplicates = len(values) - len(set(str(v) for v in values))
            if duplicates > 0:
                results.append(DataQualityResult(
                    check_name='uniqueness',
                    system=system_name,
                    table=table_name,
                    column=id_field,
                    severity='CRITICAL',
                    passed=False,
                    score=1.0 - (duplicates / max(len(values), 1)),
                    details=f"{duplicates} duplicate values in {id_field}",
                ))

        return results

    def calculate_overall_quality_score(self, results: List[DataQualityResult]) -> float:
        """Calculate an overall data quality score (0-1)."""
        if not results:
            return 1.0

        severity_weights = {'CRITICAL': 1.0, 'HIGH': 0.7, 'MEDIUM': 0.4, 'LOW': 0.1, 'INFO': 0.0}
        failed = [r for r in results if not r.passed]

        if not failed:
            return 1.0

        total_penalty = sum(severity_weights.get(r.severity, 0.5) for r in failed)
        max_possible_penalty = len(results) * 1.0
        return max(0.0, 1.0 - (total_penalty / max(max_possible_penalty, 1.0)))
