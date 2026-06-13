# Application Services — Preflight Integration Tester

This document defines the application services that coordinate domain objects to implement Preflight's use cases. Application services live in the application layer; they orchestrate domain aggregates, repositories, domain services, and event publishing. They do not contain business logic — that lives in the domain layer.

---

## Application Service Design Principles

1. **One method per use case**: each public method implements one and only one application-level use case.
2. **Thin orchestration**: application services delegate all business logic to domain objects; they provide no logic themselves.
3. **Transactional boundary**: each service method is the transaction boundary; it opens a `UnitOfWork`, calls domain methods, commits, and dispatches events.
4. **DTO in, DTO out**: service methods accept command/query DTOs and return response DTOs; they never expose domain objects to the interface layer.
5. **No cross-service calls**: application services do not call other application services; they call domain services and repositories only.
6. **Event publishing after commit**: domain events are published to Redis after the database transaction commits; never before.

---

## DiagnosticService

**Location**: `preflight/core/application/diagnostic_service.py`

The primary application service. Orchestrates the full lifecycle of a `DiagnosticRun`, from submission through completion.

```python
class DiagnosticService:
    """
    Application service for DiagnosticRun lifecycle management.
    
    Coordinates: DiagnosticRunRepository, ConnectionProfileRepository,
                 AnalysisResultRepository, UnitOfWork, Celery task dispatch,
                 and domain event publishing.
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        celery_app: Celery,
        event_publisher: DomainEventPublisher,
        progress_repo: DiagnosticRunProgressRepository,
    ): ...
```

### `submit_diagnostic_run`

**Use case**: A customer submits a new diagnostic run configuration.  
**Command**: `SubmitDiagnosticRunCommand`  
**Response**: `DiagnosticRunSubmittedResponse`

```python
async def submit_diagnostic_run(
    self,
    command: SubmitDiagnosticRunCommand,
) -> DiagnosticRunSubmittedResponse:
    """
    Creates a new DiagnosticRun, validates the connection configuration,
    and enqueues the background Celery task.
    
    Steps:
    1. Validate that system_types in the command are all supported (connector registry)
    2. Create DiagnosticRun aggregate (status=PENDING)
    3. Create ConnectionProfile for each system
    4. Create SimulationScenario from command parameters
    5. Persist via UnitOfWork
    6. Enqueue run_diagnostic Celery task with run_id
    7. Publish DiagnosticRunStarted domain event
    8. Return DiagnosticRunSubmittedResponse with run_id and polling URL
    
    Raises:
        UnsupportedSystemTypeError: if any system_type has no registered connector
        InvalidSimulationScenarioError: if the scenario parameters are invalid
    """
```

**Command schema**:

```python
class SubmitDiagnosticRunCommand(BaseModel):
    customer_name: str
    use_case_type: UseCaseType
    use_case_description: str
    systems: list[SystemConnectionConfig]   # list of system_type + endpoint + credentials
    expected_qps: float                     # Expected AI agent queries per second
    expected_concurrency: int               # Expected simultaneous agent instances
    max_simulation_duration_minutes: int = 40
    include_scenario_modeling: bool = True

class SystemConnectionConfig(BaseModel):
    system_type: SystemType
    display_name: str
    endpoint: str
    credentials: ConnectionCredentials      # SecretStr fields; never persisted
```

---

### `get_diagnostic_run`

**Use case**: Retrieve the current state and findings of a DiagnosticRun.  
**Query**: `GetDiagnosticRunQuery`  
**Response**: `DiagnosticRunDetailResponse`

```python
async def get_diagnostic_run(
    self,
    query: GetDiagnosticRunQuery,
) -> DiagnosticRunDetailResponse:
    """
    Retrieves a DiagnosticRun with its current status, progress, and any
    available findings. May be called at any point during the run lifecycle.
    
    Steps:
    1. Load DiagnosticRun from repository
    2. Load current progress from DiagnosticRunProgressRepository (Redis)
    3. Load AnalysisResult if available (may be partial)
    4. Load ReadinessReport if available
    5. Map to DiagnosticRunDetailResponse DTO
    
    Raises:
        DiagnosticRunNotFoundError: if run_id does not exist
        PermissionError: if caller does not have access to this run
    """
```

---

### `cancel_diagnostic_run`

**Use case**: Cancel an in-progress DiagnosticRun.  
**Command**: `CancelDiagnosticRunCommand`  
**Response**: `DiagnosticRunCancelledResponse`

```python
async def cancel_diagnostic_run(
    self,
    command: CancelDiagnosticRunCommand,
) -> DiagnosticRunCancelledResponse:
    """
    Cancels a running diagnostic. Revokes the Celery task, updates status
    to CANCELLED, and publishes DiagnosticRunCancelled event.
    
    Raises:
        DiagnosticRunNotFoundError: if run_id does not exist
        InvalidStatusTransitionError: if run is already COMPLETED or FAILED
    """
```

