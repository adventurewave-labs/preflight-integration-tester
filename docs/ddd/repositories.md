# Repository Interfaces — Preflight Integration Tester

This document defines all repository interfaces used in Preflight's domain layer. Repository interfaces are declared in the domain layer and implemented in the infrastructure layer (Dependency Inversion Principle). The domain layer depends on these abstract interfaces; the infrastructure layer provides concrete SQLAlchemy or Redis implementations.

---

## Repository Design Principles

1. **Interface in domain, implementation in infrastructure**: All `ABC` interfaces live in `preflight/core/domain/repositories/`; concrete implementations live in `preflight/core/infrastructure/repositories/`.
2. **Domain model in, domain model out**: Repositories accept and return domain objects (aggregates, entities), never ORM models or raw database rows.
3. **Async-first**: All methods are `async`; they are invoked from Celery tasks and FastAPI route handlers within asyncio event loops.
4. **Explicit unit of work**: Repositories do not manage transactions directly; transaction boundaries are managed by the `UnitOfWork` pattern in application services.
5. **No filtering by domain logic**: Business rules are in the domain; repositories provide only persistence-layer filtering (by ID, by run_id, by status).
6. **Pagination for collection methods**: Any method that can return multiple objects accepts `limit` and `offset` parameters.

---

## Core Repository Interfaces

### DiagnosticRunRepository

Manages persistence of the `DiagnosticRun` aggregate.

```python
# preflight/core/domain/repositories/diagnostic_run_repository.py

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from preflight.core.domain.aggregates import DiagnosticRun
from preflight.core.domain.shared import DiagnosticRunStatus


class DiagnosticRunRepository(ABC):

    @abstractmethod
    async def save(self, run: DiagnosticRun) -> None:
        """
        Persist a DiagnosticRun. Creates a new record if the run is new;
        updates the existing record if already persisted.
        
        Raises: DuplicateRunError if a run with the same ID already exists (on create).
        """

    @abstractmethod
    async def get_by_id(self, run_id: UUID) -> Optional[DiagnosticRun]:
        """
        Retrieve a DiagnosticRun by its identifier.
        Returns None if not found.
        Loads the full aggregate including ConnectionSet and SimulationScenario.
        Note: AnalysisResults and ReadinessReport are loaded lazily via their own repositories.
        """

    @abstractmethod
    async def get_by_id_required(self, run_id: UUID) -> DiagnosticRun:
        """
        Retrieve a DiagnosticRun by its identifier.
        Raises: DiagnosticRunNotFoundError if not found.
        """

    @abstractmethod
    async def list_by_status(
        self,
        status: DiagnosticRunStatus,
        limit: int = 20,
        offset: int = 0,
    ) -> list[DiagnosticRun]:
        """
        Retrieve DiagnosticRuns with a specific status, ordered by created_at descending.
        """

    @abstractmethod
    async def list_by_customer(
        self,
        customer_name: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[DiagnosticRun]:
        """
        Retrieve DiagnosticRuns for a specific customer, ordered by created_at descending.
        """

    @abstractmethod
    async def list_recent(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[DiagnosticRun]:
        """
        Retrieve the most recent DiagnosticRuns across all customers, ordered by created_at descending.
        """

    @abstractmethod
    async def update_status(
        self,
        run_id: UUID,
        new_status: DiagnosticRunStatus,
        completed_at: Optional[datetime] = None,
    ) -> None:
        """
        Update only the status (and optionally completed_at) of a DiagnosticRun.
        Raises: DiagnosticRunNotFoundError if not found.
        Raises: InvalidStatusTransitionError if the transition is not valid.
        """

    @abstractmethod
    async def count_by_status(self, status: DiagnosticRunStatus) -> int:
        """Return the total count of DiagnosticRuns with the given status."""

    @abstractmethod
    async def delete(self, run_id: UUID) -> None:
        """
        Hard-delete a DiagnosticRun and all associated records.
        This is an administrative operation; it cascades to all child records.
        Raises: DiagnosticRunNotFoundError if not found.
        """
```

