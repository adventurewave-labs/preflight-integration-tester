# ADR-008: Async Processing Architecture

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, Backend Team  
**Technical Story**: [PRD-002 §5.2, §5.4](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

A full Preflight diagnostic run involves:

1. Connecting to 3–10 enterprise systems (parallel; seconds to minutes each)
2. Introspecting schemas across all systems (parallel; minutes each for large schemas)
3. Running concurrent stress-test agent simulation (15–40 minutes)
4. Graph-based schema consistency analysis (minutes)
5. Middleware gap assessment (minutes)
6. Report generation and scoring (seconds to minutes)

**Total wall-clock time: 30 minutes to several hours** depending on schema complexity, system count, and simulation duration.

This is fundamentally incompatible with the HTTP request/response model. A FastAPI endpoint cannot hold a connection open for hours while a diagnostic run completes. The architecture needs:

- **Non-blocking job submission**: the API returns immediately with a job ID; the diagnostic run executes in the background.
- **Progress tracking**: the React dashboard and CLI must be able to poll or stream real-time progress (phase completion, current step, partial results).
- **Retry logic**: enterprise system connections fail transiently; individual phases must retry without restarting the entire run.
- **Horizontal scaling**: multiple concurrent diagnostic runs must scale by adding worker capacity.
- **Durability**: a worker crash mid-run should not lose the work completed so far.
- **Timeouts and cancellation**: customers must be able to cancel a running diagnostic; long-running steps must time out rather than hang indefinitely.

---

## Decision Drivers

- Decoupling of API response time from job duration
- Real-time progress visibility during multi-hour runs
- Reliable retry and error handling for transient connector failures
- Horizontal scalability of worker capacity
- Durability: no work lost on worker crash
- Operational simplicity in VPC deployments
- Compatibility with Redis (already required for cache — ADR-006)

---

## Considered Options

### Option A: Celery + Redis (chosen)
### Option B: FastAPI BackgroundTasks only
### Option C: asyncio only (in-process)
### Option D: Apache Kafka + custom consumer workers

---

## Decision Outcome

**Chosen option: Celery with Redis as broker and result backend**.

### Architecture

```
HTTP Request                    Redis Broker                    Celery Worker
──────────────                  ──────────────                  ──────────────
POST /api/v1/diagnostics/run    
        │                       
        ▼                       
FastAPI route handler           
  └── DiagnosticService         
        .submit_run(scenario)   
              │                 
              ▼                 
        run_diagnostic.delay()  ──── enqueue task ──────────▶  Worker picks up
              │                                                  task
              ▼                                                       │
        Returns RunId          ◀─────── Redis job:progress ◀────── Progress updates
                                                                      │
GET /api/v1/diagnostics/{id}                                          ▼
        │                                                       Phase 1: Connect
        ▼                                                       Phase 2: Introspect
  Progress: 35% — Introspecting                                 Phase 3: Simulate
  schemas across 4 systems                                      Phase 4: Analyse
                                                                Phase 5: Report
                                                                       │
                                                                       ▼
                                                               Write results to
                                                               PostgreSQL
```

### Task Structure

```python
# preflight/core/application/tasks.py

@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=14400,   # 4-hour soft limit: raises SoftTimeLimitExceeded
    time_limit=14700,         # 4h5m hard kill
    acks_late=True,           # Don't ack until task completes (durability)
)
def run_diagnostic(self, run_id: str, scenario_config: dict) -> dict:
    """
    Orchestrates a full diagnostic run as a series of sub-tasks.
    Progress is written to Redis at each phase boundary.
    """
    progress = ProgressTracker(run_id)
    
    try:
        progress.update(phase="connecting", pct=5)
        connection_set = _connect_all_systems(scenario_config)
        
        progress.update(phase="introspecting", pct=15)
        schemas = _introspect_all_schemas(connection_set)
        
        progress.update(phase="simulating", pct=30)
        pipeline_metrics = _run_simulation(connection_set, scenario_config)
        
        progress.update(phase="analysing_schema", pct=65)
        schema_results = _analyse_schema_consistency(schemas)
        
        progress.update(phase="assessing_middleware", pct=80)
        middleware_gaps = _assess_middleware_gaps(schemas, scenario_config)
        
        progress.update(phase="generating_report", pct=90)
        report = _generate_report(schema_results, pipeline_metrics, middleware_gaps)
        
        progress.update(phase="complete", pct=100)
        return {"run_id": run_id, "report_id": report.id}
        
    except ConnectorConnectionError as exc:
        raise self.retry(exc=exc, countdown=60)
    except SoftTimeLimitExceeded:
        progress.update(phase="timeout_error", pct=None)
        raise
```

### Progress Tracking

```python
class ProgressTracker:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.redis = get_redis_client()
    
    def update(self, phase: str, pct: int | None, detail: str = ""):
        payload = {
            "run_id": self.run_id,
            "phase": phase,
            "pct_complete": pct,
            "detail": detail,
            "updated_at": datetime.utcnow().isoformat()
        }
        self.redis.setex(f"job:{self.run_id}:progress", 14400, json.dumps(payload))
        # Also publish to pub/sub for WebSocket streaming
        self.redis.publish(f"run:{self.run_id}:events", json.dumps(payload))
```

### WebSocket Live Progress

```python
# FastAPI WebSocket endpoint for live progress streaming
@router.websocket("/api/v1/diagnostics/{run_id}/progress")
async def diagnostic_progress_ws(websocket: WebSocket, run_id: str):
    await websocket.accept()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"run:{run_id}:events")
    async for message in pubsub.listen():
        if message["type"] == "message":
            await websocket.send_text(message["data"])
```

### Positive Consequences

- **Immediate API response**: `POST /diagnostics/run` returns in milliseconds with a `run_id`; no blocking.
- **Reliable retry**: transient connector failures retry automatically with exponential backoff; the customer does not need to restart the run.
- **Durable task processing**: `acks_late=True` ensures tasks are not lost if a worker crashes mid-execution.
- **Horizontal scaling**: additional Celery workers can be added to handle concurrent diagnostic runs by increasing the K8s replica count.
- **Real-time progress**: WebSocket streaming via Redis Pub/Sub provides live phase updates to the React dashboard.
- **Monitoring**: Flower (Celery monitoring UI) and Prometheus metrics (`celery_task_received`, `celery_task_succeeded`, `celery_task_failed`) are available out of the box.
- **Redis reuse**: Celery uses the same Redis instance already required for schema caching (ADR-006); no additional infrastructure.

### Negative Consequences

- Celery adds operational complexity: broker health, worker scaling, and task serialisation must be managed.
- Celery's default task serialiser (JSON) requires all task arguments to be JSON-serialisable; domain objects must be serialised to dict before enqueueing.
- `acks_late=True` means a crashed worker may re-execute a task; phases must be designed to be idempotent (re-introspecting a schema produces the same result).
- Celery Beat (scheduler) would be required for future scheduled re-assessments (v2 feature).

---

## Alternatives Considered

### Option B: FastAPI BackgroundTasks Only

FastAPI's built-in `BackgroundTasks` runs tasks as asyncio coroutines within the same process as the API server.

| Criterion | Assessment |
|-----------|-----------|
| Simplicity | High — no additional infrastructure |
| Durability | None — task lost if API process restarts |
| Retry logic | None built-in |
| Scaling | Not horizontally scalable; bound to the API process |
| Suitability | Appropriate for tasks <30 seconds; diagnostic runs take hours |
| Verdict | **Rejected** — inadequate for multi-hour, multi-phase runs requiring durability and retry |

### Option C: asyncio Only (In-Process)

All diagnostic phases run as asyncio coroutines within the FastAPI process, managed by a custom coroutine scheduler.

| Criterion | Assessment |
|-----------|-----------|
| Infrastructure | None required |
| Durability | None — lost on process restart |
| Scaling | Process-bound; CPU-bound analysis phases would block the event loop |
| Progress | Achievable but requires custom implementation |
| Verdict | **Rejected** — CPU-bound analysis phases (graph computation) block the asyncio event loop; no durability |

### Option D: Apache Kafka + Custom Consumer Workers

Kafka as a durable event streaming platform; diagnostic phases publish/consume events.

| Criterion | Assessment |
|-----------|-----------|
| Durability | Excellent — log-based persistence |
| Complexity | Very high — Kafka cluster management in VPC deployments |
| Operational overhead | Zookeeper/KRaft, topic management, consumer group coordination |
| Fit for purpose | Kafka is designed for high-throughput event streams; Preflight needs a job queue with retry and progress tracking |
| Verdict | **Rejected** — significant operational overhead for a use case that Celery handles with far less complexity |

---

## Implementation Notes

- Celery app configured in `preflight/core/infrastructure/celery_app.py`.
- Task serialiser: JSON with custom `DatetimeEncoder`.
- Worker concurrency: `--concurrency=4` per pod (I/O-bound tasks; higher concurrency acceptable); configurable in K8s deployment manifest.
- Flower dashboard exposed on port 5555 in development; protected behind internal network policy in production.
- Celery result backend: Redis with TTL of 48 hours (results written to PostgreSQL before TTL expires).
- Canvas (Celery chord/chain) used to run schema introspection across all connectors in parallel before analysis begins.

---

## Links

- [ADR-001: Core Language and Runtime](./ADR-001-core-language-and-runtime.md)
- [ADR-002: API Framework Selection](./ADR-002-api-framework-selection.md)
- [ADR-006: Data Storage Architecture](./ADR-006-data-storage-architecture.md)
- [DDD: Application Services](../ddd/application-services.md)
- [DDD: Domain Events](../ddd/domain-events.md)
