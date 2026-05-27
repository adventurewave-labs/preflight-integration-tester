# ADR-006: Data Storage Architecture

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, DevOps Lead  
**Technical Story**: [PRD-002 §5](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight generates and consumes several distinct categories of data with different storage requirements:

| Data Category | Characteristics | Requirements |
|---------------|-----------------|--------------|
| Diagnostic run state | Job lifecycle, phase completion, error events | Durable, queryable, ACID |
| Schema snapshots | Per-system entity/field metadata; reused across runs | Cacheable, TTL, fast read |
| Pipeline metrics | Time-series latency/throughput measurements per agent | High write rate, time-range queryable |
| Analysis results | SchemaInconsistencies, PipelineBottlenecks, MiddlewareGaps | Durable, structured, queryable |
| Readiness reports | Final HTML/JSON reports per run | Durable, large blob-friendly |
| Job queue state | Celery task status, progress percentages, retry counts | Fast R/W, ephemeral acceptable |
| Session state | API authentication tokens, in-progress wizard state | Fast R/W, TTL |

A single database technology cannot optimally serve all these categories. The architecture must balance operational simplicity (fewer systems to operate) against fit-for-purpose data handling.

---

## Decision Drivers

- ACID compliance for diagnostic result data (financial and contractual decisions depend on reports)
- High-throughput write capability for real-time pipeline metrics during stress tests
- Fast key-value lookups for schema caches and session state
- Reliable job queue with durability guarantees
- Operational simplicity: VPC deployments should not require exotic infrastructure
- Backup and recovery: diagnostic results must be recoverable
- Encryption at rest and in transit

---

## Considered Options

### Option A: PostgreSQL (primary) + Redis (cache and queue) — chosen
### Option B: MongoDB (document store) as sole database
### Option C: SQLite (development) + PostgreSQL (production)
### Option D: Pure file storage (JSON on disk)

---

## Decision Outcome

**Chosen option: PostgreSQL for persistent results + Redis for cache, session, and job queue**.

This is a deliberate polyglot persistence decision: each store is used only where it provides clear fit-for-purpose advantages.

### PostgreSQL — Persistent Data Store

**Used for**: DiagnosticRun records, ConnectionProfile metadata, AnalysisResults, ReadinessReports, RemediationItems, audit logs.

**Why PostgreSQL specifically**:
- **ACID transactions**: diagnostic results are written transactionally; partial writes (e.g., schema analysis result without pipeline result) are impossible.
- **JSONB columns**: `AnalysisResults.raw_findings` stores variable-structure finding payloads as JSONB; complex nested queries are supported with GIN indexes.
- **Row-level security**: database-level tenant isolation for multi-tenant SaaS deployment.
- **`pg_partman`**: `pipeline_metrics` table partitioned by `run_id`; old partitions are archived after report generation.
- **Full-text search on reports**: `ts_vector` columns enable keyword search across historical diagnostic reports.
- **TimescaleDB extension** (optional): time-series hypertables for pipeline metric data in high-throughput deployments.

### Schema Overview

```sql
-- Core tables (simplified)
diagnostic_runs (
    id UUID PRIMARY KEY,
    status diagnostic_run_status,  -- ENUM: pending, running, completed, failed
    scenario_config JSONB,
    created_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

connection_profiles (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES diagnostic_runs(id),
    system_type VARCHAR(64),
    status connection_status,
    schema_snapshot_key VARCHAR(255),  -- Redis cache key
    connected_at TIMESTAMPTZ
);

analysis_results (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES diagnostic_runs(id),
    result_type VARCHAR(64),
    severity severity_level,
    payload JSONB,
    impact_score SMALLINT CHECK (impact_score BETWEEN 0 AND 100)
);

readiness_reports (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES diagnostic_runs(id) UNIQUE,
    readiness_score SMALLINT,
    verdict readiness_verdict,  -- ENUM: GO, NOT_YET, NOT_READY
    report_html TEXT,
    report_json JSONB,
    generated_at TIMESTAMPTZ
);
```

### Redis — Cache, Session, and Job Queue

**Used for**:

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `schema:{system_id}:{snapshot_hash}` | 24h | Cached `SchemaSnapshot`; avoids re-introspection for unchanged systems |
| `job:{run_id}:progress` | 4h | Real-time job progress (phase, % complete, current step) |
| `session:{token_hash}` | 8h | API session authentication context |
| `rate_limit:{connector_id}` | 60s | Per-connector rate-limit sliding window counter |
| `celery:*` | Celery-managed | Celery task queue and result backend |

**Why Redis specifically**:
- Sub-millisecond key-value lookups for session state and rate-limit counters.
- Pub/Sub for streaming job progress events to connected WebSocket clients.
- Celery's native Redis broker and result backend integration.
- TTL support prevents stale schema caches from persisting indefinitely.
- Redis Sentinel or Redis Cluster for HA in enterprise deployments.

### Positive Consequences

- ACID guarantees on diagnostic results: reports are always internally consistent.
- Schema snapshots are cached and reused; re-running a diagnostic against the same systems is fast.
- Job progress is real-time and survives FastAPI process restarts (state in Redis, not in memory).
- PostgreSQL JSONB enables flexible analysis result storage without sacrificing queryability.
- Both PostgreSQL and Redis are universally supported in enterprise VPC environments.

### Negative Consequences

- Two database systems to operate, monitor, and back up.
- Redis data (schema cache, job progress) is not backed up by default; loss requires schema re-introspection and job re-run rather than data loss.
- Pipeline metric time-series data at high agent concurrency can grow large; partitioning strategy must be maintained.

---

## Alternatives Considered

### Option B: MongoDB as Sole Database

| Criterion | Assessment |
|-----------|-----------|
| Schema flexibility | Excellent for variable analysis result payloads |
| ACID compliance | Multi-document transactions added in 4.0 but more complex than PostgreSQL |
| Job queue | Would require a separate queue system anyway |
| Enterprise familiarity | Many enterprise procurement teams require PostgreSQL or Oracle; MongoDB faces more scrutiny |
| Verdict | **Rejected** — ACID transaction complexity for diagnostic results; no job queue advantage; PostgreSQL JSONB covers the flexibility requirement |

### Option C: SQLite (Development) + PostgreSQL (Production)

| Criterion | Assessment |
|-----------|-----------|
| Development simplicity | SQLite is zero-config for local development |
| Schema parity | SQLite and PostgreSQL have subtle type and constraint differences that cause production bugs |
| Concurrency | SQLite write concurrency is incompatible with Celery workers |
| Verdict | **Rejected** — parity risks and write-concurrency limitations; Docker Compose makes local PostgreSQL straightforward enough to eliminate this tradeoff |

### Option D: Pure File Storage (JSON on disk)

| Criterion | Assessment |
|-----------|-----------|
| Operational simplicity | Minimal; no database to manage |
| ACID compliance | None; partial writes leave corrupt report files |
| Queryability | None; searching historical reports requires full-file parsing |
| Multi-instance | Files on disk do not work with multiple FastAPI/Celery instances |
| Verdict | **Rejected** — no ACID, no queryability, no multi-process support |

---

## Implementation Notes

- ORM: **SQLAlchemy 2.0** with async engine (`asyncpg` driver for PostgreSQL).
- Migrations: **Alembic** manages schema migrations; migration scripts included in the repository.
- Redis client: `redis-py` with async support (`aioredis` compatibility layer).
- Connection pooling: SQLAlchemy connection pool sized to Celery worker count × 2.
- Encryption: PostgreSQL TLS required in all non-localhost deployments; Redis TLS enabled in VPC deployments.
- Backup: PostgreSQL `pg_dump` scheduled daily; Redis persistence uses RDB snapshots for schema cache (AOF not required for cache-only data).

---

## Links

- [ADR-004: Schema Consistency Analysis Strategy](./ADR-004-schema-consistency-analysis-strategy.md)
- [ADR-008: Async Processing Architecture](./ADR-008-async-processing-architecture.md)
- [ADR-010: Deployment and Containerization](./ADR-010-deployment-and-containerization.md)
- [DDD: Readiness Reporting Context](../ddd/bounded-contexts.md)