---

### `list_diagnostic_runs`

**Use case**: List DiagnosticRuns with optional filtering.  
**Query**: `ListDiagnosticRunsQuery`  
**Response**: `list[DiagnosticRunSummaryResponse]`

```python
async def list_diagnostic_runs(
    self,
    query: ListDiagnosticRunsQuery,
) -> list[DiagnosticRunSummaryResponse]:
    """
    Returns a paginated list of DiagnosticRuns, optionally filtered by
    status or customer_name. Ordered by created_at descending.
    """
```

---

## SchemaAnalysisService

**Location**: `preflight/core/application/schema_analysis_service.py`

Orchestrates the schema consistency analysis phase of a DiagnosticRun.

```python
class SchemaAnalysisService:
    """
    Application service for schema consistency analysis.
    
    Coordinates: SchemaSnapshotCacheRepository, AnalysisResultRepository,
                 EntityGraphBuilder (domain service), SchemaMatchingEngine (domain service),
                 InconsistencyDetector (domain service), UnitOfWork, event publishing.
    """
```

### `run_schema_analysis`

**Use case**: Execute schema consistency analysis for a DiagnosticRun.  
**Command**: `RunSchemaAnalysisCommand`  
**Response**: `SchemaAnalysisCompletedResponse`

```python
async def run_schema_analysis(
    self,
    command: RunSchemaAnalysisCommand,
) -> SchemaAnalysisCompletedResponse:
    """
    Executes the full schema analysis pipeline for a DiagnosticRun.
    Called by the Celery task during the ANALYSING phase.
    
    Steps:
    1. Load SchemaSnapshots from cache for all ConnectionProfiles in the run
    2. Build EntityGraph for each system (EntityGraphBuilder domain service)
    3. Build CrossSystemGraph via weighted fuzzy matching (SchemaMatchingEngine)
    4. Detect SchemaInconsistencies on matched entity pairs (InconsistencyDetector)
    5. Calculate ImpactScore for each inconsistency against the SimulationScenario
    6. Rank inconsistencies by impact_score descending
    7. Calculate SchemaConsistencyScore (domain service)
    8. Persist all findings via AnalysisResultRepository (streaming writes)
    9. Publish SchemaAnalysisCompleted event
    10. Return summary response
    
    Raises:
        SchemaSnapshotNotFoundError: if any connection's snapshot is missing from cache
        InsufficientConnectionsError: if fewer than 2 systems are available for comparison
    """
```

**Command schema**:

```python
class RunSchemaAnalysisCommand(BaseModel):
    run_id: UUID
    analysis_id: UUID
    connection_ids: list[UUID]              # Connections to analyse
    matching_threshold: float = 0.65       # Minimum MatchConfidence to consider
    scenario_id: UUID                       # SimulationScenario for impact weighting
```

---

### `get_schema_analysis_results`

**Use case**: Retrieve schema inconsistency findings for a completed run.  
**Query**: `GetSchemaAnalysisResultsQuery`  
**Response**: `SchemaAnalysisResultsResponse`

```python
async def get_schema_analysis_results(
    self,
    query: GetSchemaAnalysisResultsQuery,
) -> SchemaAnalysisResultsResponse:
    """
    Retrieves schema inconsistency findings, optionally filtered by minimum severity
    and paginated.
    
    Returns findings ordered by impact_score descending.
    """
```

---

### `get_entity_mapping`

**Use case**: Retrieve the cross-system entity mapping for inspection or export.  
**Query**: `GetEntityMappingQuery`  
**Response**: `EntityMappingResponse`

```python
async def get_entity_mapping(
    self,
    query: GetEntityMappingQuery,
) -> EntityMappingResponse:
    """
    Returns the full entity mapping matrix for a completed run:
    for each pair of connected systems, the matched entities, their
    confidence scores, and any detected inconsistencies.
    """
```

---

## PipelineTestService

**Location**: `preflight/core/application/pipeline_test_service.py`

Orchestrates the pipeline stress test simulation and bottleneck analysis.

```python
class PipelineTestService:
    """
    Application service for pipeline stress testing.
    
    Coordinates: SimulationOrchestrator (domain service), ConnectionProfileRepository,
                 AnalysisResultRepository, DiagnosticRunProgressRepository,
                 UnitOfWork, event publishing.
    """
```

### `run_pipeline_stress_test`

**Use case**: Execute the read-only load simulation and analyse results.  
**Command**: `RunPipelineStressTestCommand`  
**Response**: `PipelineTestCompletedResponse`

