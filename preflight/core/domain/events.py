"""
Domain events emitted by aggregates in the Preflight domain model.

Events are immutable records of something that happened.  They are produced by
aggregates (primarily :class:`~preflight.core.domain.aggregates.DiagnosticRun`)
and consumed by application services or external subscribers.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import uuid


@dataclass
class DomainEvent:
    """Base class for all domain events.

    Attributes:
        event_id: Unique identifier for this event instance.
        occurred_at: UTC timestamp of when the event was raised.
        event_type: Discriminator string set by each concrete subclass.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=datetime.utcnow)
    event_type: str = field(init=False, default="DomainEvent")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the event to a plain dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass
class DiagnosticRunStarted(DomainEvent):
    """Raised when a :class:`DiagnosticRun` transitions to the *running* state.

    Attributes:
        run_id: ID of the diagnostic run that started.
    """

    run_id: str = ""
    event_type: str = field(default="DiagnosticRunStarted", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["run_id"] = self.run_id
        return base


@dataclass
class ConnectionEstablished(DomainEvent):
    """Raised when a connection to an enterprise system is successfully opened.

    Attributes:
        run_id: ID of the owning diagnostic run.
        connection_id: ID of the :class:`ConnectionProfile`.
        system_name: Human-readable name of the connected system.
        connector_type: Connector type string (e.g. ``SALESFORCE``).
    """

    run_id: str = ""
    connection_id: str = ""
    system_name: str = ""
    connector_type: str = ""
    event_type: str = field(default="ConnectionEstablished", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "run_id": self.run_id,
            "connection_id": self.connection_id,
            "system_name": self.system_name,
            "connector_type": self.connector_type,
        })
        return base


@dataclass
class ConnectionFailed(DomainEvent):
    """Raised when a connection attempt to an enterprise system fails.

    Attributes:
        run_id: ID of the owning diagnostic run.
        connection_id: ID of the :class:`ConnectionProfile`.
        system_name: Human-readable name of the system that failed to connect.
        error_message: Human-readable description of the failure.
    """

    run_id: str = ""
    connection_id: str = ""
    system_name: str = ""
    error_message: str = ""
    event_type: str = field(default="ConnectionFailed", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "run_id": self.run_id,
            "connection_id": self.connection_id,
            "system_name": self.system_name,
            "error_message": self.error_message,
        })
        return base


@dataclass
class SchemaAnalysisCompleted(DomainEvent):
    """Raised when schema consistency analysis finishes for a diagnostic run.

    Attributes:
        run_id: ID of the owning diagnostic run.
        inconsistency_count: Total number of inconsistencies detected.
        critical_count: Number of CRITICAL-severity inconsistencies.
        entity_count: Number of business entities that were analysed.
    """

    run_id: str = ""
    inconsistency_count: int = 0
    critical_count: int = 0
    entity_count: int = 0
    event_type: str = field(default="SchemaAnalysisCompleted", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "run_id": self.run_id,
            "inconsistency_count": self.inconsistency_count,
            "critical_count": self.critical_count,
            "entity_count": self.entity_count,
        })
        return base


@dataclass
class PipelineTestCompleted(DomainEvent):
    """Raised when the pipeline load-simulation phase finishes.

    Attributes:
        run_id: ID of the owning diagnostic run.
        bottleneck_count: Number of bottlenecks detected.
        systems_tested: Number of systems included in the simulation.
    """

    run_id: str = ""
    bottleneck_count: int = 0
    systems_tested: int = 0
    event_type: str = field(default="PipelineTestCompleted", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "run_id": self.run_id,
            "bottleneck_count": self.bottleneck_count,
            "systems_tested": self.systems_tested,
        })
        return base


@dataclass
class MiddlewareAssessmentCompleted(DomainEvent):
    """Raised when middleware gap assessment finishes.

    Attributes:
        run_id: ID of the owning diagnostic run.
        gap_count: Total number of middleware gaps identified.
        blocking_count: Number of gaps that block deployment.
    """

    run_id: str = ""
    gap_count: int = 0
    blocking_count: int = 0
    event_type: str = field(default="MiddlewareAssessmentCompleted", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "run_id": self.run_id,
            "gap_count": self.gap_count,
            "blocking_count": self.blocking_count,
        })
        return base


@dataclass
class ReadinessReportGenerated(DomainEvent):
    """Raised when the :class:`ReadinessReport` is produced.

    Attributes:
        run_id: ID of the owning diagnostic run.
        score: Numeric readiness score (0–100).
        verdict: Readiness verdict string (e.g. ``GO``).
        remediation_count: Number of items in the remediation plan.
    """

    run_id: str = ""
    score: float = 0.0
    verdict: str = ""
    remediation_count: int = 0
    event_type: str = field(default="ReadinessReportGenerated", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "run_id": self.run_id,
            "score": self.score,
            "verdict": self.verdict,
            "remediation_count": self.remediation_count,
        })
        return base


@dataclass
class DiagnosticRunCompleted(DomainEvent):
    """Raised when the entire diagnostic run reaches the *completed* state.

    Attributes:
        run_id: ID of the diagnostic run.
        verdict: Final readiness verdict string.
        score: Final numeric readiness score (0–100).
    """

    run_id: str = ""
    verdict: str = ""
    score: float = 0.0
    event_type: str = field(default="DiagnosticRunCompleted", init=False)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "run_id": self.run_id,
            "verdict": self.verdict,
            "score": self.score,
        })
        return base
