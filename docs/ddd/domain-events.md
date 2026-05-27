# Domain Events Reference — Preflight Integration Tester

This document is the authoritative catalogue of all domain events in the Preflight system. Every state change of significance is expressed as a domain event. Events are the primary mechanism for communication between bounded contexts.

---

## Event Conventions

All domain events follow these conventions:

```python
class DomainEvent(BaseModel, frozen=True):
    event_id: UUID = Field(default_factory=uuid4)     # Unique event identifier
    event_type: str                                     # Fully qualified event name
    aggregate_type: str                                 # Aggregate that produced it
    aggregate_id: str                                   # ID of the producing aggregate
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "1.0"                         # For forward compatibility
    payload: dict                                       # Event-specific data
```

**Event naming convention**: `<AggregateType><PastTenseFact>` (e.g., `DiagnosticRunStarted`, `ConnectionEstablished`).

**Publishing**: Domain events are published to Redis Pub/Sub. Channel names follow: `preflight.events.<aggregate_type_lower>`.

**Ordering guarantee**: Events from the same aggregate are published in order. No ordering guarantee across aggregates.

**Idempotency**: All event consumers must be idempotent; duplicate delivery is possible.

---

## Event Catalogue

### DiagnosticRun Events

---

#### `DiagnosticRunStarted`

Published when a `DiagnosticRun` transitions from PENDING to CONNECTING.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | The DiagnosticRun identifier |
| `customer_name` | str | Customer organisation |
| `use_case_type` | str | UseCaseType value |
| `system_types` | list[str] | SystemType values of all configured systems |
| `expected_concurrency` | int | Target agent concurrency from SimulationScenario |
| `initiated_by` | str | API user who started the run |
| `started_at` | datetime | Timestamp |

**Producing aggregate**: `DiagnosticRun`  
**Channel**: `preflight.events.diagnostic_run`  
**Consuming contexts**: Simulation Context (to initialise DiagnosticAgent pool), all contexts (to register the run_id)

**Trigger**: `DiagnosticRun.start()` method called

---

#### `DiagnosticRunCancelled`

Published when a running `DiagnosticRun` is cancelled by the user.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | The DiagnosticRun identifier |
| `cancelled_at` | datetime | Timestamp |
| `cancelled_by` | str | API user who cancelled |
| `phase_at_cancellation` | str | DiagnosticRunStatus at time of cancellation |
| `partial_results_available` | bool | Whether any analysis results were completed before cancellation |

**Producing aggregate**: `DiagnosticRun`  
**Channel**: `preflight.events.diagnostic_run`  
**Consuming contexts**: All contexts (to stop processing); Celery task revocation

---

#### `DiagnosticRunFailed`

Published when a `DiagnosticRun` transitions to FAILED.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | The DiagnosticRun identifier |
| `failed_at` | datetime | Timestamp |
| `failure_phase` | str | Phase where failure occurred |
| `error_code` | str | Machine-readable error code |
| `error_message` | str | Human-readable description |
| `retry_possible` | bool | Whether the run can be retried |

**Producing aggregate**: `DiagnosticRun`  
**Channel**: `preflight.events.diagnostic_run`  
**Consuming contexts**: Readiness Reporting Context (to mark report as unavailable); notification service

---

#### `DiagnosticRunCompleted`

Published when a `DiagnosticRun` transitions to COMPLETED.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | The DiagnosticRun identifier |
| `report_id` | UUID | The generated ReadinessReport identifier |
| `readiness_score` | int | Final ReadinessScore value |
| `verdict` | str | ReadinessVerdict value |
| `completed_at` | datetime | Timestamp |
| `duration_seconds` | int | Total wall-clock time of the run |

**Producing aggregate**: `DiagnosticRun`  
**Channel**: `preflight.events.diagnostic_run`  
**Consuming contexts**: Scenario Modeling Context (to make run available for what-if analysis); notification service

---

### Connection Events

---

