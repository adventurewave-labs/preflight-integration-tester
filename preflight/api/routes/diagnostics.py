"""
Diagnostic run management endpoints.
"""
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status

from preflight.api.schemas import (
    CreateDiagnosticRunRequest,
    DiagnosticRunResponse,
    ReadinessReportResponse,
    SchemaInconsistencyResponse,
    PipelineBottleneckResponse,
    MiddlewareGapResponse,
    RemediationItemResponse,
    VerdictEnum,
)
from preflight.api.dependencies import (
    get_connections_store,
    get_runs_store,
    get_reports_store,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _severity_for_verdict(verdict: str) -> VerdictEnum:
    mapping = {
        "GO": VerdictEnum.GO,
        "NOT_YET": VerdictEnum.NOT_YET,
        "NOT_READY": VerdictEnum.NOT_READY,
    }
    return mapping.get(verdict, VerdictEnum.NOT_READY)


async def _run_diagnostic(
    run_id: str,
    request: CreateDiagnosticRunRequest,
    connections_store: Dict[str, Any],
    runs_store: Dict[str, Any],
    reports_store: Dict[str, Any],
) -> None:
    """Background task: execute the full diagnostic pipeline and store results."""
    run = runs_store[run_id]

    try:
        run["status"] = "running"
        run["started_at"] = datetime.now(timezone.utc)
        run["progress_pct"] = 10.0

        await asyncio.sleep(0)  # yield control

        # Gather connection definitions from the store — these drive the real
        # pipeline (create_connector → connect → introspect → analyze).
        conn_defs = []
        for conn_id in request.connection_ids:
            conn = connections_store.get(conn_id)
            if conn:
                conn_defs.append(
                    {
                        "name": conn["name"],
                        "connector_type": conn["connector_type"],
                        "system_type": conn.get("system_type"),
                        "config": conn.get("config") or {},
                    }
                )

        scenario_dict = {
            "name": request.scenario.name,
            "description": request.scenario.description,
            "systems": [c["name"] for c in conn_defs],
            "target_systems": request.scenario.target_systems,
            "concurrent_users": request.scenario.concurrent_users,
            "queries_per_minute": request.scenario.queries_per_minute,
            "peak_multiplier": request.scenario.peak_multiplier,
            "response_time_target_ms": request.scenario.response_time_target_ms,
            "business_entities": _entities_as_dicts(request.scenario.business_entities),
            "entities": _entities_as_dicts(request.scenario.business_entities),
        }

        # ── The real pipeline (shared with the CLI) ─────────────────────────
        from preflight.analysis.pipeline_runner import (
            run_diagnostic as run_pipeline,
            PipelineError,
        )

        def on_progress(event: Dict[str, Any]) -> None:
            stages = {
                "connect": 25.0,
                "schema": 45.0,
                "pipeline": 65.0,
                "middleware": 75.0,
                "score": 85.0,
                "report": 95.0,
            }
            pct = stages.get(event.get("stage"))
            if pct is not None:
                run["progress_pct"] = pct

        config = {"scenario": scenario_dict, "analysis": {}, "reporting": {}}
        try:
            payload = await run_pipeline(
                config, mock=False, progress_cb=on_progress, connections=conn_defs
            )
        except PipelineError as exc:
            run["status"] = "failed"
            run["error_message"] = str(exc)
            run["completed_at"] = datetime.now(timezone.utc)
            return

        inconsistencies = payload["schema_inconsistencies"]
        gaps = payload["middleware_gaps"]
        pipeline_results = payload["pipeline_bottlenecks"]
        executive_summary = payload["executive_summary"]

        await asyncio.sleep(0)

        # ── Build report payload ──────────────────────────────────────────────
        schema_resp = [
            SchemaInconsistencyResponse(
                id=inc.get("id", str(uuid.uuid4())),
                entity_name=inc.get("entity", ""),
                source_system=inc.get("source", ""),
                target_system=inc.get("target", ""),
                inconsistency_type=inc.get("type", ""),
                severity=inc.get("severity", "MEDIUM"),
                impact_description=inc.get("detail", ""),
                remediation_hint="Review and align field definitions across systems.",
            ).model_dump()
            for inc in inconsistencies
        ]

        middleware_resp = []
        for gap in gaps:
            effort = gap.get("effort_days", (5, 20))
            pattern = gap.get("pattern")
            recommended = ""
            if pattern is not None:
                if hasattr(pattern, "description"):
                    recommended = pattern.description
                elif isinstance(pattern, dict):
                    recommended = pattern.get("description", "")
            middleware_resp.append(
                MiddlewareGapResponse(
                    id=gap.get("id", str(uuid.uuid4())),
                    gap_type=gap.get("type", ""),
                    description=gap.get("description", ""),
                    severity=gap.get("severity", "MEDIUM"),
                    blocking=gap.get("blocking", False),
                    effort_min_days=effort[0] if isinstance(effort, tuple) else 5,
                    effort_max_days=effort[1] if isinstance(effort, tuple) else 20,
                    recommended_solution=recommended,
                ).model_dump()
            )

        remediation = _build_remediation(inconsistencies, gaps)

        # Map measured bottleneck rows (CLI shape) onto the API response model.
        pipeline_resp = [
            PipelineBottleneckResponse(
                id=str(uuid.uuid4()),
                system=row.get("system", ""),
                bottleneck_type="stress_test",
                severity=row.get("severity", "INFO"),
                description=(
                    f"{row.get('system', 'system')}: p95 {row.get('p95_ms', 0)} ms, "
                    f"{row.get('error_rate_pct', 0)}% errors at {row.get('actual_qps', 0)} QPS"
                ),
                p95_latency_ms=row.get("p95_ms"),
                error_rate_pct=row.get("error_rate_pct"),
            ).model_dump()
            for row in pipeline_results
        ]

        report_payload = {
            "run_id": run_id,
            "readiness_score": payload["readiness_score"],
            "verdict": payload["verdict"],
            "executive_summary": executive_summary,
            "schema_inconsistencies": schema_resp,
            "pipeline_bottlenecks": pipeline_resp,
            "middleware_gaps": middleware_resp,
            "remediation_plan": remediation,
            "total_effort_min_days": None,
            "total_effort_max_days": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "findings_summary": {
                "schema_inconsistencies": len(schema_resp),
                "middleware_gaps": len(middleware_resp),
                "pipeline_bottlenecks": len(pipeline_results),
                "critical_issues": payload["critical_count"],
            },
            # Extra fields for HTML reporter
            "scenario_name": request.scenario.name,
            "critical_count": payload["critical_count"],
            "total_issues": payload["total_issues"],
            "remediation_weeks": payload["remediation_weeks"],
            "entity_matches": payload.get("entity_matches") or [],
            "warnings": payload.get("warnings") or [],
            "data_sources": payload.get("data_sources") or {},
        }

        reports_store[run_id] = report_payload
        run["status"] = "completed"
        run["completed_at"] = datetime.now(timezone.utc)
        run["progress_pct"] = 100.0

    except Exception as exc:
        logger.exception("Diagnostic run %s failed", run_id)
        run["status"] = "failed"
        run["error_message"] = str(exc)
        run["completed_at"] = datetime.now(timezone.utc)


def _entities_as_dicts(business_entities) -> List[Dict]:
    """BusinessEntity models -> dicts; plain strings (legacy clients) ->
    fieldless dicts, which the matcher accepts and skips."""
    from preflight.api.schemas import BusinessEntity

    return [
        be.model_dump() if isinstance(be, BusinessEntity) else {"name": str(be), "fields": []}
        for be in (business_entities or [])
    ]


def _build_remediation(inconsistencies: List[Dict], gaps: List[Dict]) -> List[Dict]:
    """Build a prioritised remediation plan (shared implementation in the runner)."""
    from preflight.analysis.pipeline_runner import build_remediation_plan

    return build_remediation_plan(inconsistencies, gaps)


@router.post(
    "",
    response_model=DiagnosticRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create and start a diagnostic run",
)
async def create_diagnostic_run(
    request: CreateDiagnosticRunRequest,
    background_tasks: BackgroundTasks,
    connections_store: Dict[str, Any] = Depends(get_connections_store),
    runs_store: Dict[str, Any] = Depends(get_runs_store),
    reports_store: Dict[str, Any] = Depends(get_reports_store),
) -> DiagnosticRunResponse:
    """Create a new diagnostic run and immediately start it in the background."""
    # Validate that all connection IDs exist
    missing = [cid for cid in request.connection_ids if cid not in connections_store]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown connection IDs: {missing}",
        )

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    run = {
        "id": run_id,
        "name": request.name,
        "status": "pending",
        "progress_pct": 0.0,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error_message": None,
    }
    runs_store[run_id] = run

    background_tasks.add_task(
        _run_diagnostic,
        run_id,
        request,
        connections_store,
        runs_store,
        reports_store,
    )

    return DiagnosticRunResponse(**run)