**Concrete implementation**: `preflight/core/infrastructure/repositories/sqlalchemy_diagnostic_run_repository.py`  
**Table**: `diagnostic_runs`

---

### ConnectionProfileRepository

Manages persistence of `ConnectionProfile` aggregates.

```python
# preflight/core/domain/repositories/connection_profile_repository.py

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from preflight.core.domain.aggregates import ConnectionProfile
from preflight.core.domain.shared import ConnectionStatus, SystemType


class ConnectionProfileRepository(ABC):

    @abstractmethod
    async def save(self, profile: ConnectionProfile) -> None:
        """
        Persist a ConnectionProfile. Creates or updates.
        Note: ConnectionCredentials are NOT persisted; only the credential_reference_id.
        """

    @abstractmethod
    async def get_by_id(self, connection_id: UUID) -> Optional[ConnectionProfile]:
        """Retrieve a ConnectionProfile by identifier. Returns None if not found."""

    @abstractmethod
    async def get_by_id_required(self, connection_id: UUID) -> ConnectionProfile:
        """Retrieve a ConnectionProfile. Raises: ConnectionProfileNotFoundError if not found."""

    @abstractmethod
    async def list_by_run_id(self, run_id: UUID) -> list[ConnectionProfile]:
        """
        Retrieve all ConnectionProfiles for a DiagnosticRun.
        Ordered by connected_at ascending (connection order).
        """

    @abstractmethod
    async def list_by_run_and_status(
        self,
        run_id: UUID,
        status: ConnectionStatus,
    ) -> list[ConnectionProfile]:
        """Retrieve all ConnectionProfiles for a run with a specific status."""

    @abstractmethod
    async def get_by_run_and_system(
        self,
        run_id: UUID,
        system_type: SystemType,
    ) -> Optional[ConnectionProfile]:
        """
        Retrieve the ConnectionProfile for a specific system in a specific run.
        Returns None if not found.
        """

    @abstractmethod
    async def update_status(
        self,
        connection_id: UUID,
        new_status: ConnectionStatus,
        error_detail: Optional[str] = None,
    ) -> None:
        """
        Update the status of a ConnectionProfile.
        Raises: ConnectionProfileNotFoundError if not found.
        """

    @abstractmethod
    async def update_schema_snapshot_ref(
        self,
        connection_id: UUID,
        snapshot_ref: str,
    ) -> None:
        """
        Store the Redis cache key for a SchemaSnapshot.
        Called after successful schema introspection.
        """
```

**Concrete implementation**: `preflight/core/infrastructure/repositories/sqlalchemy_connection_profile_repository.py`  
**Table**: `connection_profiles`

---

### AnalysisResultRepository

Manages persistence of `AnalysisResult` aggregates and their contained entities.

