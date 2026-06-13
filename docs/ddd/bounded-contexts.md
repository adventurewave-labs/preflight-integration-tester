# Bounded Context Map — Preflight Integration Tester

This document defines all seven bounded contexts in Preflight's domain, their responsibilities, internal models, and the relationships between them.

---

## Bounded Context Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        PREFLIGHT DOMAIN                                   │
│                                                                           │
│  ┌─────────────────┐   upstream    ┌─────────────────┐                   │
│  │  1. Connectivity│──────────────▶│  2. Simulation  │                   │
│  │     Context     │               │     Context     │                   │
│  │                 │               │                 │                   │
│  │  Manages system │   upstream    │  Models & runs  │                   │
│  │  connections    │──────────────▶│  AI workload    │                   │
│  │  and credentials│               │  simulations    │                   │
│  └────────┬────────┘               └────────┬────────┘                   │
│           │                                 │                             │
│           │ upstream                        │ upstream                    │
│           │                                 │                             │
│  ┌────────▼────────┐               ┌────────▼────────┐                   │
│  │ 3. Schema       │               │ 4. Pipeline     │                   │
│  │    Analysis     │               │    Testing      │                   │
│  │    Context      │               │    Context      │                   │
│  │                 │               │                 │                   │
│  │  Entity mapping,│               │  Stress testing,│                   │
│  │  inconsistency  │               │  bottleneck     │                   │
│  │  detection      │               │  detection      │                   │
│  └────────┬────────┘               └────────┬────────┘                   │
│           │                                 │                             │
│           │ upstream                        │ upstream                    │
│           │                    ┌────────────▼────────┐                   │
│           │               ┌───▶│ 5. Middleware        │                  │
│           │               │    │    Assessment        │                  │
│           │               │    │    Context           │                  │
│           │               │    │                      │                  │
│           │               │    │  Identifies missing  │                  │
│           │               │    │  integration layers  │                  │
│           │               │    └─────────────────────┘                   │
│           │               │                 │                             │
│           │               │                 │ upstream                    │
│           │               │    upstream     │                             │
│           └───────────────┼─────────────────▼────────┐                   │
│                           │    ┌─────────────────────▶│                  │
│                           └───▶│ 6. Readiness        │                   │
│                                │    Reporting         │                   │
│                                │    Context           │                   │
│                                │                      │                   │
│                                │  Aggregates findings,│                   │
│                                │  generates score,    │                   │
│                                │  produces report     │                   │
│                                └──────────┬───────────┘                   │
│                                           │                               │
│                                           │ upstream (read-only)          │
│                                           ▼                               │
│                                ┌──────────────────────┐                  │
│                                │ 7. Scenario Modeling  │                  │
│                                │    Context            │                  │
│                                │                       │                  │
│                                │  What-if analysis and │                  │
│                                │  interactive cost     │                  │
│                                │  modeling             │                  │
│                                └───────────────────────┘                  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Context Map Relationships

| Relationship | Type | Description |
|-------------|------|-------------|
| Connectivity → Simulation | Upstream / Downstream | Connectivity provides `ConnectionSet`; Simulation is the downstream consumer. Simulation conforms to Connectivity's `SchemaSnapshot` model. |
| Connectivity → Schema Analysis | Upstream / Downstream + ACL | Schema Analysis has an Anti-Corruption Layer converting `SchemaSnapshot` into its own `EntityGraph` model. |
| Connectivity → Pipeline Testing | Upstream / Downstream + ACL | Pipeline Testing adapts `ConnectionProfile` data into its own `PipelineTarget` model. |
| Schema Analysis → Middleware Assessment | Upstream / Downstream | Middleware Assessment uses `SchemaInconsistency` findings from Schema Analysis to identify root-cause middleware gaps. |
| Pipeline Testing → Middleware Assessment | Upstream / Downstream | Pipeline bottleneck patterns inform middleware gap identification. |
| Schema Analysis → Readiness Reporting | Upstream / Downstream | Readiness Reporting aggregates `SchemaInconsistency` findings into the score and report. |
| Pipeline Testing → Readiness Reporting | Upstream / Downstream | Readiness Reporting aggregates `PipelineBottleneck` findings. |
| Middleware Assessment → Readiness Reporting | Upstream / Downstream | Readiness Reporting aggregates `MiddlewareGap` findings and builds `RemediationPlan`. |
| Readiness Reporting → Scenario Modeling | Upstream / Downstream (read-only) | Scenario Modeling reads completed `ReadinessReport` as its starting state; it does not modify it. |
| All contexts | Shared Kernel | `SeverityLevel`, `EffortEstimate`, `DiagnosticRunId` are shared types. |

