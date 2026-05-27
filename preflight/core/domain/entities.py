"""
Domain entities for the Preflight domain model.

Entities have a distinct identity (``id`` field) that persists across state
changes, unlike value objects which are identified solely by their attributes.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .value_objects import (
    ConnectorType,
    EffortEstimate,
    EffortLevel,
    EntityField,
    SchemaFieldMapping,
    SeverityLevel,
    SystemType,
)


@dataclass
class SchemaInconsistency:
    """A detected inconsistency in how a business entity is modelled across systems.

    Inconsistencies may be structural (e.g. a field present in one system but
    absent in another) or semantic (e.g. the same logical concept stored with
    different data types or cardinality).

    Attributes:
        id: Unique identifier for this finding.
        entity_name: Name of the business entity affected.
        source_system: Identifier of the originating/reference system.
        target_system: Identifier of the system being compared against.
        inconsistency_type: One of ``key_mismatch``, ``type_mismatch``,
            ``missing_field``, ``cardinality_mismatch``, or
            ``semantic_mismatch``.
        field_name: The specific field involved, if applicable.
        source_definition: How the field/entity is defined in the source system.
        target_definition: How the field/entity is defined in the target system.
        severity: Severity classification of the inconsistency.
        impact_description: Plain-English description of downstream impact.
        remediation_hint: Suggested first step towards fixing the inconsistency.
        discovered_at: UTC timestamp when this finding was recorded.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity_name: str = ""
    source_system: str = ""
    target_system: str = ""
    inconsistency_type: str = ""  # key_mismatch, type_mismatch, missing_field, cardinality_mismatch, semantic_mismatch
    field_name: Optional[str] = None
    source_definition: Optional[str] = None
    target_definition: Optional[str] = None
    severity: SeverityLevel = SeverityLevel.MEDIUM
    impact_description: str = ""
    remediation_hint: str = ""
    discovered_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PipelineBottleneck:
    """A detected bottleneck in a data pipeline under simulated load.

    Bottlenecks are identified by comparing observed performance metrics
    against configured thresholds during the simulation phase.

    Attributes:
        id: Unique identifier for this finding.
        pipeline_name: Human-readable name of the pipeline or integration.
        system: Identifier of the system where the bottleneck was observed.
        bottleneck_type: One of ``latency``, ``throughput``, ``error_rate``,
            ``timeout``, or ``connection_pool``.
        observed_value: The measured metric value.
        threshold_value: The acceptable threshold that was exceeded.
        unit: Unit of the measured metric (e.g. ``ms``, ``qps``, ``%``).
        severity: Severity classification of the bottleneck.
        description: Plain-English description of the bottleneck.
        breaking_point_qps: Query-per-second rate at which the system begins
            to fail, if determined.
        p95_latency_ms: 95th-percentile response latency in milliseconds.
        p99_latency_ms: 99th-percentile response latency in milliseconds.
        error_rate_pct: Error rate as a percentage of total requests.
        discovered_at: UTC timestamp when this finding was recorded.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pipeline_name: str = ""
    system: str = ""
    bottleneck_type: str = ""  # latency, throughput, error_rate, timeout, connection_pool
    observed_value: float = 0.0
    threshold_value: float = 0.0
    unit: str = ""  # ms, qps, %, connections
    severity: SeverityLevel = SeverityLevel.MEDIUM
    description: str = ""
    breaking_point_qps: Optional[float] = None
    p95_latency_ms: Optional[float] = None
    p99_latency_ms: Optional[float] = None
    error_rate_pct: Optional[float] = None
    discovered_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class MiddlewareGap:
    """A missing integration or middleware component required for the deployment.

    Gaps represent integration work that must be completed before the AI
    workload can run against the target systems.

    Attributes:
        id: Unique identifier for this finding.
        gap_type: One of ``missing_api``, ``missing_etl``,
            ``missing_event_bus``, ``missing_auth``, or ``missing_caching``.
        source_system: System that needs to expose or consume the middleware.
        target_system: System on the other end of the missing integration.
        description: Plain-English description of the gap.
        severity: Severity classification of the gap.
        effort_estimate: Estimated remediation effort.
        recommended_solution: Suggested tool or approach to fill the gap.
        blocking: ``True`` if deployment cannot proceed without closing this gap.
        discovered_at: UTC timestamp when this finding was recorded.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    gap_type: str = ""  # missing_api, missing_etl, missing_event_bus, missing_auth, missing_caching
    source_system: str = ""
    target_system: str = ""
    description: str = ""
    severity: SeverityLevel = SeverityLevel.MEDIUM
    effort_estimate: EffortEstimate = field(
        default_factory=lambda: EffortEstimate.from_level(EffortLevel.MEDIUM)
    )
    recommended_solution: str = ""
    blocking: bool = False  # True if deployment cannot proceed without this
    discovered_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RemediationItem:
    """A specific action in the remediation plan.

    Remediation items are produced by the report service and ordered to
    form an actionable execution plan for the customer.

    Attributes:
        id: Unique identifier for this item.
        title: Short title suitable for display in a plan overview.
        description: Detailed description of what needs to be done.
        category: Broad category: ``schema``, ``pipeline``, ``middleware``,
            ``security``, or ``data_quality``.
        priority: Urgency score from 1 (lowest) to 10 (highest).
        severity: Severity of the underlying issue.
        effort_estimate: Estimated effort to complete this item.
        related_gap_ids: IDs of :class:`MiddlewareGap`,
            :class:`PipelineBottleneck`, or :class:`SchemaInconsistency`
            objects that prompted this item.
        prerequisites: IDs of :class:`RemediationItem` objects that must be
            completed before this one can start.
        recommended_sequence: Suggested execution order within the plan.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    category: str = ""  # schema, pipeline, middleware, security, data_quality
    priority: int = 5  # 1-10, higher = more urgent
    severity: SeverityLevel = SeverityLevel.MEDIUM
    effort_estimate: EffortEstimate = field(
        default_factory=lambda: EffortEstimate.from_level(EffortLevel.MEDIUM)
    )
    related_gap_ids: List[str] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)  # IDs of items that must be done first
    recommended_sequence: int = 0  # order in execution plan


@dataclass
class ConnectionProfile:
    """Profile and live status of a connected enterprise system.

    Attributes:
        id: Unique identifier for this profile.
        name: Human-readable name for the system.
        system_type: High-level system classification.
        connector_type: Specific product connector used.
        credentials: Connection credentials (password excluded).
        status: Connection lifecycle state:
            ``disconnected`` → ``connecting`` → ``connected`` | ``failed``.
        error_message: Last error message, populated when ``status == "failed"``.
        schema_version: Version string of the target system schema, if available.
        entity_count: Number of business entities discovered in the system.
        connection_latency_ms: Round-trip latency of the connection in ms.
        connected_at: UTC timestamp of the last successful connection.
        metadata: Arbitrary connector-specific key/value metadata.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    system_type: SystemType = SystemType.DATABASE
    connector_type: ConnectorType = ConnectorType.POSTGRESQL
    credentials: Optional["ConnectionCredentials"] = None  # type: ignore[name-defined]
    status: str = "disconnected"  # disconnected, connecting, connected, failed
    error_message: Optional[str] = None
    schema_version: Optional[str] = None
    entity_count: int = 0
    connection_latency_ms: Optional[float] = None
    connected_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityMapping:
    """Maps how a business entity is represented across multiple systems.

    The canonical definition acts as the reference schema, and each system
    representation tracks how that system deviates from the canonical form.

    Attributes:
        id: Unique identifier for this mapping.
        entity_name: Name of the business entity (e.g. ``Customer``).
        canonical_definition: Map of field name → :class:`EntityField` for
            the agreed canonical schema.
        system_representations: Per-system raw schema metadata keyed by
            system identifier.
        field_mappings: Cross-system field mappings derived by analysis.
        consistency_score: Aggregate consistency score from 0.0 (fully
            inconsistent) to 1.0 (perfectly consistent).
        inconsistencies: List of detected :class:`SchemaInconsistency` objects
            for this entity.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity_name: str = ""
    canonical_definition: Dict[str, EntityField] = field(default_factory=dict)
    system_representations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    field_mappings: List[SchemaFieldMapping] = field(default_factory=list)
    consistency_score: float = 1.0  # 0-1, lower = more inconsistent
    inconsistencies: List[SchemaInconsistency] = field(default_factory=list)
