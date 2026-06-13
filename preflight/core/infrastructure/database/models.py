"""
SQLAlchemy ORM models for Preflight persistence.

All models use UUID primary keys and include created_at/updated_at timestamps.
"""
import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    String, Float, Integer, Boolean, DateTime, Text, JSON,
    ForeignKey, Index, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class DiagnosticRunModel(Base):
    """Persists DiagnosticRun aggregate."""
    __tablename__ = "diagnostic_runs"
    __table_args__ = (
        Index("ix_diagnostic_runs_status", "status"),
        Index("ix_diagnostic_runs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Scenario stored as JSON
    scenario: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Summary results stored as JSON
    analysis_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    report_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    connections: Mapped[List["ConnectionProfileModel"]] = relationship(
        "ConnectionProfileModel", back_populates="run", cascade="all, delete-orphan"
    )
    schema_inconsistencies: Mapped[List["SchemaInconsistencyModel"]] = relationship(
        "SchemaInconsistencyModel", back_populates="run", cascade="all, delete-orphan"
    )
    pipeline_bottlenecks: Mapped[List["PipelineBottleneckModel"]] = relationship(
        "PipelineBottleneckModel", back_populates="run", cascade="all, delete-orphan"
    )
    middleware_gaps: Mapped[List["MiddlewareGapModel"]] = relationship(
        "MiddlewareGapModel", back_populates="run", cascade="all, delete-orphan"
    )
    remediation_items: Mapped[List["RemediationItemModel"]] = relationship(
        "RemediationItemModel", back_populates="run", cascade="all, delete-orphan"
    )


class ConnectionProfileModel(Base):
    """Persists ConnectionProfile entity."""
    __tablename__ = "connection_profiles"
    __table_args__ = (
        Index("ix_connection_profiles_run_id", "run_id"),
        Index("ix_connection_profiles_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("diagnostic_runs.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    system_type: Mapped[str] = mapped_column(String(50), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="disconnected")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    connection_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Credentials stored as JSON reference only (no plaintext secrets)
    credential_ref: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    run: Mapped[Optional["DiagnosticRunModel"]] = relationship(
        "DiagnosticRunModel", back_populates="connections"
    )


class SchemaInconsistencyModel(Base):
    """Persists SchemaInconsistency findings."""
    __tablename__ = "schema_inconsistencies"
    __table_args__ = (
        Index("ix_schema_inconsistencies_run_id", "run_id"),
        Index("ix_schema_inconsistencies_severity", "severity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("diagnostic_runs.id", ondelete="CASCADE"), nullable=False
    )
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    target_system: Mapped[str] = mapped_column(String(100), nullable=False)
    inconsistency_type: Mapped[str] = mapped_column(String(100), nullable=False)
    field_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    impact_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remediation_hint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    run: Mapped["DiagnosticRunModel"] = relationship(
        "DiagnosticRunModel", back_populates="schema_inconsistencies"
    )


class PipelineBottleneckModel(Base):
    """Persists PipelineBottleneck findings."""
    __tablename__ = "pipeline_bottlenecks"
    __table_args__ = (
        Index("ix_pipeline_bottlenecks_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("diagnostic_runs.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_name: Mapped[str] = mapped_column(String(255), nullable=False)
    system: Mapped[str] = mapped_column(String(100), nullable=False)
    bottleneck_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    p95_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p99_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_rate_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    breaking_point_qps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    run: Mapped["DiagnosticRunModel"] = relationship(
        "DiagnosticRunModel", back_populates="pipeline_bottlenecks"
    )


class MiddlewareGapModel(Base):
    """Persists MiddlewareGap findings."""
    __tablename__ = "middleware_gaps"
    __table_args__ = (
        Index("ix_middleware_gaps_run_id", "run_id"),
        Index("ix_middleware_gaps_blocking", "blocking"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("diagnostic_runs.id", ondelete="CASCADE"), nullable=False
    )
    gap_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    target_system: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    effort_min_days: Mapped[int] = mapped_column(Integer, default=1)
    effort_max_days: Mapped[int] = mapped_column(Integer, default=10)
    recommended_solution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    run: Mapped["DiagnosticRunModel"] = relationship(
        "DiagnosticRunModel", back_populates="middleware_gaps"
    )


class RemediationItemModel(Base):
    """Persists RemediationItem in the remediation plan."""
    __tablename__ = "remediation_items"
    __table_args__ = (
        Index("ix_remediation_items_run_id", "run_id"),
        Index("ix_remediation_items_priority", "priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("diagnostic_runs.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    effort_min_days: Mapped[int] = mapped_column(Integer, default=1)
    effort_max_days: Mapped[int] = mapped_column(Integer, default=10)
    recommended_sequence: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped["DiagnosticRunModel"] = relationship(
        "DiagnosticRunModel", back_populates="remediation_items"
    )


class ApiKeyModel(Base):
    """Persists API keys for authentication."""
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_api_keys_active", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # SHA-256 hash
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