#### `ConnectionEstablished`

Published when a `ConnectionProfile` transitions to CONNECTED status.

| Field | Type | Description |
|-------|------|-------------|
| `connection_id` | UUID | The ConnectionProfile identifier |
| `run_id` | UUID | Parent DiagnosticRun |
| `system_type` | str | SystemType value |
| `display_name` | str | Human-readable connection name |
| `total_entities` | int | Number of entities discovered during introspection |
| `total_fields` | int | Number of fields across all entities |
| `connected_at` | datetime | Timestamp |

**Producing aggregate**: `ConnectionProfile`  
**Channel**: `preflight.events.connection`  
**Consuming contexts**: Connectivity Context (to update ConnectionSet readiness); Schema Analysis Context (to begin entity graph construction)

---

#### `ConnectionFailed`

Published when a `ConnectionProfile` transitions to FAILED status.

| Field | Type | Description |
|-------|------|-------------|
| `connection_id` | UUID | The ConnectionProfile identifier |
| `run_id` | UUID | Parent DiagnosticRun |
| `system_type` | str | SystemType value |
| `error_code` | str | e.g., `AUTH_FAILED`, `TIMEOUT`, `NETWORK_UNREACHABLE`, `PERMISSION_DENIED` |
| `error_message` | str | Human-readable description |
| `retry_count` | int | Number of attempts made |
| `failed_at` | datetime | Timestamp |

**Producing aggregate**: `ConnectionProfile`  
**Channel**: `preflight.events.connection`  
**Consuming contexts**: Connectivity Context (to evaluate whether the run can proceed); DiagnosticRun (to consider transitioning to FAILED)

---

#### `ConnectionSetReady`

Published when all `ConnectionProfile` objects in a `ConnectionSet` have reached CONNECTED status. This is the signal for downstream contexts to begin their work.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `connection_ids` | list[UUID] | All ConnectionProfile IDs |
| `system_types` | list[str] | All connected system types |
| `schema_snapshot_refs` | dict[str, str] | system_type → Redis cache key for SchemaSnapshot |
| `ready_at` | datetime | Timestamp |

**Producing aggregate**: `DiagnosticRun` (via ConnectionSet domain service)  
**Channel**: `preflight.events.connection`  
**Consuming contexts**: Simulation Context, Schema Analysis Context, Pipeline Testing Context (all begin their work on receiving this event)

---

#### `SchemaIntrospectionCompleted`

Published when a `SchemaSnapshot` is successfully captured for a connected system.

| Field | Type | Description |
|-------|------|-------------|
| `connection_id` | UUID | The ConnectionProfile identifier |
| `run_id` | UUID | Parent DiagnosticRun |
| `system_type` | str | SystemType value |
| `snapshot_ref` | str | Redis cache key |
| `entity_count` | int | Total entities in the snapshot |
| `field_count` | int | Total fields |
| `snapshot_hash` | str | SHA-256 of snapshot content (for cache validation) |
| `introspected_at` | datetime | Timestamp |

**Producing aggregate**: `ConnectionProfile`  
**Channel**: `preflight.events.connection`  
**Consuming contexts**: Schema Analysis Context (to begin EntityGraph construction)

---

### Simulation Events

---

#### `SimulationStarted`

Published when the concurrent DiagnosticAgent pool begins execution.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `scenario_id` | UUID | SimulationScenario identifier |
| `agent_count` | int | Initial agent concurrency |
| `planned_phases` | list[str] | SimulationPhase values in execution order |
| `estimated_duration_seconds` | int | Estimated total simulation time |
| `started_at` | datetime | Timestamp |

**Producing aggregate**: `DiagnosticRun`  
**Channel**: `preflight.events.simulation`  
**Consuming contexts**: Pipeline Testing Context (to begin metrics aggregation)

---

#### `SimulationPhaseCompleted`