```python
# preflight/core/domain/repositories/analysis_result_repository.py

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from preflight.core.domain.aggregates import AnalysisResult
from preflight.core.domain.entities import (
    SchemaInconsistency, PipelineBottleneck, MiddlewareGap
)
from preflight.core.domain.shared import SeverityLevel


class AnalysisResultRepository(ABC):

    @abstractmethod
    async def save(self, result: AnalysisResult) -> None:
        """
        Persist an AnalysisResult and all contained findings.
        Finding entities are inserted with ON CONFLICT DO UPDATE (upsert).
        """

    @abstractmethod
    async def get_by_id(self, analysis_id: UUID) -> Optional[AnalysisResult]:
        """Retrieve an AnalysisResult with all contained findings. Returns None if not found."""

    @abstractmethod
    async def get_by_run_id(self, run_id: UUID) -> Optional[AnalysisResult]:
        """
        Retrieve the AnalysisResult for a DiagnosticRun.
        There is at most one AnalysisResult per run.
        Returns None if the analysis phase has not completed.
        """

    @abstractmethod
    async def get_by_run_id_required(self, run_id: UUID) -> AnalysisResult:
        """Retrieve AnalysisResult. Raises: AnalysisResultNotFoundError if not found."""

    @abstractmethod
    async def save_schema_inconsistency(
        self,
        analysis_id: UUID,
        inconsistency: SchemaInconsistency,
    ) -> None:
        """
        Persist a single SchemaInconsistency as it is detected (streaming write
        during analysis, before the full AnalysisResult is complete).
        """

    @abstractmethod
    async def save_pipeline_bottleneck(
        self,
        analysis_id: UUID,
        bottleneck: PipelineBottleneck,
    ) -> None:
        """Persist a single PipelineBottleneck as it is identified."""

    @abstractmethod
    async def save_middleware_gap(
        self,
        analysis_id: UUID,
        gap: MiddlewareGap,
    ) -> None:
        """Persist a single MiddlewareGap as it is identified."""

    @abstractmethod
    async def list_schema_inconsistencies(
        self,
        run_id: UUID,
        min_severity: Optional[SeverityLevel] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SchemaInconsistency]:
        """
        List SchemaInconsistencies for a run, filtered by minimum severity.
        Ordered by impact_score descending.
        """

    @abstractmethod
    async def list_pipeline_bottlenecks(
        self,
        run_id: UUID,
        min_severity: Optional[SeverityLevel] = None,
    ) -> list[PipelineBottleneck]:
        """
        List PipelineBottlenecks for a run, filtered by minimum severity.
        Ordered by breaking_p95_latency_ms descending.
        """

    @abstractmethod
    async def list_middleware_gaps(
        self,
        run_id: UUID,
        min_severity: Optional[SeverityLevel] = None,
    ) -> list[MiddlewareGap]:
        """
        List MiddlewareGaps for a run, filtered by minimum severity.
        Ordered by severity descending, then effort_estimate ascending.
        """

    @abstractmethod
    async def update_sub_scores(
        self,
        analysis_id: UUID,
        schema_consistency_score: Optional[int] = None,
        pipeline_readiness_score: Optional[int] = None,
        middleware_readiness_score: Optional[int] = None,
    ) -> None:
        """
        Update one or more sub-scores as they become available.
        Only the explicitly provided sub-scores are updated; others remain unchanged.
        """
```

**Concrete implementation**: `preflight/core/infrastructure/repositories/sqlalchemy_analysis_result_repository.py`  
**Tables**: `analysis_results`, `schema_inconsistencies`, `pipeline_bottlenecks`, `middleware_gaps`

---

### ReadinessReportRepository

Manages persistence of `ReadinessReport` entities and their remediation items.

```python
# preflight/core/domain/repositories/readiness_report_repository.py

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from preflight.core.domain.entities import ReadinessReport, RemediationItem
from preflight.core.domain.shared import ReadinessVerdict


class ReadinessReportRepository(ABC):

    @abstractmethod
    async def save(self, report: ReadinessReport) -> None:
        """
        Persist a ReadinessReport with all ReportSections and RemediationItems.
        Report content (HTML, PDF paths, JSON) is also recorded.
        """

    @abstractmethod
    async def get_by_id(self, report_id: UUID) -> Optional[ReadinessReport]:
        """Retrieve a ReadinessReport with all sections and remediation items."""

    @abstractmethod
    async def get_by_run_id(self, run_id: UUID) -> Optional[ReadinessReport]:
        """
        Retrieve the ReadinessReport for a DiagnosticRun.
        There is at most one ReadinessReport per run.
        Returns None if report has not been generated.
        """

    @abstractmethod
    async def get_by_run_id_required(self, run_id: UUID) -> ReadinessReport:
        """Retrieve ReadinessReport. Raises: ReadinessReportNotFoundError if not found."""

    @abstractmethod
    async def list_by_verdict(
        self,
        verdict: ReadinessVerdict,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ReadinessReport]:
        """
        List ReadinessReports with a specific verdict.
        Ordered by generated_at descending.
        """

    @abstractmethod
    async def list_remediation_items(
        self,
        report_id: UUID,
        min_severity: Optional[SeverityLevel] = None,
    ) -> list[RemediationItem]:
        """
        List RemediationItems for a report, ordered by sequence_order ascending.
        Optionally filtered by minimum severity.
        """

    @abstractmethod
    async def update_report_files(
        self,
        report_id: UUID,
        html_path: Optional[str] = None,
        pdf_path: Optional[str] = None,
    ) -> None:
        """
        Update the file paths for generated report artifacts.
        Called after rendering completes.
        """

    @abstractmethod
    async def exists_for_run(self, run_id: UUID) -> bool:
        """Return True if a ReadinessReport has been generated for the given run."""
```

