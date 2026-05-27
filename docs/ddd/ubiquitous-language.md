# Ubiquitous Language Glossary — Preflight Integration Tester

This glossary defines every domain term used in Preflight's codebase, documentation, and team communication. All engineers, product managers, and domain experts must use these terms consistently. When a new concept is introduced, it must be added here before being used in code.

Terms are grouped by their primary bounded context. Terms in the **Shared** section appear across multiple contexts.

---

## Shared / Cross-Context Terms

### DiagnosticRun
The top-level aggregate representing a single end-to-end readiness assessment session. A DiagnosticRun has a lifecycle (PENDING → CONNECTING → SIMULATING → ANALYSING → REPORTING → COMPLETED or FAILED), an associated `ConnectionSet`, `SimulationScenario`, `AnalysisResults`, and ultimately produces a `ReadinessReport`. All findings, scores, and remediation items belong to exactly one DiagnosticRun.

**Code**: `DiagnosticRun` aggregate in `preflight/core/domain/`  
**NOT synonymous with**: "job", "task", "session", or "run" (use DiagnosticRun precisely)

---

### ReadinessScore
A composite numeric score from **0 to 100** representing how prepared the assessed enterprise systems are to support the specified AI deployment. It is a **value object** — immutable, calculated from weighted sub-scores across schema consistency, pipeline performance, and middleware readiness. A score alone is not actionable; it must be interpreted alongside the `ReadinessVerdict` and `RemediationPlan`.

**Calculation**: weighted average of `SchemaConsistencyScore` (40%), `PipelineReadinessScore` (35%), `MiddlewareReadinessScore` (25%).

**Code**: `ReadinessScore` value object  
**NOT synonymous with**: "grade", "rating", "percentage"

---

### ReadinessVerdict
A categorical assessment derived from the `ReadinessScore` and qualitative analysis. One of three values:

| Verdict | Score Range | Meaning |
|---------|-------------|---------|
| `GO` | 80–100 | Systems are sufficiently ready; AI deployment is likely to succeed with manageable integration work |
| `NOT_YET` | 50–79 | Significant integration gaps exist; remediation work required before deployment can succeed |
| `NOT_READY` | 0–49 | Critical structural issues that would cause AI deployment to fail; substantial remediation required |

**Code**: `ReadinessVerdict` enum  
**NOT synonymous with**: "status", "result", "outcome"

---

### SeverityLevel
A classification of how critically an identified issue would impact the proposed AI deployment. One of:
- `CRITICAL`: would cause complete AI deployment failure
- `HIGH`: would cause significant degradation or failure for core use cases
- `MEDIUM`: would cause partial failure or workarounds required
- `LOW`: minor issues; deployment succeeds but with suboptimal behaviour

**Code**: `SeverityLevel` enum (shared kernel)

---

### EffortEstimate
A rough estimate of the engineering effort required to remediate a specific gap. A value object with two fields: `level` (LOW / MEDIUM / HIGH / WEEKS) and `weeks_range` (e.g., `(2, 4)` for "2–4 weeks"). Effort is always expressed as a range to communicate uncertainty.

**Code**: `EffortEstimate` value object (shared kernel)

---

### RemediationItem
A specific, actionable task identified from the diagnostic findings. Each item has: a title, description, `SeverityLevel`, `EffortEstimate`, affected systems, and recommended sequencing. Together, all RemediationItems for a run constitute the `RemediationPlan`.

**Code**: `RemediationItem` entity  
**NOT synonymous with**: "ticket", "issue", "task" — RemediationItem is the domain term

---

### RemediationPlan
The ordered collection of `RemediationItem` objects for a DiagnosticRun, sequenced by the domain service to minimise dependencies and maximise delivery speed. The plan is the primary engineering output of the diagnostic.

---

### ConnectorProfile (also: ConnectionProfile)
A record of a successfully established read-only connection to a specific enterprise system for a DiagnosticRun. Contains the system type, endpoint, connection status, and a reference to the `SchemaSnapshot` captured during introspection. The `ConnectionCredentials` used to establish it are never stored in the profile.