Published when a ConcurrencyProfile phase finishes.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `phase` | str | Completed SimulationPhase |
| `agent_count` | int | Agent count during this phase |
| `duration_seconds` | int | Phase duration |
| `p95_latency_ms` | dict[str, float] | system_type → p95 latency for this phase |
| `error_rate` | dict[str, float] | system_type → error rate for this phase |
| `throttle_events` | int | Total throttle events during this phase |
| `completed_at` | datetime | Timestamp |

**Producing aggregate**: Simulation Context  
**Channel**: `preflight.events.simulation`  
**Consuming contexts**: Pipeline Testing Context; progress tracking (Redis job key update)

---

#### `BreakingPointDetected`

Published when a system's performance degrades beyond acceptable thresholds during simulation.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `system_type` | str | The system that hit its breaking point |
| `breaking_concurrency` | int | Agent count when breaking point was detected |
| `baseline_p95_ms` | float | p95 latency at single-agent baseline |
| `breaking_p95_ms` | float | p95 latency at breaking point |
| `degradation_ratio` | float | breaking_p95 / baseline_p95 |
| `error_rate` | float | Error rate at breaking point |
| `detected_at` | datetime | Timestamp |

**Producing aggregate**: Simulation Context  
**Channel**: `preflight.events.simulation`  
**Consuming contexts**: Pipeline Testing Context (creates PipelineBottleneck entity)

---

#### `SimulationCompleted`

Published when all simulation phases have completed.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `pipeline_metrics_ref` | str | Redis key for serialised PipelineMetrics |
| `breaking_points` | list[str] | system_types that hit breaking points |
| `total_queries_executed` | int | Total read queries across all agents |
| `total_duration_seconds` | int | Total simulation duration |
| `completed_at` | datetime | Timestamp |

**Producing aggregate**: Simulation Context  
**Channel**: `preflight.events.simulation`  
**Consuming contexts**: Pipeline Testing Context (to begin bottleneck analysis); DiagnosticRun (to advance to ANALYSING state)

---

### Schema Analysis Events

---

#### `EntityMappingCompleted`

Published when the cross-system entity matching algorithm completes.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `analysis_id` | UUID | Parent AnalysisResult |
| `total_entities_per_system` | dict[str, int] | system_type → entity count |
| `total_mappings` | int | Total entity mappings identified |
| `high_confidence_mappings` | int | Mappings with confidence >= 0.80 |
| `low_confidence_mappings` | int | Mappings with confidence 0.65–0.79 |
| `completed_at` | datetime | Timestamp |

**Producing aggregate**: Schema Analysis Context (AnalysisResult)  
**Channel**: `preflight.events.schema_analysis`  
**Consuming contexts**: Schema Analysis Context itself (to proceed to inconsistency detection)

---

#### `SchemaInconsistencyDetected`

Published for each individual `SchemaInconsistency` as it is identified.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `analysis_id` | UUID | Parent AnalysisResult |
| `inconsistency_id` | UUID | The SchemaInconsistency identifier |
| `entity_a_system` | str | First system type |
| `entity_a_name` | str | First entity name |
| `entity_b_system` | str | Second system type |
| `entity_b_name` | str | Second entity name |
| `inconsistency_type` | str | InconsistencyType value |
| `severity` | str | SeverityLevel value |
| `impact_score` | int | 0–100 |
| `detected_at` | datetime | Timestamp |

**Producing aggregate**: AnalysisResult  
**Channel**: `preflight.events.schema_analysis`  
**Consuming contexts**: Middleware Assessment Context (to correlate with gap identification); Readiness Reporting Context (incremental report building)

---

#### `SchemaAnalysisCompleted`

Published when all schema inconsistencies have been identified and ranked.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `analysis_id` | UUID | Parent AnalysisResult |
| `schema_consistency_score` | int | Sub-score 0–100 |
| `total_inconsistencies` | int | Total count |
| `critical_count` | int | CRITICAL severity count |
| `high_count` | int | HIGH severity count |
| `medium_count` | int | MEDIUM severity count |
| `low_count` | int | LOW severity count |
| `completed_at` | datetime | Timestamp |