---

## Context 1: Connectivity Context

### Responsibility
Manages the lifecycle of read-only connections to enterprise systems. Handles authentication, schema introspection, connection pooling, health monitoring, and the fundamental safety guarantee that no write operations are possible.

### Core Domain Concepts
| Concept | Type | Description |
|---------|------|-------------|
| `ConnectionProfile` | Aggregate Root | Complete record of a connection: system type, endpoint, status, capabilities |
| `ConnectionCredentials` | Value Object | Authentication material; never persisted; `SecretStr` fields |
| `ConnectionStatus` | Value Object | Lifecycle state: PENDING, CONNECTING, CONNECTED, DEGRADED, FAILED, DISCONNECTED |
| `ConnectionSet` | Entity | Ordered collection of `ConnectionProfile` objects for a DiagnosticRun |
| `SchemaSnapshot` | Value Object | Immutable, point-in-time schema metadata from one system |
| `EntityMetadata` | Value Object | Metadata for one entity within a SchemaSnapshot |
| `FieldMetadata` | Value Object | Metadata for one field within an EntityMetadata |
| `SystemType` | Value Object | String identifier for the enterprise system class |

### Published Domain Events
- `ConnectionEstablished` — a `ConnectionProfile` transitioned to CONNECTED
- `ConnectionFailed` — a connection attempt failed
- `SchemaIntrospectionCompleted` — `SchemaSnapshot` successfully captured
- `ConnectionSetReady` — all connections in a `ConnectionSet` are established

### Invariants
- A `ConnectionProfile` can only transition to CONNECTED if credential validation succeeds.
- `ConnectionCredentials` must never be serialised to JSON, logged, or persisted.
- `execute_read_query()` must call `assert_read_only()` before delegating to the SDK.

### External Dependencies
- Enterprise SDK libraries (per connector): `simple-salesforce`, `snowflake-connector-python`, `sap-rfc`, `cx-oracle`, `psycopg2`, `databricks-sql-connector`
- Redis (schema cache)

---

## Context 2: Simulation Context

### Responsibility
Models the AI workload the customer intends to deploy, constructs a realistic execution plan for `DiagnosticAgent` instances, and orchestrates the concurrent read-only load simulation against connected systems. Produces `PipelineMetrics` and `AgentRunMetrics` as its primary outputs.

### Core Domain Concepts
| Concept | Type | Description |
|---------|------|-------------|
| `SimulationScenario` | Aggregate Root | Customer's description of intended AI deployment |
| `ExecutionPlan` | Entity | Ordered list of read operations a DiagnosticAgent will execute |
| `DiagnosticAgent` | Domain Service | Stateful simulation actor executing an ExecutionPlan |
| `ConcurrencyProfile` | Value Object | Schedule of agent counts over time (ramp, peak, spike, recovery) |
| `AgentRunMetrics` | Value Object | Per-agent performance measurements |
| `PipelineMetrics` | Value Object | Aggregated performance data across all agents |
| `SimulationPhase` | Value Object | Enum: BASELINE, RAMP_UP, SUSTAINED_PEAK, SPIKE, RECOVERY |
| `UseCaseType` | Value Object | Enum: CUSTOMER_SERVICE_AI, SUPPLY_CHAIN_AGENT, FINANCIAL_REPORTING, DOCUMENT_PROCESSING, CUSTOM |

### Published Domain Events
- `SimulationStarted` — concurrent agent execution begins
- `SimulationPhaseCompleted` — a ConcurrencyProfile phase completes
- `BreakingPointDetected` — a system's performance degrades beyond thresholds
- `SimulationCompleted` — all phases complete; PipelineMetrics available