**Code**: `ConnectionProfile` aggregate  
**NOT synonymous with**: "integration", "adapter", "link"

---

## Connectivity Context Terms

### ConnectionCredentials
A value object holding the authentication material required to connect to a specific enterprise system. Fields use `SecretStr` to prevent accidental logging. Credentials are provided by the customer, held in memory only for the duration of the diagnostic run, and never persisted.

**Subtypes**: `UsernamePasswordCredentials`, `ApiKeyCredentials`, `OAuth2Credentials`, `JdbcCredentials`

---

### ConnectionStatus
The lifecycle state of a `ConnectionProfile`:
- `PENDING` — not yet attempted
- `CONNECTING` — connection attempt in progress
- `CONNECTED` — successfully authenticated; schema introspection available
- `DEGRADED` — connected but with reduced access (rate-limited, partial schema)
- `FAILED` — connection could not be established
- `DISCONNECTED` — was connected; cleanly disconnected after use

---

### SchemaSnapshot
An immutable point-in-time capture of the entity/field metadata available through a connected system. Contains all discovered entities, their fields, data types, constraints, and relationships. SchemaSnapshots are cached in Redis to avoid re-introspecting unchanged systems.

**Code**: `SchemaSnapshot` frozen Pydantic model  
**NOT synonymous with**: "schema", "metadata", "data dictionary"

---

### EntityMetadata
The description of a single entity (table, object, API resource) within a `SchemaSnapshot`. Contains the entity name, entity type (record, dimension, fact, document), field list, estimated row count, and relationship references.

---

### FieldMetadata
The description of a single field within an `EntityMetadata`. Contains: field name, data type, nullability, uniqueness constraint, whether it appears to be a primary key, foreign key references, and any value format hints.

---

### ConnectorWriteAttemptError
A domain-level exception raised if any connector call attempts a write operation. This is a safety invariant, not a user error — it indicates a bug in a connector implementation.

---

### SystemType
The identifier for a class of enterprise system. Examples: `"salesforce"`, `"sap_s4hana"`, `"oracle_ebs"`, `"snowflake"`, `"postgresql"`, `"microsoft_dynamics_365"`. Used as the key in the connector registry.

---

## Simulation Context Terms

### SimulationScenario
The customer-provided description of the AI deployment being assessed. Includes: use-case type, which systems the AI agents will access, expected query volume (queries per second), expected concurrency (simultaneous agent instances), and typical query patterns. The SimulationScenario drives both the stress-test profile and the impact weighting in the readiness score.

**Code**: `SimulationScenario` entity  
**NOT synonymous with**: "test case", "configuration", "setup"

---

### DiagnosticAgent
A simulated AI agent instance executing read-only queries against connected systems as part of the load simulation. Multiple DiagnosticAgents run concurrently to simulate the concurrent access patterns of a real AI deployment. Each agent executes an `ExecutionPlan` derived from the `SimulationScenario`.

**Code**: `DiagnosticAgent` class in the Simulation context  
**NOT synonymous with**: "bot", "worker", "thread"

---

### ExecutionPlan
The sequence of read operations a `DiagnosticAgent` will perform during a simulation run. Generated from the `SimulationScenario` and the `SchemaSnapshot`s of connected systems. Includes entity reads, cross-system lookups, and analytical scans.

---

### ConcurrencyProfile
The schedule of DiagnosticAgent counts over time during a simulation: baseline agents, ramp-up rate, peak agent count, spike count, and recovery period. Defines the shape of the stress test.

---

### AgentRunMetrics
The performance measurements captured by a single `DiagnosticAgent` during its execution: query latencies, success/failure counts, throttle events, and timeout events. Aggregated across all agents to produce `PipelineMetrics`.

---

## Schema Analysis Context Terms

### EntityMapping
A proposed correspondence between an entity in one system and an entity in another system. Produced by the schema analysis engine with a `match_confidence` score (0.0–1.0) and an `evidence` list explaining the contributing signals. The mapping is not binary — it expresses probabilistic correspondence.