```python
async def run_pipeline_stress_test(
    self,
    command: RunPipelineStressTestCommand,
) -> PipelineTestCompletedResponse:
    """
    Executes the full pipeline stress test for a DiagnosticRun.
    Called by the Celery task during the SIMULATING phase.
    
    Steps:
    1. Load ConnectionSet from ConnectionProfileRepository
    2. Load SimulationScenario from DiagnosticRunRepository
    3. Build ExecutionPlan from scenario and SchemaSnapshots
       (EntityCoverageSelector domain service selects representative queries)
    4. Construct ConcurrencyProfile from SimulationScenario parameters
    5. Execute simulation via SimulationOrchestrator:
       a. Baseline phase: 1 agent, 5 minutes
       b. Ramp-up phase: 1→N agents, 10 minutes
       c. Sustained peak: N agents, 15 minutes
       d. Spike phase: 2N agents, 5 minutes
       e. Recovery phase: 0 agents, 5 minutes
    6. Update progress in DiagnosticRunProgressRepository at each phase
    7. Detect BreakingPoints from phase-over-phase latency comparison
    8. Publish BreakingPointDetected events as breaking points are found
    9. Aggregate PipelineMetrics across all agents
    10. Identify PipelineBottlenecks (PipelineBottleneckAnalyser domain service)
    11. Calculate PipelineReadinessScore
    12. Persist findings via AnalysisResultRepository
    13. Publish PipelineTestCompleted event
    14. Return summary response
    
    Raises:
        SimulationTimeoutError: if any phase exceeds its time limit
        AllConnectionsFailedError: if no connections are available for simulation
    """
```

**Command schema**:

```python
class RunPipelineStressTestCommand(BaseModel):
    run_id: UUID
    analysis_id: UUID
    connection_ids: list[UUID]
    scenario_id: UUID
    max_duration_minutes: int = 40
    breaking_point_latency_multiplier: float = 3.0  # p95 > baseline × this
    breaking_point_error_rate: float = 0.05         # 5% error rate threshold
```

---

### `get_pipeline_test_results`

**Use case**: Retrieve pipeline bottleneck findings and metrics for a completed run.

```python
async def get_pipeline_test_results(
    self,
    query: GetPipelineTestResultsQuery,
) -> PipelineTestResultsResponse:
    """
    Returns all PipelineBottlenecks ordered by severity and impact.
    Also returns PipelineMetrics summary (p50/p95/p99 by system and entity).
    """
```

---

## ReportService

**Location**: `preflight/core/application/report_service.py`

Orchestrates the generation and retrieval of `ReadinessReport` objects.

```python
class ReportService:
    """
    Application service for readiness report generation and retrieval.
    
    Coordinates: AnalysisResultRepository, ReadinessReportRepository,
                 ReadinessScoreCalculator (domain service),
                 RemediationPlannerService (domain service),
                 ReportRenderer (infrastructure service), UnitOfWork, event publishing.
    """
```

### `generate_readiness_report`

**Use case**: Generate the final ReadinessReport for a completed analysis.  
**Command**: `GenerateReadinessReportCommand`  
**Response**: `ReadinessReportGeneratedResponse`

```python
async def generate_readiness_report(
    self,
    command: GenerateReadinessReportCommand,
) -> ReadinessReportGeneratedResponse:
    """
    Generates the ReadinessReport for a DiagnosticRun.
    Called by the Celery task during the REPORTING phase.
    
    Steps:
    1. Load AnalysisResult (schema_inconsistencies, pipeline_bottlenecks, middleware_gaps)
    2. Load DiagnosticRun and SimulationScenario
    3. Calculate composite ReadinessScore via ReadinessScoreCalculator domain service
    4. Determine ReadinessVerdict via ReadinessVerdict.from_score()
    5. Publish ReadinessScoreCalculated event
    6. Build ordered RemediationPlan via RemediationPlannerService domain service:
       a. Create RemediationItem for each CRITICAL and HIGH finding
       b. Create RemediationItem for MEDIUM findings above impact threshold
       c. Apply topological sort on dependency graph
    7. Publish RemediationPlanBuilt event
    8. Build ReportContext (template variables)
    9. Render HTML report via Jinja2ReportRenderer
    10. Render PDF from HTML via PlaywrightPdfRenderer
    11. Serialize JSON report via Pydantic .model_dump()
    12. Store all three formats
    13. Create ReadinessReport entity
    14. Persist via ReadinessReportRepository
    15. Publish ReadinessReportGenerated event
    16. Transition DiagnosticRun to COMPLETED
    17. Return response with report_id and access URLs
    
    Raises:
        AnalysisResultNotFoundError: if analysis has not completed
        InsufficientAnalysisDataError: if fewer than 2 sub-scores are available
        ReportRenderingError: if HTML/PDF rendering fails
    """
```