### Invariants
- `DiagnosticAgent` instances may only call `BaseConnector.execute_read_query()` and `stream_records()`.
- The `ConcurrencyProfile` peak agent count must not exceed the `SimulationScenario.max_concurrency` limit.
- Simulation must terminate (either naturally or via timeout) and never run indefinitely.

### Upstream Dependencies
- Connectivity Context: `ConnectionSet` (provides the connectors agents will use)
- Connectivity Context: `SchemaSnapshot` (used to generate `ExecutionPlan`)

---

## Context 3: Schema Analysis Context

### Responsibility
Analyses the `SchemaSnapshot`s from all connected systems to identify how business entities are modelled differently across systems. Produces an authoritative list of `SchemaInconsistency` findings, each with a severity and impact score for the proposed AI deployment.

### Core Domain Concepts
| Concept | Type | Description |
|---------|------|-------------|
| `AnalysisResult` | Aggregate Root | Container for all analysis findings from one DiagnosticRun |
| `EntityGraph` | Value Object | Directed graph of entities and relationships for one system |
| `CrossSystemGraph` | Value Object | Bipartite graph of matched entities across all systems |
| `EntityMapping` | Value Object | Proposed correspondence between two entities; includes MatchConfidence and evidence |
| `MatchConfidence` | Value Object | Numeric score (0.0–1.0) for entity mapping certainty |
| `SchemaInconsistency` | Entity | A documented schema difference with severity and impact score |
| `InconsistencyType` | Value Object | Enum: KEY_TYPE_MISMATCH, NULLABILITY_DIFFERENCE, VALUE_FORMAT_MISMATCH, CARDINALITY_DIVERGENCE, MISSING_FIELD |
| `ImpactScore` | Value Object | Integer 0–100: how severely this inconsistency would break the AI deployment |
| `SchemaConsistencyScore` | Value Object | Aggregate sub-score (0–100) for schema consistency dimension of the ReadinessScore |

### Published Domain Events
- `EntityMappingCompleted` — cross-system entity matching finished; EntityMappings available
- `SchemaInconsistencyDetected` — a specific SchemaInconsistency identified
- `SchemaAnalysisCompleted` — all SchemaInconsistencies ranked and scored

### Invariants
- Every `SchemaInconsistency` must reference exactly two `EntityMapping` objects.
- `SchemaConsistencyScore` is `null` until `SchemaAnalysisCompleted` is published.
- `EntityMapping` objects with `MatchConfidence < 0.65` (configurable) are not included in the inconsistency analysis.

### Upstream Dependencies (with ACL)
- Connectivity Context: `SchemaSnapshot` objects are consumed via an ACL that converts them into `EntityGraph` instances in the Schema Analysis bounded context's own model.

---

## Context 4: Pipeline Testing Context

### Responsibility
Receives `PipelineMetrics` from the Simulation Context and analyses them to identify specific pipeline bottlenecks, breaking points, and performance degradation patterns. Produces `PipelineBottleneck` findings with detailed performance data and a `PipelineReadinessScore`.

### Core Domain Concepts
| Concept | Type | Description |
|---------|------|-------------|
| `PipelineTestResult` | Aggregate Root | Container for all pipeline findings from one DiagnosticRun |
| `PipelineBottleneck` | Entity | A documented performance constraint: system, type, concurrency level, latencies |
| `BreakingPoint` | Value Object | The concurrency level at which degradation was detected |
| `BaselineLatency` | Value Object | p50, p95, p99 latency from the single-agent baseline phase |
| `ThrottleEvent` | Value Object | A rate-limit response: system, query type, retry-after |
| `BottleneckType` | Value Object | Enum: THROUGHPUT, LATENCY, THROTTLING, CONNECTION_LIMIT |
| `PipelineReadinessScore` | Value Object | Sub-score (0–100) for the pipeline performance dimension of ReadinessScore |
| `MaxSafeQPS` | Value Object | Maximum queries per second before performance degrades |