**Code**: `EntityMapping` value object

---

### MatchConfidence
A numeric score (0.0 to 1.0) indicating how certain the schema analysis engine is that two entities represent the same business concept. Composed from weighted signals: name similarity, field-set overlap, cardinality similarity, field-type distribution, and semantic role alignment.

---

### SchemaInconsistency
A documented difference between corresponding entities or fields across two or more connected systems. Has a type (`key_type_mismatch`, `nullability_difference`, `value_format_mismatch`, `cardinality_divergence`, `missing_field`), `SeverityLevel`, and `ImpactScore` (0–100) indicating how severely this inconsistency would break the proposed AI deployment.

**Code**: `SchemaInconsistency` entity

---

### EntityGraph
A directed graph representing entities and their relationships within a single enterprise system. Nodes are entities; edges are relationships (foreign keys, joins, references). Used internally by the schema analysis engine for graph-based entity matching.

---

### CrossSystemGraph
A bipartite graph connecting matched entities across multiple enterprise systems. Edge weights represent `MatchConfidence`. Used to identify clusters of entities that represent the same business concept (e.g., "Customer" appears in 4 systems with different names and structures).

---

### ImpactScore
A numeric score (0–100) indicating how much a specific `SchemaInconsistency` or `PipelineBottleneck` would degrade the proposed AI deployment. Accounts for: how central the affected entity is to the use case, how many systems are affected, and the severity of the divergence.

---

## Pipeline Testing Context Terms

### PipelineBottleneck
A documented performance constraint discovered during stress testing. Specifies: the affected system, the bottleneck type (`throughput`, `latency`, `throttling`, `connection_limit`), the concurrency level at which the bottleneck appeared, the baseline and breaking-point latency, and the maximum safe queries-per-second.

**Code**: `PipelineBottleneck` entity

---

### PipelineMetrics
The aggregated time-series performance data collected during the simulation. Contains per-system and per-entity latency percentiles (p50, p95, p99), throughput (queries/second), error rates, and throttle event counts.

---

### BreakingPoint
The specific concurrency level (number of simultaneous `DiagnosticAgent` instances) at which a system's performance degrades beyond acceptable thresholds. Defined as: p95 latency exceeds 3× baseline, or error rate exceeds 5%.

---

### BaselineLatency
The median query latency measured during the baseline phase of the stress test (single DiagnosticAgent, no concurrency). Used as the reference point for detecting degradation at higher concurrency.

---

### ThrottleEvent
A rate-limiting response received from an enterprise system during simulation. Logged with timestamp, affected system, query type, and retry-after duration. High throttle-event rates indicate that the system's API limits are incompatible with the proposed AI agent concurrency.

---

## Middleware Assessment Context Terms

### MiddlewareGap
An integration or middleware layer that the proposed AI deployment would require but that does not currently exist in the enterprise landscape. Has a description, `GapType`, `SeverityLevel`, `EffortEstimate`, and affected systems.

**Code**: `MiddlewareGap` entity

---

### GapType
The category of missing middleware:
- `DATA_INTEGRATION` — missing ETL/ELT pipeline to synchronise data between systems
- `API_GATEWAY` — no unified API layer for cross-system agent access
- `EVENT_STREAMING` — no event bus for real-time data propagation
- `IDENTITY_UNIFICATION` — no cross-system identity resolution service
- `SCHEMA_REGISTRY` — no canonical schema for cross-system entity definitions
- `CACHING_LAYER` — no intermediate cache for high-volume AI agent reads

---

### IntegrationPattern
The recommended integration approach to resolve a `MiddlewareGap`. Examples: "CDC pipeline (Debezium)", "REST API gateway (Kong)", "Kafka event bus", "MDM identity service".

---

## Readiness Reporting Context Terms

