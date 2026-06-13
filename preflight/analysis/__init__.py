"""Analysis engines for the Preflight Integration Tester."""

from preflight.analysis.schema_analyzer import (
    SchemaAnalyzer,
    EntityComparisonResult,
    FieldComparison,
)
from preflight.analysis.pipeline_tester import (
    PipelineTester,
    PipelineTestResult,
    LoadTestConfig,
    RequestMetrics,
)
from preflight.analysis.middleware_analyzer import (
    MiddlewareAnalyzer,
    IntegrationPattern,
    INTEGRATION_PATTERNS,
)
from preflight.analysis.data_quality import (
    DataQualityAnalyzer,
    DataQualityResult,
    DataQualityCheck,
)
from preflight.analysis.readiness_calculator import (
    ReadinessCalculator,
    ReadinessBreakdown,
)

__all__ = [
    # Schema
    "SchemaAnalyzer",
    "EntityComparisonResult",
    "FieldComparison",
    # Pipeline
    "PipelineTester",
    "PipelineTestResult",
    "LoadTestConfig",
    "RequestMetrics",
    # Middleware
    "MiddlewareAnalyzer",
    "IntegrationPattern",
    "INTEGRATION_PATTERNS",
    # Data Quality
    "DataQualityAnalyzer",
    "DataQualityResult",
    "DataQualityCheck",
    # Readiness
    "ReadinessCalculator",
    "ReadinessBreakdown",
]
