from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class SystemTypeEnum(str, Enum):
    ERP = "ERP"
    CRM = "CRM"
    DATA_WAREHOUSE = "DATA_WAREHOUSE"
    DATABASE = "DATABASE"

class VerdictEnum(str, Enum):
    GO = "GO"
    NOT_YET = "NOT_YET"
    NOT_READY = "NOT_READY"

class CreateConnectionRequest(BaseModel):
    name: str = Field(..., description="Human-readable system name")
    connector_type: str = Field(..., description="e.g. postgresql, salesforce, snowflake, mock")
    system_type: SystemTypeEnum
    config: Dict[str, Any] = Field(default_factory=dict, description="Connector-specific config")

class ConnectionResponse(BaseModel):
    id: str
    name: str
    connector_type: str
    system_type: str
    status: str
    error_message: Optional[str] = None
    entity_count: int = 0
    connection_latency_ms: Optional[float] = None

class CreateScenarioRequest(BaseModel):
    name: str
    description: str = ""
    target_systems: List[str] = Field(..., description="List of connection IDs")
    concurrent_users: int = Field(10, ge=1, le=1000)
    queries_per_minute: int = Field(60, ge=1, le=10000)
    peak_multiplier: float = Field(2.0, ge=1.0, le=10.0)
    response_time_target_ms: int = Field(500, ge=50, le=30000)
    business_entities: List[str] = Field(default_factory=list)
    use_case: str = ""

class CreateDiagnosticRunRequest(BaseModel):
    name: str
    scenario: CreateScenarioRequest
    connection_ids: List[str]

class DiagnosticRunResponse(BaseModel):
    id: str
    name: str
    status: str
    progress_pct: float = 0.0
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

class SchemaInconsistencyResponse(BaseModel):
    id: str
    entity_name: str
    source_system: str
    target_system: str
    inconsistency_type: str
    severity: str
    impact_description: str
    remediation_hint: str

class PipelineBottleneckResponse(BaseModel):
    id: str
    system: str
    bottleneck_type: str
    severity: str
    description: str
    p95_latency_ms: Optional[float] = None
    error_rate_pct: Optional[float] = None

class MiddlewareGapResponse(BaseModel):
    id: str
    gap_type: str
    description: str
    severity: str
    blocking: bool
    effort_min_days: int
    effort_max_days: int
    recommended_solution: str

class RemediationItemResponse(BaseModel):
    id: str
    title: str
    description: str
    category: str
    priority: int
    severity: str
    effort_min_days: int
    effort_max_days: int
    recommended_sequence: int

class ReadinessReportResponse(BaseModel):
    run_id: str
    readiness_score: float
    verdict: VerdictEnum
    executive_summary: str
    schema_inconsistencies: List[SchemaInconsistencyResponse] = []
    pipeline_bottlenecks: List[PipelineBottleneckResponse] = []
    middleware_gaps: List[MiddlewareGapResponse] = []
    remediation_plan: List[RemediationItemResponse] = []
    total_effort_min_days: Optional[int] = None
    total_effort_max_days: Optional[int] = None
    generated_at: Optional[datetime] = None
    findings_summary: Dict[str, int] = {}

class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