### Published Domain Events
- `PipelineTestStarted` — pipeline analysis begins
- `PipelineBottleneckIdentified` — a PipelineBottleneck confirmed
- `PipelineTestCompleted` — all bottlenecks identified; PipelineReadinessScore available

### Invariants
- A `PipelineBottleneck` must have an `observed_at_concurrency` greater than the baseline concurrency (1 agent).
- `MaxSafeQPS` is calculated from the last phase where p95 latency < 3× baseline.
- `PipelineReadinessScore` is `null` until `PipelineTestCompleted` is published.

### Upstream Dependencies
- Simulation Context: `PipelineMetrics` and `AgentRunMetrics`

---

## Context 5: Middleware Assessment Context

### Responsibility
Examines the `SchemaInconsistency` findings, `PipelineBottleneck` patterns, and the `SimulationScenario` to identify which integration and middleware layers are missing from the enterprise landscape. Produces `MiddlewareGap` findings with effort estimates and recommended integration patterns.

### Core Domain Concepts
| Concept | Type | Description |
|---------|------|-------------|
| `MiddlewareAssessmentResult` | Aggregate Root | Container for all middleware findings from one DiagnosticRun |
| `MiddlewareGap` | Entity | A missing integration layer: type, description, effort, affected systems |
| `GapType` | Value Object | Enum: DATA_INTEGRATION, API_GATEWAY, EVENT_STREAMING, IDENTITY_UNIFICATION, SCHEMA_REGISTRY, CACHING_LAYER |
| `IntegrationPattern` | Value Object | Recommended approach to fill the gap |
| `MiddlewareReadinessScore` | Value Object | Sub-score (0–100) for the middleware dimension of ReadinessScore |
| `EffortEstimate` | Value Object | (Shared Kernel) Level + weeks range |

### Published Domain Events
- `MiddlewareGapIdentified` — a specific MiddlewareGap found
- `MiddlewareAssessmentCompleted` — all MiddlewareGaps identified and scored

### Invariants
- Every `MiddlewareGap` must be traceable to at least one `SchemaInconsistency` or `PipelineBottleneck`.
- `MiddlewareReadinessScore` is `null` until `MiddlewareAssessmentCompleted` is published.

### Upstream Dependencies
- Schema Analysis Context: `SchemaInconsistency` findings
- Pipeline Testing Context: `PipelineBottleneck` findings
- Connectivity Context: `ConnectionSet` (which systems are present)
- Simulation Context: `SimulationScenario` (what integration the AI use case requires)

---

## Context 6: Readiness Reporting Context

### Responsibility
Aggregates findings from Schema Analysis, Pipeline Testing, and Middleware Assessment. Calculates the composite `ReadinessScore`, assigns the `ReadinessVerdict`, builds the ordered `RemediationPlan`, and generates the `ReadinessReport` in all output formats (HTML, PDF, JSON).

### Core Domain Concepts
| Concept | Type | Description |
|---------|------|-------------|
| `ReadinessReport` | Aggregate Root | The final deliverable: all findings, score, verdict, remediation plan |
| `ReportSection` | Entity | A logical section of the report |
| `ExecutiveSummary` | Entity | Plain-English narrative for non-technical decision-makers |
| `RemediationPlan` | Entity | Ordered collection of RemediationItems |
| `RemediationItem` | Entity | A specific, actionable remediation task with effort and sequence |
| `ReadinessScore` | Value Object | Composite 0–100 score |
| `ReadinessVerdict` | Value Object | GO / NOT_YET / NOT_READY |
| `HiddenCostEstimate` | Value Object | Estimated monetary cost if gaps not addressed pre-purchase |
| `ReportFormat` | Value Object | Enum: HTML, PDF, JSON |

### Published Domain Events
- `RemediationPlanBuilt` — RemediationItems created and sequenced
- `ReadinessReportGenerated` — final report available in all formats
- `ReadinessScoreCalculated` — composite score and verdict determined

### Domain Service: `RemediationPlannerService`
Sequences `RemediationItem` objects by: critical items first, then by dependency graph (topological sort), then by effort (ascending) within the same priority tier.

