"""
Domain layer for the Preflight Integration Tester.

Exports all public domain classes so consumers can import directly from
``preflight.core.domain`` without knowing the internal module layout.
"""

from .value_objects import (
    ReadinessVerdict,
    SeverityLevel,
    EffortLevel,
    SystemType,
    ConnectorType,
    ReadinessScore,
    EffortEstimate,
    EntityField,
    ConnectionCredentials,
    SchemaFieldMapping,
)

from .events import (
    DomainEvent,
    DiagnosticRunStarted,
    ConnectionEstablished,
    ConnectionFailed,
    SchemaAnalysisCompleted,
    PipelineTestCompleted,
    MiddlewareAssessmentCompleted,
    ReadinessReportGenerated,
    DiagnosticRunCompleted,
)

from .entities import (
    SchemaInconsistency,
    PipelineBottleneck,
    MiddlewareGap,
    RemediationItem,
    ConnectionProfile,
    EntityMapping,
)

from .aggregates import (
    AnalysisResults,
    SimulationScenario,
    ReadinessReport,
    DiagnosticRun,
)

__all__ = [
    # Value objects
    "ReadinessVerdict",
    "SeverityLevel",
    "EffortLevel",
    "SystemType",
    "ConnectorType",
    "ReadinessScore",
    "EffortEstimate",
    "EntityField",
    "ConnectionCredentials",
    "SchemaFieldMapping",
    # Events
    "DomainEvent",
    "DiagnosticRunStarted",
    "ConnectionEstablished",
    "ConnectionFailed",
    "SchemaAnalysisCompleted",
    "PipelineTestCompleted",
    "MiddlewareAssessmentCompleted",
    "ReadinessReportGenerated",
    "DiagnosticRunCompleted",
    # Entities
    "SchemaInconsistency",
    "PipelineBottleneck",
    "MiddlewareGap",
    "RemediationItem",
    "ConnectionProfile",
    "EntityMapping",
    # Aggregates
    "AnalysisResults",
    "SimulationScenario",
    "ReadinessReport",
    "DiagnosticRun",
]