**Concrete implementation**: `preflight/core/infrastructure/repositories/sqlalchemy_readiness_report_repository.py`  
**Tables**: `readiness_reports`, `report_sections`, `remediation_items`

---

## Cache Repository Interfaces

### SchemaSnapshotCacheRepository

Manages Redis-backed caching of `SchemaSnapshot` value objects.

```python
# preflight/core/domain/repositories/schema_snapshot_cache_repository.py

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from preflight.core.domain.value_objects import SchemaSnapshot


class SchemaSnapshotCacheRepository(ABC):

    @abstractmethod
    async def store(
        self,
        connection_id: UUID,
        snapshot: SchemaSnapshot,
        ttl_seconds: int = 86400,  # 24 hours default
    ) -> str:
        """
        Store a SchemaSnapshot in the cache.
        Returns the cache key (for storage in ConnectionProfile.schema_snapshot_ref).
        TTL is configurable; set to 0 for no expiry (not recommended for production).
        """

    @abstractmethod
    async def retrieve(self, cache_key: str) -> Optional[SchemaSnapshot]:
        """
        Retrieve a SchemaSnapshot by cache key.
        Returns None if the key has expired or does not exist.
        """

    @abstractmethod
    async def retrieve_by_connection_id(
        self, connection_id: UUID
    ) -> Optional[SchemaSnapshot]:
        """
        Retrieve the most recent SchemaSnapshot for a ConnectionProfile.
        Returns None if not cached.
        """

    @abstractmethod
    async def invalidate(self, cache_key: str) -> None:
        """
        Remove a SchemaSnapshot from the cache.
        Called if a re-connection reveals the cached schema is stale.
        """

    @abstractmethod
    async def exists(self, cache_key: str) -> bool:
        """Return True if a cache entry exists and has not expired."""
```

**Concrete implementation**: `preflight/core/infrastructure/repositories/redis_schema_snapshot_cache_repository.py`  
**Storage**: Redis; keys follow `schema:{connection_id}:{snapshot_hash}`

---

### DiagnosticRunProgressRepository

Manages real-time job progress tracking in Redis.

```python
# preflight/core/domain/repositories/diagnostic_run_progress_repository.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from uuid import UUID


@dataclass(frozen=True)
class RunProgress:
    run_id: str
    phase: str          # DiagnosticRunStatus or sub-phase name
    pct_complete: Optional[int]  # 0–100; None if indeterminate
    detail: str         # Human-readable current activity
    updated_at: datetime


class DiagnosticRunProgressRepository(ABC):

    @abstractmethod
    async def update_progress(self, progress: RunProgress) -> None:
        """
        Write the current progress state for a DiagnosticRun.
        Also publishes to the Redis Pub/Sub channel for WebSocket streaming.
        TTL: 4 hours (auto-expires after run completes).
        """

    @abstractmethod
    async def get_progress(self, run_id: UUID) -> Optional[RunProgress]:
        """
        Retrieve the current progress for a DiagnosticRun.
        Returns None if the run has no progress record (not started, or expired).
        """

    @abstractmethod
    async def subscribe_to_progress(
        self, run_id: UUID
    ) -> AsyncIterator[RunProgress]:
        """
        Subscribe to real-time progress updates for a DiagnosticRun.
        Yields RunProgress objects as they are published.
        Stops when the run completes or fails.
        Used by the WebSocket endpoint.
        """

    @abstractmethod
    async def clear_progress(self, run_id: UUID) -> None:
        """Remove the progress record for a completed or failed run."""
```

