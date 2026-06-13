"""
Report generation and retrieval endpoints.
"""
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, PlainTextResponse

from preflight.api.schemas import ReadinessReportResponse, VerdictEnum
from preflight.api.dependencies import get_runs_store, get_reports_store

router = APIRouter()


def _get_completed_report(
    run_id: str,
    runs_store: Dict[str, Any],
    reports_store: Dict[str, Any],
) -> Dict[str, Any]:
    """Shared helper: validate run exists + completed, then return its report dict."""
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
    return report


@router.get(
    "/{run_id}",
    response_model=ReadinessReportResponse,
    summary="Full report JSON",
)
async def get_report(
    run_id: str,
    runs_store: Dict[str, Any] = Depends(get_runs_store),
    reports_store: Dict[str, Any] = Depends(get_reports_store),
) -> ReadinessReportResponse:
    """Return the complete readiness report as JSON."""
    report = _get_completed_report(run_id, runs_store, reports_store)

    verdict_val = report.get("verdict", "NOT_READY")
    mapping = {"GO": VerdictEnum.GO, "NOT_YET": VerdictEnum.NOT_YET, "NOT_READY": VerdictEnum.NOT_READY}
    verdict_enum = mapping.get(verdict_val, VerdictEnum.NOT_READY)

    report_copy = dict(report)
    report_copy["verdict"] = verdict_enum
    if isinstance(report_copy.get("generated_at"), str):
        report_copy["generated_at"] = datetime.fromisoformat(
            report_copy["generated_at"].replace("Z", "+00:00")
        )

    allowed = set(ReadinessReportResponse.model_fields.keys())
    filtered = {k: v for k, v in report_copy.items() if k in allowed}
    return ReadinessReportResponse(**filtered)


@router.get(
    "/{run_id}/html",
    response_class=HTMLResponse,
    summary="HTML report",
)
async def get_report_html(
    run_id: str,
    runs_store: Dict[str, Any] = Depends(get_runs_store),
    reports_store: Dict[str, Any] = Depends(get_reports_store),
) -> HTMLResponse:
    """Generate and return the readiness report as a self-contained HTML page."""
    report = _get_completed_report(run_id, runs_store, reports_store)

    from preflight.reporting.html_reporter import HTMLReporter

    reporter = HTMLReporter()
    html = reporter.generate(report)
    return HTMLResponse(content=html)


@router.get(
    "/{run_id}/executive-summary",
    response_class=PlainTextResponse,
    summary="Executive summary text",
)
async def get_executive_summary(
    run_id: str,
    runs_store: Dict[str, Any] = Depends(get_runs_store),
    reports_store: Dict[str, Any] = Depends(get_reports_store),
) -> PlainTextResponse:
    """Return just the executive summary text for a completed report."""
    report = _get_completed_report(run_id, runs_store, reports_store)
    summary = report.get("executive_summary", "No executive summary available.")
    return PlainTextResponse(content=summary)