### Domain Service: `ReadinessScoreCalculator`
Calculates `ReadinessScore` as the weighted average:
- `SchemaConsistencyScore` × 0.40
- `PipelineReadinessScore` × 0.35
- `MiddlewareReadinessScore` × 0.25

Applies a penalty: any `CRITICAL` severity finding reduces the score by 10 points (capped at 3 penalties), regardless of the weighted average.

### Invariants
- `ReadinessReport` cannot be generated until all three sub-scores are available.
- `ReadinessVerdict` is `NOT_READY` if any `CRITICAL` `SchemaInconsistency` is present, regardless of the numeric score.
- `RemediationPlan` must cover every finding with `SeverityLevel >= MEDIUM`.

### Upstream Dependencies
- Schema Analysis Context: `SchemaInconsistency` list, `SchemaConsistencyScore`
- Pipeline Testing Context: `PipelineBottleneck` list, `PipelineReadinessScore`
- Middleware Assessment Context: `MiddlewareGap` list, `MiddlewareReadinessScore`

---

## Context 7: Scenario Modeling Context

### Responsibility
Provides an interactive what-if analysis environment. Allows buyers to adjust assumptions from a completed `ReadinessReport` — modifying data volumes, system scope, team size — and observe how the projected integration effort and readiness trajectory change. Supports sales-cycle exploration without modifying the authoritative diagnostic record.

### Core Domain Concepts
| Concept | Type | Description |
|---------|------|-------------|
| `ScenarioModel` | Aggregate Root | A what-if exploration based on a completed ReadinessReport |
| `ScenarioAssumption` | Value Object | A tunable parameter: name, current value, range, type |
| `ProjectedIntegrationCost` | Value Object | Effort and cost range given current ScenarioAssumptions |
| `ScenarioTimeline` | Value Object | Week-by-week projected remediation schedule |
| `AssumptionSensitivityScore` | Value Object | How much changing this assumption affects the projected cost |

### Published Domain Events
- `ScenarioModelCreated` — a new ScenarioModel based on a ReadinessReport
- `ScenarioAssumptionUpdated` — a user changed an assumption; new projection calculated
- `ScenarioModelExported` — scenario exported as a shareable snapshot

### Invariants
- `ScenarioModel` is a read-only projection; it does not modify the source `ReadinessReport`.
- `ProjectedIntegrationCost` must always be presented as a range, not a single value.
- Scenario state is ephemeral (session-scoped); it is not persisted to the diagnostic run record.

### Upstream Dependencies (read-only)
- Readiness Reporting Context: `ReadinessReport` (read-only; used as starting state)

---

## Context Boundary Implementation Notes

### How Contexts Communicate
Inter-context communication uses **domain events** published to a Redis Pub/Sub channel and consumed by subscribing contexts. This decouples contexts so each can evolve independently.

```
Schema Analysis Context
    publishes: SchemaAnalysisCompleted
                    │
                    ▼ Redis Pub/Sub: "preflight.events.schema_analysis"
                    │
    consumed by: Middleware Assessment Context
                 Readiness Reporting Context
```

### Anti-Corruption Layer Pattern
Where a downstream context imports data from an upstream context, it applies an ACL (mapper) to translate upstream data types into its own internal model. This prevents upstream model changes from propagating into downstream contexts:

```
Connectivity Context (upstream)           Schema Analysis Context (downstream)
  SchemaSnapshot                    ACL     EntityGraph
    .entities: list[EntityMetadata]  ──▶     .nodes: dict[str, Entity]
    .relationships: list[...]         ──▶     .edges: list[Relationship]
```

### Shared Kernel
The following types are shared directly across all contexts without ACL:
- `SeverityLevel` — `preflight/core/domain/shared/severity.py`
- `EffortEstimate` — `preflight/core/domain/shared/effort.py`
- `DiagnosticRunId` — `preflight/core/domain/shared/identifiers.py`
- `ReadinessScore` — `preflight/core/domain/shared/readiness.py`
- `ReadinessVerdict` — `preflight/core/domain/shared/readiness.py`

Changes to shared kernel types require coordinated updates across all contexts.
