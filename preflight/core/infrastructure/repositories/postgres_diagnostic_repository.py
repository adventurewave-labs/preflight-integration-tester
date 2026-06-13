"""
PostgreSQL-backed DiagnosticRun repository using SQLAlchemy async ORM.
"""
import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from ..database.models import DiagnosticRunModel
from ...domain.aggregates import DiagnosticRun, SimulationScenario, ReadinessReport
from ...domain.value_objects import ReadinessScore, ReadinessVerdict
from .diagnostic_repository import DiagnosticRunRepository

logger = logging.getLogger(__name__)


class PostgresDiagnosticRunRepository(DiagnosticRunRepository):
    """PostgreSQL-backed implementation of DiagnosticRunRepository.

    Uses SQLAlchemy's async ORM to persist and retrieve
    :class:`~preflight.core.domain.aggregates.DiagnosticRun` aggregates.

    Args:
        session: An active :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, run: DiagnosticRun) -> None:
        """Save or update a DiagnosticRun.

        Performs an upsert: if a record with the same ``id`` already exists
        it is updated in place; otherwise a new row is inserted.

        Args:
            run: The :class:`DiagnosticRun` aggregate to persist.
        """
        existing = await self._session.get(DiagnosticRunModel, run.id)

        model_data = self._to_model_dict(run)

        if existing:
            for key, value in model_data.items():
                setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
        else:
            model = DiagnosticRunModel(**model_data)
            self._session.add(model)

        await self._session.flush()

    async def find_by_id(self, run_id: str) -> Optional[DiagnosticRun]:
        """Find a DiagnosticRun by its unique identifier.

        Args:
            run_id: The unique run identifier.

        Returns:
            The matching :class:`DiagnosticRun`, or ``None`` if not found.
        """
        model = await self._session.get(DiagnosticRunModel, run_id)
        if not model:
            return None
        return self._to_domain(model)

    async def find_all(self) -> List[DiagnosticRun]:
        """List all diagnostic runs ordered newest-first.

        Returns:
            A list of all :class:`DiagnosticRun` aggregates.
        """
        result = await self._session.execute(
            select(DiagnosticRunModel).order_by(DiagnosticRunModel.created_at.desc())
        )
        models = result.scalars().all()
        return [self._to_domain(m) for m in models]

    async def delete(self, run_id: str) -> None:
        """Delete a diagnostic run and all its child findings (cascade).

        Args:
            run_id: The unique run identifier to delete.
        """
        await self._session.execute(
            delete(DiagnosticRunModel).where(DiagnosticRunModel.id == run_id)
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _to_model_dict(self, run: DiagnosticRun) -> dict:
        """Convert a domain aggregate to a flat dict for ORM hydration.

        Args:
            run: The :class:`DiagnosticRun` to convert.

        Returns:
            A dict suitable for constructing / updating a
            :class:`DiagnosticRunModel`.
        """
        scenario_dict = None
        if run.scenario:
            scenario_dict = {
                "name": run.scenario.name,
                "description": run.scenario.description,
                "target_systems": run.scenario.target_systems,
                "concurrent_users": run.scenario.concurrent_users,
                "queries_per_minute": run.scenario.queries_per_minute,
                "peak_multiplier": run.scenario.peak_multiplier,
                "response_time_target_ms": run.scenario.response_time_target_ms,
                "business_entities": run.scenario.business_entities,
                "use_case": run.scenario.use_case,
            }

        report_summary = None
        if run.report and run.report.readiness_score:
            report_summary = {
                "score": run.report.readiness_score.value,
                "verdict": run.report.verdict.value if run.report.verdict else None,
                "executive_summary": (
                    run.report.executive_summary[:2000]
                    if run.report.executive_summary
                    else ""
                ),
            }

        return {
            "id": run.id,
            "name": run.name,
            "status": run.status,
            "progress_pct": run.progress_pct,
            "error_message": run.error_message,
            "scenario": scenario_dict,
            "report_summary": report_summary,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        }

    def _to_domain(self, model: DiagnosticRunModel) -> DiagnosticRun:
        """Reconstruct a domain aggregate from an ORM model row.

        Only scalar/summary fields are rehydrated — detailed child findings
        (schema inconsistencies, bottlenecks, etc.) are *not* loaded here to
        keep reads lightweight.  Callers that need full finding lists should
        query the relevant child repositories.

        Args:
            model: The :class:`DiagnosticRunModel` ORM row.

        Returns:
            A :class:`DiagnosticRun` aggregate with scalar fields populated.
        """
        run = DiagnosticRun(
            id=model.id,
            name=model.name,
            status=model.status,
            progress_pct=model.progress_pct,
            error_message=model.error_message,
            created_at=model.created_at,
            started_at=model.started_at,
            completed_at=model.completed_at,
        )

        if model.scenario:
            s = model.scenario
            run.scenario = SimulationScenario(
                name=s.get("name", ""),
                description=s.get("description", ""),
                target_systems=s.get("target_systems", []),
                concurrent_users=s.get("concurrent_users", 10),
                queries_per_minute=s.get("queries_per_minute", 60),
                peak_multiplier=s.get("peak_multiplier", 2.0),
                response_time_target_ms=s.get("response_time_target_ms", 500),
                business_entities=s.get("business_entities", []),
                use_case=s.get("use_case", ""),
            )

        if model.report_summary:
            rs = model.report_summary
            report = ReadinessReport()
            if rs.get("score") is not None:
                report.readiness_score = ReadinessScore(value=float(rs["score"]))
            if rs.get("verdict"):
                report.verdict = ReadinessVerdict(rs["verdict"])
            report.executive_summary = rs.get("executive_summary", "")
            run.report = report

        return run