**Producing aggregate**: AnalysisResult  
**Channel**: `preflight.events.schema_analysis`  
**Consuming contexts**: Middleware Assessment Context; Readiness Reporting Context; DiagnosticRun (schema analysis phase tracking)

---

### Pipeline Testing Events

---

#### `PipelineBottleneckIdentified`

Published for each `PipelineBottleneck` confirmed by the Pipeline Testing Context.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `analysis_id` | UUID | Parent AnalysisResult |
| `bottleneck_id` | UUID | The PipelineBottleneck identifier |
| `system_type` | str | Affected system |
| `entity_name` | str | Affected entity/table |
| `bottleneck_type` | str | BottleneckType value |
| `max_safe_qps` | float | QPS before degradation |
| `severity` | str | SeverityLevel value |
| `identified_at` | datetime | Timestamp |

**Producing aggregate**: AnalysisResult  
**Channel**: `preflight.events.pipeline_testing`  
**Consuming contexts**: Middleware Assessment Context; Readiness Reporting Context

---

#### `PipelineTestCompleted`

Published when pipeline bottleneck analysis is complete.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `analysis_id` | UUID | Parent AnalysisResult |
| `pipeline_readiness_score` | int | Sub-score 0–100 |
| `total_bottlenecks` | int | Total count |
| `systems_with_breaking_points` | list[str] | SystemTypes that hit breaking points |
| `completed_at` | datetime | Timestamp |

**Producing aggregate**: AnalysisResult  
**Channel**: `preflight.events.pipeline_testing`  
**Consuming contexts**: Readiness Reporting Context; DiagnosticRun (pipeline testing phase tracking)

---

### Middleware Assessment Events

---

#### `MiddlewareGapIdentified`

Published for each `MiddlewareGap` identified.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `analysis_id` | UUID | Parent AnalysisResult |
| `gap_id` | UUID | The MiddlewareGap identifier |
| `gap_type` | str | GapType value |
| `title` | str | Short description |
| `severity` | str | SeverityLevel value |
| `effort_level` | str | EffortLevel value |
| `effort_weeks_min` | int | Minimum weeks estimate |
| `effort_weeks_max` | int | Maximum weeks estimate |
| `affected_system_count` | int | How many systems affected |
| `identified_at` | datetime | Timestamp |

**Producing aggregate**: AnalysisResult  
**Channel**: `preflight.events.middleware_assessment`  
**Consuming contexts**: Readiness Reporting Context

---

#### `MiddlewareAssessmentCompleted`

Published when all middleware gaps have been identified.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `analysis_id` | UUID | Parent AnalysisResult |
| `middleware_readiness_score` | int | Sub-score 0–100 |
| `total_gaps` | int | Total gap count |
| `total_effort_weeks_min` | int | Summed minimum effort |
| `total_effort_weeks_max` | int | Summed maximum effort |
| `completed_at` | datetime | Timestamp |

**Producing aggregate**: AnalysisResult  
**Channel**: `preflight.events.middleware_assessment`  
**Consuming contexts**: Readiness Reporting Context; DiagnosticRun (middleware assessment phase tracking)

---

### Readiness Reporting Events

---

#### `ReadinessScoreCalculated`

Published when the composite `ReadinessScore` and `ReadinessVerdict` are determined.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `report_id` | UUID | The ReadinessReport identifier |
| `readiness_score` | int | Composite score 0–100 |
| `verdict` | str | ReadinessVerdict value |
| `schema_sub_score` | int | Schema consistency sub-score |
| `pipeline_sub_score` | int | Pipeline readiness sub-score |
| `middleware_sub_score` | int | Middleware readiness sub-score |
| `critical_penalty` | int | Points deducted for critical findings |
| `calculated_at` | datetime | Timestamp |

