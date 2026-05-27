"""Connection manager for concurrent enterprise system connections.

Manages a pool of connectors for a single diagnostic run: establishes
connections concurrently, health-checks them periodically, and ensures
graceful cleanup even when individual connectors fail.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional

from preflight.core.infrastructure.connectors.base import BaseConnector, SystemMetadata
from preflight.core.infrastructure.connectors.connector_factory import create_connector

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-transfer objects
# ---------------------------------------------------------------------------


@dataclass
class ConnectionSpec:
    """Specification for a single connection to establish."""

    connector_id: str         # unique identifier within the run
    connector_type: str       # e.g. "postgresql", "salesforce"
    config: Dict[str, Any]   # connector-specific config dict
    timeout_seconds: int = 30


@dataclass
class ConnectionStatus:
    """Runtime status of a managed connection."""

    spec: ConnectionSpec
    connector: Optional[BaseConnector] = None
    connected: bool = False
    error: Optional[str] = None
    latency_ms: Optional[float] = None
    metadata: Optional[SystemMetadata] = None
    connected_at: Optional[datetime] = None
    last_health_check: Optional[datetime] = None

    @property
    def connector_id(self) -> str:
        return self.spec.connector_id

    @property
    def is_healthy(self) -> bool:
        return self.connected and self.connector is not None and self.connector.is_connected


@dataclass
class ManagerProgress:
    """Progress snapshot for a connection-establishment run."""

    total: int
    connected: int
    failed: int
    pending: int
    elapsed_ms: float

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.connected / self.total


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Manages multiple connectors for a diagnostic run.

    Usage::

        manager = ConnectionManager(specs)
        async with manager:
            for status in manager.iter_healthy():
                meta = status.metadata
                ...

    Or manually::

        await manager.connect_all(progress_callback=lambda p: print(p))
        ...
        await manager.disconnect_all()
    """

    def __init__(
        self,
        specs: List[ConnectionSpec],
        max_concurrency: int = 5,
        health_check_interval_seconds: int = 60,
    ):
        self._specs = specs
        self._max_concurrency = max_concurrency
        self._health_check_interval = health_check_interval_seconds
        self._statuses: Dict[str, ConnectionStatus] = {
            s.connector_id: ConnectionStatus(spec=s) for s in specs
        }
        self._health_task: Optional[asyncio.Task] = None
        self._started_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ConnectionManager":
        await self.connect_all()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect_all()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect_all(
        self,
        progress_callback: Optional[Callable[[ManagerProgress], None]] = None,
    ) -> Dict[str, ConnectionStatus]:
        """Establish all connections concurrently.

        Args:
            progress_callback: Optional callable invoked after each connection
                attempt with the current :class:`ManagerProgress`.

        Returns:
            Dictionary of connector_id → :class:`ConnectionStatus`.
        """
        self._started_at = time.monotonic()
        logger.info(
            "ConnectionManager: establishing %d connections (max_concurrency=%d)",
            len(self._specs),
            self._max_concurrency,
        )

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _connect_one(spec: ConnectionSpec) -> None:
            async with semaphore:
                status = self._statuses[spec.connector_id]
                try:
                    connector = create_connector(
                        spec.connector_type, spec.config, spec.timeout_seconds
                    )
                    status.connector = connector
                    t0 = time.monotonic()
                    ok = await asyncio.wait_for(
                        connector.connect(), timeout=spec.timeout_seconds + 5
                    )
                    elapsed = (time.monotonic() - t0) * 1000.0
                    if ok:
                        status.connected = True
                        status.latency_ms = elapsed
                        status.connected_at = datetime.utcnow()
                        logger.info(
                            "Connected: %s (%s) in %.1f ms",
                            spec.connector_id,
                            spec.connector_type,
                            elapsed,
                        )
                        # Pre-fetch metadata while we have the connection
                        try:
                            status.metadata = await connector.get_metadata()
                        except Exception as meta_exc:
                            logger.warning(
                                "Could not fetch metadata for %s: %s",
                                spec.connector_id,
                                meta_exc,
                            )
                    else:
                        status.error = "connect() returned False"
                        logger.warning("Connection failed: %s", spec.connector_id)
                except asyncio.TimeoutError:
                    status.error = f"Connection timed out after {spec.timeout_seconds}s"
                    logger.error("Timeout connecting: %s", spec.connector_id)
                except Exception as exc:
                    status.error = str(exc)
                    logger.error(
                        "Error connecting %s: %s", spec.connector_id, exc
                    )
                finally:
                    if progress_callback is not None:
                        progress_callback(self._build_progress())

        tasks = [asyncio.create_task(_connect_one(s)) for s in self._specs]
        await asyncio.gather(*tasks, return_exceptions=True)

        healthy = sum(1 for s in self._statuses.values() if s.is_healthy)
        logger.info(
            "ConnectionManager: %d/%d connections healthy",
            healthy,
            len(self._specs),
        )

        # Start background health-checker
        if healthy > 0:
            self._health_task = asyncio.create_task(self._health_check_loop())

        return dict(self._statuses)

    async def disconnect_all(self) -> None:
        """Disconnect all active connectors gracefully."""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        disconnect_tasks = []
        for status in self._statuses.values():
            if status.connector and status.connected:
                disconnect_tasks.append(
                    asyncio.create_task(self._safe_disconnect(status))
                )
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
        logger.info("ConnectionManager: all connections closed")

    async def _safe_disconnect(self, status: ConnectionStatus) -> None:
        try:
            await status.connector.disconnect()
        except Exception as exc:
            logger.warning(
                "Error disconnecting %s: %s", status.connector_id, exc
            )
        finally:
            status.connected = False

    # ------------------------------------------------------------------
    # Health checking
    # ------------------------------------------------------------------

    async def _health_check_loop(self) -> None:
        """Background coroutine that pings each active connector periodically."""
        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
                await self._check_all_health()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Health check error: %s", exc)

    async def _check_all_health(self) -> None:
        tasks = [
            asyncio.create_task(self._check_one_health(status))
            for status in self._statuses.values()
            if status.is_healthy
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_one_health(self, status: ConnectionStatus) -> None:
        try:
            latency = await asyncio.wait_for(
                status.connector.ping(), timeout=10
            )
            status.latency_ms = latency
            status.last_health_check = datetime.utcnow()
        except Exception as exc:
            logger.warning(
                "Health check failed for %s: %s – marking unhealthy",
                status.connector_id,
                exc,
            )
            status.connected = False
            status.error = str(exc)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_status(self, connector_id: str) -> Optional[ConnectionStatus]:
        return self._statuses.get(connector_id)

    def iter_healthy(self) -> Iterator[ConnectionStatus]:
        """Yield only healthy (connected) connector statuses."""
        for status in self._statuses.values():
            if status.is_healthy:
                yield status

    def iter_all(self) -> Iterator[ConnectionStatus]:
        """Yield all connector statuses regardless of health."""
        yield from self._statuses.values()

    @property
    def healthy_count(self) -> int:
        return sum(1 for s in self._statuses.values() if s.is_healthy)

    @property
    def total_count(self) -> int:
        return len(self._statuses)

    def summary(self) -> Dict[str, Any]:
        """Return a human-readable summary of connection health."""
        return {
            "total": self.total_count,
            "healthy": self.healthy_count,
            "failed": sum(1 for s in self._statuses.values() if s.error),
            "connectors": {
                cid: {
                    "type": s.spec.connector_type,
                    "healthy": s.is_healthy,
                    "latency_ms": s.latency_ms,
                    "error": s.error,
                    "tables": len(s.metadata.tables) if s.metadata else None,
                }
                for cid, s in self._statuses.items()
            },
        }

    def _build_progress(self) -> ManagerProgress:
        connected = sum(1 for s in self._statuses.values() if s.connected)
        failed = sum(1 for s in self._statuses.values() if s.error and not s.connected)
        elapsed = (time.monotonic() - (self._started_at or time.monotonic())) * 1000.0
        return ManagerProgress(
            total=len(self._specs),
            connected=connected,
            failed=failed,
            pending=len(self._specs) - connected - failed,
            elapsed_ms=elapsed,
        )
