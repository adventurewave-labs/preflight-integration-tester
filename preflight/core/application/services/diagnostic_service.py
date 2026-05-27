"""
Diagnostic orchestration application service.

Coordinates the full lifecycle of a :class:`DiagnosticRun`: creation,
asynchronous execution, status polling, and result retrieval.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...domain.aggregates import (
    AnalysisResults,
    DiagnosticRun,
    SimulationScenario,
)
from ...domain.entities import ConnectionProfile
from ...domain.value_objects import ConnectorType, SystemType
from ...infrastructure.repositories.diagnostic_repository import (
    DiagnosticRunRepository,
)
from .schema_analysis_service import SchemaAnalysisService
from .report_service import ReportService

logger = logging.getLogger(__name__)


class DiagnosticService:
    """Orchestration service that coordinates a full Preflight diagnostic run.

    The service manages the run lifecycle and delegates to specialised
    sub-services for analysis and reporting.

    Args:
        repository: Persistence store for :class:`DiagnosticRun` aggregates.
        schema_service: Service for schema consistency analysis.
        report_service: Service for readiness report generation.

    Usage::

        repo = InMemoryDiagnosticRunRepository()
        service = DiagnosticService(repo)

        run = await service.create_run("My First Run", scenario_config)
        await service.start_run(run.id)
        status = await service.get_run_status(run.id)
    """

    def __init__(
        self,
        repository: DiagnosticRunRepository,
        schema_service: Optional[SchemaAnalysisService] = None,
        report_service: Optional[ReportService] = None,
    ) -> None:
        self._repo = repository
        self._schema_service = schema_service or SchemaAnalysisService()
        self._report_service = report_service or ReportService()

    async def create_run(
        self,
        name: str,
        scenario_config: Optional[Dict[str, Any]] = None,
    ) -> DiagnosticRun:
        """Create and persist a new :class:`DiagnosticRun`.

        Args:
            name: Human-readable name for the diagnostic run.
            scenario_config: Optional dict used to build a
                :class:`SimulationScenario`.  Keys correspond to
                :class:`SimulationScenario` attributes.

        Returns:
            The newly created (``pending``) :class:`DiagnosticRun`.
        """
        scenario: Optional[SimulationScenario] = None
        if scenario_config:
            scenario = SimulationScenario(
                name=scenario_config.get("name", name),
                description=scenario_config.get("description", ""),
                target_systems=scenario_config.get("target_systems", []),
                concurrent_users=scenario_config.get("concurrent_users", 10),
                queries_per_minute=scenario_config.get("queries_per_minute", 100),
                peak_multiplier=float(scenario_config.get("peak_multiplier", 2.0)),
                response_time_target_ms=scenario_config.get(
                    "response_time_target_ms", 500
                ),
                business_entities=scenario_config.get("business_entities", []),
                use_case=scenario_config.get("use_case", ""),
            )

        run = DiagnosticRun(name=name, scenario=scenario)
        await self._repo.save(run)
        logger.info("Created diagnostic run '%s' (%s)", name, run.id)
        return run

    async def start_run(self, run_id: str) -> None:
        """Transition a run to *running* and execute the diagnostic pipeline.

        The actual analysis is executed in a background asyncio task so that
        this coroutine returns quickly.  Callers should poll
        :meth:`get_run_status` to monitor progress.

        Args:
            run_id: ID of the diagnostic run to start.

        Raises:
            ValueError: If the run does not exist or is not in ``pending`` state.
        """
        run = await self._repo.find_by_id(run_id)
        if run is None:
            raise ValueError(f"Diagnostic run '{run_id}' not found")
        if run.status != "pending":
            raise ValueError(
                f"Cannot start run '{run_id}': status is '{run.status}' (expected 'pending')"
            )

        run.start()
        await self._repo.save(run)

        # Dispatch the pipeline as a background task.
        asyncio.create_task(self._execute_pipeline(run_id))
        logger.info("Started diagnostic run '%s'", run_id)

    async def get_run_status(self, run_id: str) -> Dict[str, Any]:
        """Return the current status and progress of a diagnostic run.

        Args:
            run_id: ID of the diagnostic run.

        Returns:
            A dict with keys ``run_id``, ``name``, ``status``,
            ``progress_pct``, ``started_at``, ``completed_at``, and
            ``error_message``.

        Raises:
            ValueError: If the run does not exist.
        """
        run = await self._repo.find_by_id(run_id)
        if run is None:
            raise ValueError(f"Diagnostic run '{run_id}' not found")

        return {
            "run_id": run.id,
            "name": run.name,
            "status": run.status,
            "progress_pct": run.progress_pct,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": (
                run.completed_at.isoformat() if run.completed_at else None
            ),
            "error_message": run.error_message,
        }

    async def get_run_results(self, run_id: str) -> DiagnosticRun:
        """Return the full :class:`DiagnosticRun` aggregate including results.

        Args:
            run_id: ID of the completed diagnostic run.

        Returns:
            The :class:`DiagnosticRun` aggregate.

        Raises:
            ValueError: If the run does not exist.
            RuntimeError: If the run has not yet completed.
        """
        run = await self._repo.find_by_id(run_id)
        if run is None:
            raise ValueError(f"Diagnostic run '{run_id}' not found")
        if run.status not in ("completed", "failed"):
            raise RuntimeError(
                f"Run '{run_id}' has not completed yet (status: '{run.status}')"
            )
        return run

    async def list_runs(self) -> List[DiagnosticRun]:
        """Return all diagnostic runs in the repository.

        Returns:
            List of :class:`DiagnosticRun` aggregates, newest first.
        """
        runs = await self._repo.find_all()
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    # ------------------------------------------------------------------
    # Private pipeline execution
    # ------------------------------------------------------------------

    async def _execute_pipeline(self, run_id: str) -> None:
        """Execute the full diagnostic pipeline for a run.

        This method is designed to run as a background asyncio task.
        Errors are caught and recorded on the run via :meth:`DiagnosticRun.fail`.

        Args:
            run_id: ID of the run to execute.
        """
        run = await self._repo.find_by_id(run_id)
        if run is None:
            logger.error("Pipeline execution aborted: run '%s' not found", run_id)
            return

        try:
            # Phase 1: Schema analysis.
            run.status = "analyzing"
            run.progress_pct = 20.0
            await self._repo.save(run)

            entities: List[str] = []
            if run.scenario:
                entities = run.scenario.business_entities

            analysis = await self._schema_service.analyze_schemas(
                run.connections, entities
            )
            analysis.completed_at = datetime.utcnow()

            # Phase 2: Transition to reporting.
            run.complete_analysis(analysis)
            await self._repo.save(run)

            # Phase 3: Generate report.
            report = self._report_service.generate_report(run)

            # Phase 4: Finalise run.
            run.complete(report)
            await self._repo.save(run)

            # Dispatch domain events (in a real system these would go to a bus).
            events = run.pop_events()
            for event in events:
                logger.debug("Domain event: %s", event.to_dict())

            logger.info(
                "Diagnostic run '%s' completed with verdict '%s' (score %.1f)",
                run_id,
                run.report.verdict.value if run.report and run.report.verdict else "N/A",
                (
                    run.report.readiness_score.value
                    if run.report and run.report.readiness_score
                    else 0
                ),
            )

        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Diagnostic run '%s' failed: %s", run_id, exc)
            run = await self._repo.find_by_id(run_id)
            if run is not None:
                run.fail(str(exc))
                await self._repo.save(run)