@router.get(
    "",
    response_model=List[DiagnosticRunResponse],
    summary="List all diagnostic runs",
)
async def list_diagnostic_runs(
    runs_store: Dict[str, Any] = Depends(get_runs_store),
) -> List[DiagnosticRunResponse]:
    """Return all diagnostic runs."""
    return [DiagnosticRunResponse(**r) for r in runs_store.values()]


@router.get(
    "/{run_id}",
    response_model=DiagnosticRunResponse,
    summary="Get run status and progress",
)
async def get_diagnostic_run(
    run_id: str,
    runs_store: Dict[str, Any] = Depends(get_runs_store),
) -> DiagnosticRunResponse:
    """Return the current status and progress of a diagnostic run."""
    run = runs_store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagnostic run '{run_id}' not found.",
        )
    return DiagnosticRunResponse(**run)


@router.get(
    "/{run_id}/report",
    response_model=ReadinessReportResponse,
    summary="Get the completed readiness report for a run",
)
async def get_run_report(
    run_id: str,
    runs_store: Dict[str, Any] = Depends(get_runs_store),
    reports_store: Dict[str, Any] = Depends(get_reports_store),
) -> ReadinessReportResponse:
    """Return the full readiness report for a completed diagnostic run."""
    run = runs_store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagnostic run '{run_id}' not found.",
        )
    if run["status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run '{run_id}' is not yet completed (status: {run['status']}).",
        )

    report = reports_store.get(run_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report for run '{run_id}' not found.",
        )

    # Coerce verdict to enum
    verdict_val = report.get("verdict", "NOT_READY")
    report_copy = dict(report)
    report_copy["verdict"] = _severity_for_verdict(verdict_val)
    if isinstance(report_copy.get("generated_at"), str):
        from datetime import datetime
        report_copy["generated_at"] = datetime.fromisoformat(
            report_copy["generated_at"].replace("Z", "+00:00")
        )

    # Strip extra fields not in the response schema
    allowed = set(ReadinessReportResponse.model_fields.keys())
    filtered = {k: v for k, v in report_copy.items() if k in allowed}
    return ReadinessReportResponse(**filtered)


@router.delete(
    "/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a diagnostic run",
)
async def delete_diagnostic_run(
    run_id: str,
    runs_store: Dict[str, Any] = Depends(get_runs_store),
    reports_store: Dict[str, Any] = Depends(get_reports_store),
) -> None:
    """Remove a diagnostic run and its associated report."""
    if run_id not in runs_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagnostic run '{run_id}' not found.",
        )
    del runs_store[run_id]
    reports_store.pop(run_id, None)