### ReadinessReport
The final deliverable of a DiagnosticRun. Contains: `ReadinessScore`, `ReadinessVerdict`, all `SchemaInconsistency` findings, all `PipelineBottleneck` findings, all `MiddlewareGap` findings, the `RemediationPlan`, an executive summary, and metadata. Produced in HTML, PDF, and JSON formats.

**Code**: `ReadinessReport` entity

---

### ReportSection
A logical section of the `ReadinessReport`. Each section corresponds to a specific analysis domain: executive summary, schema analysis, pipeline analysis, middleware analysis, remediation plan. Sections can be included or excluded based on customer configuration.

---

### ExecutiveSummary
The first section of a `ReadinessReport`, written in plain English for non-technical decision-makers. Contains: the `ReadinessVerdict` with rationale, the top 3 critical issues, estimated remediation timeline, and estimated hidden integration cost if the issues are discovered mid-pilot.

---

### HiddenCostEstimate
A monetary estimate of the integration work that would need to be performed if the gaps discovered by Preflight are not addressed before the AI deployment begins. Calculated from `EffortEstimate` ranges for all `RemediationItem` objects, multiplied by a configurable blended hourly rate. Expressed as a range to communicate uncertainty.

---

## Scenario Modeling Context Terms

### ScenarioModel
An interactive what-if model that allows buyers to adjust assumptions (data volume, system count, agent concurrency, use-case scope) and observe how the projected integration effort and readiness score change. Built on the `SimulationScenario` and `AnalysisResults` from a completed DiagnosticRun.

---

### ScenarioAssumption
A tunable parameter in the `ScenarioModel`. Examples: `expected_qps`, `agent_concurrency`, `systems_in_scope`, `integration_team_size`.

---

### ProjectedIntegrationCost
The estimated total cost (in engineering weeks and dollars) to bring the assessed systems from their current state to AI-deployment-ready, given the current `ScenarioAssumption` values.

---

## Process and Lifecycle Terms

### Pilot Purgatory
The business problem Preflight solves: the pattern where enterprises buy AI software, fail to get it into production, and never identify the root cause. Pilot purgatory is caused by hidden schema inconsistencies, pipeline fragility, and unbudgeted middleware discovered mid-pilot rather than pre-purchase.

*This term should appear in customer-facing documentation and sales materials. It is NOT a technical term; do not use it in code.*

---

### Pre-Purchase Assessment
The primary use-case context for a DiagnosticRun: performed before signing an AI software contract, to determine readiness and identify the true cost of deployment. Distinguished from a `Post-Purchase Assessment` (assessing after contract signing, typically to scope remediation work).

---

### Remediation Sequence
The recommended order in which `RemediationItem` objects should be addressed, accounting for dependencies between items. Calculated by the `RemediationPlannerService` using topological sort on the dependency graph.

---

### Read-Only Guarantee
The core safety promise of Preflight: the system will never write to, modify, or delete any data in any connected enterprise system. Enforced at the connector interface level (ADR-003) and the database credential level (ADR-007).

---

## Technical Domain Terms

### Anti-Corruption Layer (ACL)
An adapter layer that translates between an external system's data model and Preflight's domain model. Each connector implements an ACL that converts vendor-specific API responses into domain value objects (`EntityMetadata`, `FieldMetadata`, `QueryResult`). This isolates the domain model from external system conventions.

---

### Shared Kernel
Domain concepts shared across multiple bounded contexts without duplication. In Preflight, the shared kernel includes: `SeverityLevel`, `EffortEstimate`, `ReadinessScore`, `ReadinessVerdict`, and the `DiagnosticRunId`. Changes to shared kernel types require coordination across all consuming contexts.

---

### Aggregate Boundary
The consistency boundary around an aggregate. Within a DiagnosticRun boundary, all state changes are transactional; no invariant within the aggregate can be broken by a partial update. Across aggregate boundaries, consistency is eventual (via domain events).

---

*Last updated: 2026-05-27. To add or modify terms, submit a pull request with the change and a rationale. All team members must be consulted on changes to existing terms, as renaming a term has implications throughout the codebase.*
