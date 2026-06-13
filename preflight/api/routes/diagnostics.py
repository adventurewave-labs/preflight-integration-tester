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

        # Gather connected systems info
        systems = []
        for conn_id in request.connection_ids:
            conn = connections_store.get(conn_id)
            if conn:
                systems.append({"name": conn["name"], "type": conn["system_type"]})

        # ── Schema analysis ──────────────────────────────────────────────────
        from preflight.analysis.schema_analyzer import SchemaAnalyzer

        scenario_dict = {
            "name": request.scenario.name,
            "systems": [s["name"] for s in systems],
            "concurrent_users": request.scenario.concurrent_users,
            "queries_per_minute": request.scenario.queries_per_minute,
            "peak_multiplier": request.scenario.peak_multiplier,
        }

        mock_schemas = {
            "salesforce": {
                "Account": [
                    {"name": "Id", "type": "varchar(18)", "nullable": False},
                    {"name": "Name", "type": "varchar(255)", "nullable": False},
                    {"name": "Phone", "type": "varchar(40)", "nullable": True},
                    {"name": "AnnualRevenue", "type": "decimal", "nullable": True},
                ],
                "Contact": [
                    {"name": "Id", "type": "varchar(18)", "nullable": False},
                    {"name": "AccountId", "type": "varchar(18)", "nullable": True},
                    {"name": "LastName", "type": "varchar(80)", "nullable": False},
                    {"name": "Email", "type": "varchar(80)", "nullable": True},
                ],
            },
            "sap": {
                "KUNNR": [
                    {"name": "KUNNR", "type": "varchar(10)", "nullable": False},
                    {"name": "NAME1", "type": "nvarchar(35)", "nullable": False},
                    {"name": "TELF1", "type": "varchar(16)", "nullable": True},
                    {"name": "UMSAV", "type": "numeric(15,2)", "nullable": True},
                ],
            },
        }

        analyzer = SchemaAnalyzer()
        schema_results = analyzer.analyze_all(mock_schemas)
        inconsistencies = analyzer.generate_inconsistency_report(schema_results)

        run["progress_pct"] = 40.0
        await asyncio.sleep(0)

        # ── Middleware analysis ───────────────────────────────────────────────
        from preflight.analysis.middleware_analyzer import MiddlewareAnalyzer

        mw_analyzer = MiddlewareAnalyzer()
        gaps = mw_analyzer.analyze(systems, scenario_dict, {"inconsistencies": inconsistencies})

        run["progress_pct"] = 65.0
        await asyncio.sleep(0)

        # ── Readiness score ───────────────────────────────────────────────────
        from preflight.analysis.readiness_calculator import ReadinessCalculator

        calc = ReadinessCalculator()
        pipeline_results: List[Dict[str, Any]] = []
        breakdown = calc.calculate(inconsistencies, pipeline_results, gaps, [])

        run["progress_pct"] = 80.0
        await asyncio.sleep(0)

        # ── Executive summary ─────────────────────────────────────────────────
        from preflight.reporting.executive_summary import ExecutiveSummaryGenerator

        summary_gen = ExecutiveSummaryGenerator()
        executive_summary = summary_gen.generate(
            verdict=breakdown.verdict,
            score=breakdown.overall_score,
            schema_issues=inconsistencies,
            pipeline_issues=pipeline_results,
            middleware_gaps=gaps,
            scenario=scenario_dict,
            remediation_weeks=breakdown.estimated_remediation_weeks,
        )

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

        report_payload = {
            "run_id": run_id,
            "readiness_score": breakdown.overall_score,
            "verdict": breakdown.verdict,
            "executive_summary": executive_summary,
            "schema_inconsistencies": schema_resp,
            "pipeline_bottlenecks": [],
            "middleware_gaps": middleware_resp,
            "remediation_plan": remediation,
            "total_effort_min_days": None,
            "total_effort_max_days": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "findings_summary": {
                "schema_inconsistencies": len(schema_resp),
                "middleware_gaps": len(middleware_resp),
                "pipeline_bottlenecks": 0,
                "critical_issues": breakdown.critical_issues,
            },
            # Extra fields for HTML reporter
            "scenario_name": request.scenario.name,
            "critical_count": breakdown.critical_issues,
            "total_issues": breakdown.total_issues,
            "remediation_weeks": (
                f"{breakdown.estimated_remediation_weeks:.0f}"
                if breakdown.estimated_remediation_weeks
                else "TBD"
            ),
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


def _build_remediation(inconsistencies: List[Dict], gaps: List[Dict]) -> List[Dict]:
    """Build a prioritised remediation plan from inconsistencies and gaps."""
    items = []
    seq = 1

    for gap in gaps:
        if gap.get("blocking", False):
            effort = gap.get("effort_days", (5, 20))
            items.append(
                RemediationItemResponse(
                    id=f"rem_{gap.get('id', seq)}",
                    title=f"Implement {gap.get('type', 'middleware').replace('_', ' ').title()}",
                    description=gap.get("description", ""),
                    category="middleware",
                    priority=9 if gap.get("severity") == "CRITICAL" else 7,
                    severity=gap.get("severity", "HIGH"),
                    effort_min_days=effort[0] if isinstance(effort, tuple) else 5,
                    effort_max_days=effort[1] if isinstance(effort, tuple) else 20,
                    recommended_sequence=seq,
                ).model_dump()
            )
            seq += 1

    critical_schema = [i for i in inconsistencies if i.get("severity") == "CRITICAL"]
    if critical_schema:
        items.append(
            RemediationItemResponse(
                id="rem_schema_critical",
                title="Resolve Critical Schema Mismatches",
                description=f"Fix {len(critical_schema)} critical key/schema mismatches across systems",
                category="schema",
                priority=10,
                severity="CRITICAL",
                effort_min_days=len(critical_schema) * 3,
                effort_max_days=len(critical_schema) * 7,
                recommended_sequence=seq,
            ).model_dump()
        )
        seq += 1

    high_schema = [i for i in inconsistencies if i.get("severity") == "HIGH"]
    if high_schema:
        items.append(
            RemediationItemResponse(
                id="rem_schema_high",
                title="Resolve High-Severity Schema Issues",
                description=f"Fix {len(high_schema)} high-severity schema issues",
                category="schema",
                priority=8,
                severity="HIGH",
                effort_min_days=len(high_schema) * 2,
                effort_max_days=len(high_schema) * 5,
                recommended_sequence=seq,
            ).model_dump()
        )

    return items


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
