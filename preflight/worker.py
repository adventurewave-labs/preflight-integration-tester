"""
Preflight Celery Worker

Background task processing for diagnostic runs.
Celery uses Redis as both broker and result backend.

Usage:
    celery -A preflight.worker worker --loglevel=info
"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict

from celery import Celery
from celery.signals import worker_ready, task_failure, task_success
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

# Configure Celery
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///preflight.db")

celery_app = Celery(
    "preflight",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Reliability settings
    task_acks_late=True,                # Acknowledge after completion, not before
    worker_prefetch_multiplier=1,       # One task at a time per worker
    task_reject_on_worker_lost=True,    # Re-queue on worker crash

    # Result settings
    result_expires=86400,               # 24 hours
    task_track_started=True,

    # Retry settings
    task_max_retries=3,
    task_default_retry_delay=30,

    # Concurrency
    worker_concurrency=int(os.environ.get("CELERY_CONCURRENCY", "4")),

    # Task routes
    task_routes={
        "preflight.worker.run_full_diagnostic": {"queue": "diagnostics"},
        "preflight.worker.run_schema_analysis": {"queue": "analysis"},
        "preflight.worker.generate_report": {"queue": "reports"},
    },

    # Beat schedule for health checks (optional)
    beat_schedule={},
)


def run_async(coro):
    """Run async coroutine from sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


async def _update_run_progress(run_id: str, status: str, progress: float, error: str = None) -> None:
    """Update diagnostic run progress in the database."""
    try:
        from preflight.core.infrastructure.database.session import get_db_session
        from preflight.core.infrastructure.database.models import DiagnosticRunModel

        async with get_db_session() as session:
            model = await session.get(DiagnosticRunModel, run_id)
            if model:
                model.status = status
                model.progress_pct = progress
                if error:
                    model.error_message = error
                if status == "running" and not model.started_at:
                    model.started_at = datetime.utcnow()
                if status in ("completed", "failed"):
                    model.completed_at = datetime.utcnow()
    except Exception as e:
        logger.warning(f"Could not update run {run_id} progress: {e}")


