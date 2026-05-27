# ADR-005: Pipeline Stress Testing Approach

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, QA/Performance Lead  
**Technical Story**: [PRD-002 §5.4](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight must stress-test enterprise data pipelines to reveal how they behave under the read query volumes that an AI deployment would generate — without writing a single byte to any production system.

A typical enterprise AI agent deployment generates a fundamentally different query pattern than human users:

- **Volume**: 10x–100x more queries per minute than human users
- **Pattern**: bursts of related queries (an agent "thinking" across multiple systems simultaneously)
- **Concurrency**: many parallel agent instances, each making simultaneous cross-system reads
- **Query shape**: complex analytical queries with broad table scans, not the point-lookup patterns that BI tools generate

Existing enterprise load-testing tools are designed for write-path web applications; they do not model read-only agent query patterns, do not understand enterprise connector protocols, and cannot be safely pointed at production systems without risk.

Key constraints:

- **Absolute safety**: must never write to, modify, or delete any data in any connected system.
- **Realistic simulation**: the stress profile must match actual AI agent query behaviour, not generic HTTP load.
- **System-specific protocols**: different systems require different stress approaches (SQL query volume vs. Salesforce SOQL query rate vs. SAP RFC call frequency).
- **Graceful degradation**: the test must detect and record the breaking point without causing a production incident.
- **Observability**: latency, throughput, error rates, and throttling events must be captured at per-system and per-pipeline granularity.

---

## Decision Drivers

- Safety: zero-write guarantee is non-negotiable
- Realism: simulated load must match actual AI agent query patterns
- Protocol diversity: must support SQL, REST/SOQL, RFC, and JDBC over a single framework
- Breaking-point detection: must find limits without causing outages
- Observability: per-pipeline metrics must feed directly into the `PipelineBottleneck` domain model
- Extensibility: new query patterns can be added for new AI use-case types

---

## Considered Options

### Option A: Read-only load simulation using concurrent async agents (chosen)
### Option B: Locust
### Option C: k6
### Option D: Artillery
### Option E: Synthetic trace replay

---

## Decision Outcome

**Chosen option: Read-only load simulation using concurrent async agents**.

The simulation engine deploys configurable concurrent `DiagnosticAgent` coroutines that execute read-only query sequences against connected systems. Each agent represents a simulated AI agent instance; the orchestrator ramps concurrency according to the configured simulation profile.

### Agent Architecture

```python
class DiagnosticAgent:
    """
    Simulates a single AI agent instance executing read queries.
    
    Each agent runs an execution plan derived from the SimulationScenario:
    - entity_reads: read queries across configured entities
    - cross_system_joins: simulated cross-system lookups
    - analytical_scans: broad table-scan equivalents
    - point_lookups: ID-based record fetches
    """

    connector_set: ConnectionSet
    scenario: SimulationScenario
    metrics_sink: MetricsSink

    async def execute_plan(self) -> AgentRunMetrics:
        for step in self.scenario.execution_plan:
            start = perf_counter()
            try:
                result = await step.execute(self.connector_set)
                self.metrics_sink.record_success(step.id, perf_counter() - start)
            except ThrottleError as e:
                self.metrics_sink.record_throttle(step.id, e)
                await asyncio.sleep(e.retry_after_seconds)
            except TimeoutError:
                self.metrics_sink.record_timeout(step.id, perf_counter() - start)
```

### Concurrency Ramp Profile

```
Phase 1: Baseline (1 agent, 5 min)    — establish normal latency baseline
Phase 2: Ramp-up (1→N agents, 10 min) — N from SimulationScenario.expected_concurrency
Phase 3: Sustained peak (N agents, 15 min)
Phase 4: Spike (2N agents, 5 min)     — detect spike-handling behaviour
Phase 5: Recovery (0 agents, 5 min)   — measure recovery time

Breaking point = first phase where p95 latency > 3× baseline OR error rate > 5%
```

### Pipeline Bottleneck Detection

```python
class PipelineBottleneck(ValueObject):
    pipeline_id: str
    system: str
    bottleneck_type: Literal["throughput", "latency", "throttling", "connection_limit"]
    observed_at_concurrency: int        # agent count when bottleneck appeared
    baseline_p95_latency_ms: float
    breaking_p95_latency_ms: float
    max_safe_qps: float                 # queries/second before degradation
    error_rate_at_breaking_point: float
```

### Positive Consequences

- **Absolute safety**: `DiagnosticAgent` only calls `BaseConnector.execute_read_query()` and `stream_records()`; the zero-write guarantee from ADR-003 applies unconditionally.
- **Realistic patterns**: agent execution plans are derived from the `SimulationScenario` domain object, which the customer configures for their specific AI use case — not a generic HTTP load profile.
- **Protocol-native**: agents interact through the `BaseConnector` abstraction; SQL, SOQL, RFC, and REST pipelines are stressed through their native protocols.
- **Graceful breaking-point detection**: the concurrency ramp profile detects breaking points without sustained overload; the test stops ramping when degradation is detected.
- **Rich observability**: every query result is timestamped and recorded; `PipelineMetrics` aggregations feed directly into the analysis result domain model.
- **Extensible scenarios**: new AI use-case simulation profiles (`customer_service_ai`, `supply_chain_agent`, `financial_reporting_agent`) are configuration, not code.

### Negative Consequences

- Custom simulation is significantly more development effort than adopting an off-the-shelf tool.
- Calibration of what constitutes a "realistic" AI agent query plan requires ongoing refinement as real AI deployment data becomes available.
- The breaking-point detection heuristic (p95 latency > 3× baseline, error rate > 5%) may not be appropriate for all enterprise system types; these thresholds are configurable.

---

## Alternatives Considered

### Option B: Locust

Python-based open-source load testing framework; tasks defined as Python coroutines.

| Criterion | Assessment |
|-----------|-----------|
| Safety | No built-in read-only enforcement; Locust tasks could write if misconfigured |
| Protocol support | HTTP/REST native; enterprise protocols (SAP RFC, JDBC) require custom wrapper tasks |
| Query patterns | Generic HTTP; would not model enterprise SDK connector query patterns |
| Verdict | **Rejected** — protocol mismatch and absence of read-only safety enforcement; would require as much custom code as Option A without the safety guarantee |

### Option C: k6

JavaScript-based load testing tool; excellent HTTP performance.

| Criterion | Assessment |
|-----------|-----------|
| Safety | No enterprise-specific read-only enforcement |
| Language | JavaScript is outside the Python-first stack (ADR-001) |
| Protocol support | HTTP/gRPC only; no enterprise connector protocol support |
| Verdict | **Rejected** — incompatible with enterprise connector protocols and the Python-first architecture |

### Option D: Artillery

Node.js-based; YAML-defined load scenarios; plugin ecosystem.

| Criterion | Assessment |
|-----------|-----------|
| Safety | No read-only enforcement |
| Protocol support | HTTP, WebSocket, Socket.IO; no enterprise protocols |
| Language | Node.js; outside the Python-first stack |
| Verdict | **Rejected** — same protocol and language concerns as k6 |

### Option E: Synthetic Trace Replay

Capture real query traces from a reference customer and replay them at scale.

| Criterion | Assessment |
|-----------|-----------|
| Realism | High for systems that have trace data; requires existing AI deployment to capture from |
| Applicability | Circular: Preflight is run *before* the AI deployment exists; there are no traces |
| Verdict | **Rejected** — not applicable in pre-purchase context; reserved for v2 continuous re-assessment feature |

---

## Implementation Notes

- Simulation orchestration is a Celery task (see ADR-008); each `DiagnosticAgent` is an asyncio coroutine managed by `asyncio.gather()`.
- Metrics are streamed in real time to Redis (see ADR-006) for live dashboard display during the test run.
- The `SimulationScenario` domain object (see DDD domain model) defines: use case type, systems to stress, concurrency target, and duration.
- Breaking-point thresholds are configurable per system type in `config.yml` under `simulation.thresholds.*`.
- Automatic cool-down period between test phases prevents residual load from contaminating the next phase's baseline.

---

## Links

- [ADR-003: Read-Only Connector Architecture](./ADR-003-read-only-connector-architecture.md)
- [ADR-006: Data Storage Architecture](./ADR-006-data-storage-architecture.md)
- [ADR-008: Async Processing Architecture](./ADR-008-async-processing-architecture.md)
- [DDD: Simulation Context](../ddd/bounded-contexts.md)
- [DDD: Domain Model — PipelineBottleneck](../ddd/domain-model.md)