**Concrete implementation**: `preflight/core/infrastructure/repositories/redis_diagnostic_run_progress_repository.py`  
**Storage**: Redis; keys follow `job:{run_id}:progress`; channel: `run:{run_id}:events`

---

## Audit Log Repository

### AuditLogRepository

Manages the append-only audit trail of all connector operations.

```python
# preflight/core/domain/repositories/audit_log_repository.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AuditLogEntry:
    run_id: UUID
    connection_id: UUID
    system_type: str
    operation: str        # 'introspect_schema' | 'execute_read_query' | 'stream_records'
    entity_name: str
    query_hash: str       # SHA-256 of the query; never the query itself
    duration_ms: int
    row_count: int
    timestamp: datetime
    agent_id: str


class AuditLogRepository(ABC):

    @abstractmethod
    async def append(self, entry: AuditLogEntry) -> None:
        """
        Append an audit log entry. This operation is INSERT-only; no UPDATE or DELETE.
        Must complete in < 5ms to avoid impacting connector throughput.
        """

    @abstractmethod
    async def list_by_run(
        self,
        run_id: UUID,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """
        Retrieve audit log entries for a DiagnosticRun, ordered by timestamp ascending.
        """

    @abstractmethod
    async def export_for_run(
        self,
        run_id: UUID,
        format: Literal["jsonl", "csv"],
    ) -> bytes:
        """
        Export the complete audit log for a run as JSONL or CSV.
        Used for customer compliance reporting and SIEM export.
        """

    @abstractmethod
    async def count_operations_by_system(self, run_id: UUID) -> dict[str, int]:
        """
        Return a summary of operation counts per system for a run.
        Used in the ReadinessReport audit summary section.
        """
```

**Concrete implementation**: `preflight/core/infrastructure/repositories/sqlalchemy_audit_log_repository.py`  
**Table**: `audit_log` (append-only; no UPDATE/DELETE permissions granted)

---

## Unit of Work Pattern

Application services use the `UnitOfWork` pattern to manage transaction boundaries across multiple repositories.

```python
# preflight/core/domain/unit_of_work.py

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager

from preflight.core.domain.repositories import (
    DiagnosticRunRepository,
    ConnectionProfileRepository,
    AnalysisResultRepository,
    ReadinessReportRepository,
    AuditLogRepository,
)


class UnitOfWork(ABC):
    """
    Manages a database transaction across multiple repositories.
    All repository operations within a UnitOfWork share a single database transaction.
    """
    diagnostic_runs: DiagnosticRunRepository
    connection_profiles: ConnectionProfileRepository
    analysis_results: AnalysisResultRepository
    readiness_reports: ReadinessReportRepository
    audit_log: AuditLogRepository

    @abstractmethod
    async def __aenter__(self) -> "UnitOfWork":
        """Begin the transaction."""

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Commit on success; rollback on exception."""

    @abstractmethod
    async def commit(self) -> None:
        """Explicitly commit the transaction."""

    @abstractmethod
    async def rollback(self) -> None:
        """Explicitly roll back the transaction."""
```

**Usage in application services**:

```python
async with unit_of_work as uow:
    run = await uow.diagnostic_runs.get_by_id_required(run_id)
    run.start()
    await uow.diagnostic_runs.save(run)
    await uow.commit()
# Transaction committed; DiagnosticRunStarted event dispatched after commit
```

**Concrete implementation**: `preflight/core/infrastructure/sqlalchemy_unit_of_work.py`