**Command schema**:

```python
class GenerateReadinessReportCommand(BaseModel):
    run_id: UUID
    analysis_id: UUID
    include_sections: list[ReportSectionType] = list(ReportSectionType)  # All by default
    brand_name: str = "Preflight"
    brand_logo_b64: Optional[str] = None
    brand_primary_colour: str = "#1a56db"
    output_formats: list[ReportFormat] = [ReportFormat.HTML, ReportFormat.PDF, ReportFormat.JSON]
```

---

### `get_report`

**Use case**: Retrieve a generated ReadinessReport.

```python
async def get_report(
    self,
    query: GetReportQuery,
) -> ReadinessReportResponse:
    """
    Retrieve a ReadinessReport for API consumption.
    Returns the full report data as a DTO.
    
    Raises:
        ReadinessReportNotFoundError: if the report does not exist
    """
```

---

### `download_report_file`

**Use case**: Download a report file (HTML or PDF).

```python
async def download_report_file(
    self,
    query: DownloadReportFileQuery,
) -> ReportFileResponse:
    """
    Return the file path and MIME type for a report download.
    The actual file serving is handled by the FastAPI route (StreamingResponse).
    
    Raises:
        ReadinessReportNotFoundError: if the report does not exist
        ReportFormatNotAvailableError: if the requested format was not generated
    """
```

---

### `list_reports`

**Use case**: List ReadinessReports with optional filtering.

```python
async def list_reports(
    self,
    query: ListReportsQuery,
) -> list[ReadinessReportSummaryResponse]:
    """
    Returns a paginated list of ReadinessReports, optionally filtered by
    verdict or customer_name. Ordered by generated_at descending.
    
    Each summary includes: run_id, verdict, score, generated_at, customer_name.
    Full report content requires a separate get_report() call.
    """
```

---

## ScenarioModelingService

**Location**: `preflight/core/application/scenario_modeling_service.py`

Manages the what-if scenario modeling for completed DiagnosticRuns.

```python
class ScenarioModelingService:
    """
    Application service for what-if scenario modeling.
    
    Coordinates: ReadinessReportRepository, AnalysisResultRepository,
                 ScenarioProjectionCalculator (domain service), event publishing.
    Note: Scenario state is session-scoped (not persisted); the service
    is stateless and recalculates on each request.
    """
```

### `create_scenario_model`

**Use case**: Initialise a what-if model from a completed ReadinessReport.

```python
async def create_scenario_model(
    self,
    command: CreateScenarioModelCommand,
) -> ScenarioModelResponse:
    """
    Creates a ScenarioModel initialised with the current assumptions from
    the source ReadinessReport. Returns the model state and default projections.
    """
```

### `update_scenario_assumption`

**Use case**: Update one assumption and receive recalculated projections.

```python
async def update_scenario_assumption(
    self,
    command: UpdateScenarioAssumptionCommand,
) -> ScenarioModelResponse:
    """
    Updates a single ScenarioAssumption and recalculates all projections:
    - ProjectedIntegrationCost (effort and dollar estimate)
    - ScenarioTimeline (week-by-week remediation schedule)
    - Sensitivity scores for all other assumptions
    
    Returns the full updated ScenarioModel state.
    Raises:
        InvalidAssumptionValueError: if the new value is outside the valid range
    """
```

---

## Service Response DTOs

All response DTOs are Pydantic models. Selected examples:

```python
class DiagnosticRunSubmittedResponse(BaseModel):
    run_id: UUID
    status: str  # "PENDING"
    polling_url: str  # e.g., "/api/v1/diagnostics/{run_id}"
    websocket_url: str  # e.g., "/api/v1/diagnostics/{run_id}/progress"
    estimated_duration_minutes: int

class DiagnosticRunDetailResponse(BaseModel):
    run_id: UUID
    status: str
    customer_name: str
    use_case_type: str
    connected_systems: list[str]
    progress: Optional[RunProgressResponse]
    analysis_summary: Optional[AnalysisSummaryResponse]  # Partial results if available
    report_id: Optional[UUID]
    readiness_score: Optional[int]
    verdict: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

class ReadinessReportResponse(BaseModel):
    report_id: UUID
    run_id: UUID
    readiness_score: int
    verdict: str
    connected_systems: list[str]
    schema_inconsistencies: list[SchemaInconsistencyResponse]
    pipeline_bottlenecks: list[PipelineBottleneckResponse]
    middleware_gaps: list[MiddlewareGapResponse]
    remediation_plan: RemediationPlanResponse
    executive_summary: ExecutiveSummaryResponse
    generated_at: datetime
    html_download_url: str
    pdf_download_url: str
```