**Producing aggregate**: ReadinessReport  
**Channel**: `preflight.events.readiness_reporting`  
**Consuming contexts**: DiagnosticRun (to store score in run record); Scenario Modeling Context

---

#### `RemediationPlanBuilt`

Published when the `RemediationPlan` has been built and sequenced.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `report_id` | UUID | The ReadinessReport identifier |
| `total_items` | int | Total RemediationItem count |
| `critical_items` | int | CRITICAL severity items |
| `total_effort_weeks_min` | int | Total minimum effort |
| `total_effort_weeks_max` | int | Total maximum effort |
| `built_at` | datetime | Timestamp |

**Producing aggregate**: ReadinessReport  
**Channel**: `preflight.events.readiness_reporting`  
**Consuming contexts**: Scenario Modeling Context (uses remediation plan as baseline for what-if)

---

#### `ReadinessReportGenerated`

Published when the `ReadinessReport` is fully generated in all formats (HTML, PDF, JSON) and available for retrieval.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | UUID | Parent DiagnosticRun |
| `report_id` | UUID | The ReadinessReport identifier |
| `readiness_score` | int | Final score |
| `verdict` | str | Final verdict |
| `report_html_path` | str | File path for HTML report |
| `report_pdf_path` | str | File path for PDF report |
| `report_json_url` | str | API URL for JSON report |
| `generated_at` | datetime | Timestamp |

**Producing aggregate**: ReadinessReport  
**Channel**: `preflight.events.readiness_reporting`  
**Consuming contexts**: DiagnosticRun (transitions to COMPLETED); notification service (customer notification); Scenario Modeling Context

---

## Event Flow Diagram

```
DiagnosticRun.start()
        │
        ▼
DiagnosticRunStarted
        │
        ├──▶ [Connectivity] Connect all systems
        │         │
        │         ├──▶ ConnectionEstablished × N
        │         │         │
        │         │         └──▶ SchemaIntrospectionCompleted × N
        │         │
        │         └──▶ ConnectionSetReady
        │                   │
        │    ┌──────────────┼──────────────────┐
        │    │              │                  │
        │    ▼              ▼                  ▼
        │ [Schema]      [Simulation]      [Pipeline]
        │  Analysis       Starts           Testing
        │                 │                waits
        │                 │
        │         SimulationStarted
        │                 │
        │         SimulationPhaseCompleted × 5
        │                 │
        │         (BreakingPointDetected × 0..N)
        │                 │
        │         SimulationCompleted
        │                 │
        │    ┌────────────┘
        │    ▼
        │ [Pipeline Testing] analyses metrics
        │    │
        │    ├──▶ PipelineBottleneckIdentified × 0..N
        │    └──▶ PipelineTestCompleted
        │
        ├──▶ EntityMappingCompleted
        │         │
        │         ├──▶ SchemaInconsistencyDetected × 0..N
        │         └──▶ SchemaAnalysisCompleted
        │                   │
        │         ┌─────────┘
        │         ▼
        │ [Middleware Assessment]
        │         │
        │         ├──▶ MiddlewareGapIdentified × 0..N
        │         └──▶ MiddlewareAssessmentCompleted
        │                   │
        │    ┌──────────────┘
        │    ▼
        │ [Readiness Reporting] — all three sub-scores available
        │    │
        │    ├──▶ ReadinessScoreCalculated
        │    ├──▶ RemediationPlanBuilt
        │    └──▶ ReadinessReportGenerated
        │               │
        └───────────────┘
DiagnosticRunCompleted
```

---

## Event Versioning

Events include a `schema_version` field. When an event's payload schema changes:

1. **Backward-compatible additions** (new optional fields): increment minor version (e.g., `1.0` → `1.1`); all consumers continue to work.
2. **Breaking changes** (removed or renamed fields): create a new event version (e.g., `DiagnosticRunStartedV2`); run both event types in parallel during the migration window.
3. **New events**: no versioning required; consumers add subscriptions.

Current event schema versions are all `1.0` (initial release).