@celery_app.task(
    bind=True,
    name="preflight.worker.run_full_diagnostic",
    max_retries=1,
    queue="diagnostics",
    soft_time_limit=3600,   # 1 hour soft limit
    time_limit=4000,         # Hard kill after ~67 min
)
def run_full_diagnostic(self, run_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a complete AI deployment readiness diagnostic.

    Args:
        run_id: DiagnosticRun ID to update
        config: Diagnostic configuration dict with scenario and connections

    Returns:
        Result dict with score, verdict, and summary
    """
    logger.info(f"Starting diagnostic run: {run_id}")

    async def _run():
        run_async(_update_run_progress(run_id, "running", 5.0))

        try:
            # Step 1: Schema Analysis
            from preflight.analysis.schema_analyzer import SchemaAnalyzer

            await _update_run_progress(run_id, "running", 20.0)
            schemas = config.get("schemas", {})
            if schemas:
                analyzer = SchemaAnalyzer()
                schema_results = analyzer.analyze_all(schemas)
                inconsistencies = analyzer.generate_inconsistency_report(schema_results)
            else:
                inconsistencies = []

            await _update_run_progress(run_id, "running", 50.0)

            # Step 2: Middleware Analysis
            from preflight.analysis.middleware_analyzer import MiddlewareAnalyzer

            connections = config.get("connections", [])
            scenario = config.get("scenario", {})
            mw_analyzer = MiddlewareAnalyzer()
            gaps = mw_analyzer.analyze(connections, scenario, {"inconsistencies": inconsistencies})

            await _update_run_progress(run_id, "running", 70.0)

            # Step 3: Calculate Score
            from preflight.analysis.readiness_calculator import ReadinessCalculator

            calc = ReadinessCalculator(weights=config.get("weights"))
            breakdown = calc.calculate(inconsistencies, [], gaps, [])

            await _update_run_progress(run_id, "running", 85.0)

            # Step 4: Generate Report
            from preflight.reporting.executive_summary import ExecutiveSummaryGenerator

            summary_gen = ExecutiveSummaryGenerator()
            executive_summary = summary_gen.generate(
                verdict=breakdown.verdict,
                score=breakdown.overall_score,
                schema_issues=inconsistencies,
                pipeline_issues=[],
                middleware_gaps=gaps,
                remediation_weeks=breakdown.estimated_remediation_weeks,
            )

            # Persist report summary
            try:
                from preflight.core.infrastructure.database.session import get_db_session
                from preflight.core.infrastructure.database.models import DiagnosticRunModel

                async with get_db_session() as session:
                    model = await session.get(DiagnosticRunModel, run_id)
                    if model:
                        model.status = "completed"
                        model.progress_pct = 100.0
                        model.completed_at = datetime.utcnow()
                        model.report_summary = {
                            "score": breakdown.overall_score,
                            "verdict": breakdown.verdict,
                            "executive_summary": executive_summary[:5000],
                            "schema_inconsistency_count": len(inconsistencies),
                            "middleware_gap_count": len(gaps),
                            "critical_issues": breakdown.critical_issues,
                            "remediation_weeks": breakdown.estimated_remediation_weeks,
                        }
            except Exception as e:
                logger.warning(f"Could not persist report: {e}")

            return {
                "run_id": run_id,
                "score": breakdown.overall_score,
                "verdict": breakdown.verdict,
                "schema_inconsistencies": len(inconsistencies),
                "middleware_gaps": len(gaps),
                "executive_summary": executive_summary[:1000],
            }

        except Exception as e:
            logger.error(f"Diagnostic failed for {run_id}: {e}", exc_info=True)
            await _update_run_progress(run_id, "failed", 0.0, str(e))
            raise

    try:
        return run_async(_run())
    except Exception as exc:
        logger.error(f"Task failed: {exc}")
        try:
            self.retry(exc=exc, countdown=30)
        except self.MaxRetriesExceededError:
            return {"run_id": run_id, "error": str(exc), "status": "failed"}


@celery_app.task(
    name="preflight.worker.run_schema_analysis",
    queue="analysis",
)
def run_schema_analysis(schemas: Dict, threshold: float = 0.8) -> Dict:
    """Run schema analysis as a background task."""
    from preflight.analysis.schema_analyzer import SchemaAnalyzer

    analyzer = SchemaAnalyzer(similarity_threshold=threshold)
    results = analyzer.analyze_all(schemas)
    inconsistencies = analyzer.generate_inconsistency_report(results)
    return {"inconsistencies": inconsistencies, "entity_count": len(results)}


@celery_app.task(
    name="preflight.worker.generate_report",
    queue="reports",
)
def generate_report(run_data: Dict) -> Dict:
    """Generate a readiness report as a background task."""
    from preflight.reporting.html_reporter import HTMLReporter
    from preflight.reporting.executive_summary import ExecutiveSummaryGenerator

    summary_gen = ExecutiveSummaryGenerator()
    summary = summary_gen.generate(
        verdict=run_data.get("verdict", "NOT_READY"),
        score=run_data.get("score", 0),
        schema_issues=run_data.get("schema_inconsistencies", []),
        pipeline_issues=run_data.get("pipeline_results", []),
        middleware_gaps=run_data.get("middleware_gaps", []),
    )

    reporter = HTMLReporter()
    report_data = {**run_data, "executive_summary": summary}
    html = reporter.generate(report_data)

    return {"html": html, "executive_summary": summary}


@worker_ready.connect
def on_worker_ready(**kwargs):
    logger.info("Preflight Celery worker ready to accept tasks")


@task_success.connect
def on_task_success(sender=None, result=None, **kwargs):
    logger.info(f"Task {sender.name} completed successfully")


@task_failure.connect
def on_task_failure(sender=None, exception=None, **kwargs):
    logger.error(f"Task {sender.name} failed: {exception}")
