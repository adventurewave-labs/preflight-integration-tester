"""
Connection management endpoints.
"""
import asyncio
import logging
import time
import uuid
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, status

from preflight.api.schemas import (
    ConnectionResponse,
    CreateConnectionRequest,
)
from preflight.api.dependencies import get_connections_store
from preflight.core.infrastructure.connectors.connector_factory import (
    create_connector,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _test_connection(connector_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Connect to the system for real and return measured connection metadata.

    Uses the connector registry (postgresql, salesforce, snowflake, mock, …).
    Connectors whose client library is missing fall back to their mock
    implementation, reported via ``error_message`` so callers know the entity
    counts come from synthetic data.  Credentials ARE validated: a failed
    handshake returns ``status="failed"`` with the error message.
    """
    from preflight.analysis.pipeline_runner import (
    check_host_allowed,
    resolve_connector_type,
)

    start = time.monotonic()
    requested = connector_type or ""
    # Resolve aliases the same way the pipeline runner does, so a connection
    # that passes the API test is also accepted by a diagnostic run.
    key = resolve_connector_type(requested) or requested.lower().strip()

    # Shared, fail-closed allowlist (see pipeline_runner.check_host_allowed):
    # enforced here AND on every diagnostic run, since the run is what opens
    # connections and generates load. Validates the connector's EFFECTIVE
    # destination for the resolved type.
    allow_error = check_host_allowed(config or {}, connector_type=key)
    if allow_error is not None:
        return {
            "status": "failed",
            "error_message": allow_error,
            "entity_count": 0,
            "connection_latency_ms": None,
        }

    try:
        connector = create_connector(key, config or {}, timeout_seconds=10)
    except ValueError as exc:
        return {
            "status": "failed",
            "error_message": str(exc),
            "entity_count": 0,
            "connection_latency_ms": None,
        }
    except Exception:
        # Bad config shapes (wrong types, missing nested keys) must not 500.
        logger.warning("Connector creation failed for type %r", key, exc_info=True)
        return {
            "status": "failed",
            "error_message": "connector could not be created from the given config",
            "entity_count": 0,
            "connection_latency_ms": None,
        }

    is_fallback = key != "mock" and type(connector).__name__.startswith("Mock")
    try:
        try:
            connected = await asyncio.wait_for(connector.connect(), timeout=10)
        except Exception as exc:
            # Deliberately coarse message: raw driver errors can echo
            # host/port details back to API callers, which turns this
            # endpoint into a network-probing oracle. Detail goes to the
            # server log only.
            logger.warning("Connection test failed for %r: %s", key, exc)
            return {
                "status": "failed",
                "error_message": "connection failed (check host, port and credentials)",
                "entity_count": 0,
                "connection_latency_ms": None,
            }
        if not connected:
            return {
                "status": "failed",
                "error_message": "connection handshake failed",
                "entity_count": 0,
                "connection_latency_ms": None,
            }

        latency_ms = 0.0
        entity_count = 0
        try:
            latency_ms = await asyncio.wait_for(connector.ping(), timeout=10)
            meta = await asyncio.wait_for(connector.get_metadata(), timeout=20)
            entity_count = meta.entity_count
        except Exception:
            pass  # best-effort; the connection itself worked
    finally:
        # Cancellation-safe cleanup: CancelledError is a BaseException and
        # must not leak a half-open connector either.
        await _safe_disconnect(connector)

    error_message = None
    if is_fallback:
        error_message = (
            f"client library for '{key}' not installed — mock fallback used; "
            f"credentials were NOT validated"
        )
    return {
        "status": "connected",
        "error_message": error_message,
        "entity_count": entity_count,
        "connection_latency_ms": round(latency_ms, 2) if latency_ms else round(
            (time.monotonic() - start) * 1000, 2
        ),
    }


async def _safe_disconnect(connector) -> None:
    try:
        await connector.disconnect()
    except Exception:
        pass


@router.post(
    "",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and test a new connection",
)
async def create_connection(
    request: CreateConnectionRequest,
    store: Dict[str, Any] = Depends(get_connections_store),
) -> ConnectionResponse:
    """Create a read-only connection to an enterprise system and run a connectivity test."""
    conn_id = str(uuid.uuid4())

    probe = await _test_connection(request.connector_type, request.config)

    record = {
        "id": conn_id,
        "name": request.name,
        "connector_type": request.connector_type,
        "system_type": request.system_type.value,
        "config": request.config,
        **probe,
    }
    store[conn_id] = record

    return ConnectionResponse(**{k: v for k, v in record.items() if k != "config"})


@router.get(
    "",
    response_model=List[ConnectionResponse],
    summary="List all connections",
)
async def list_connections(
    store: Dict[str, Any] = Depends(get_connections_store),
) -> List[ConnectionResponse]:
    """Return all registered connections."""
    return [
        ConnectionResponse(**{k: v for k, v in c.items() if k != "config"})
        for c in store.values()
    ]


@router.get(
    "/{connection_id}",
    response_model=ConnectionResponse,
    summary="Get connection details",
)
async def get_connection(
    connection_id: str,
    store: Dict[str, Any] = Depends(get_connections_store),
) -> ConnectionResponse:
    """Return details for a specific connection."""
    record = store.get(connection_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )
    return ConnectionResponse(**{k: v for k, v in record.items() if k != "config"})


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a connection",
)
async def delete_connection(
    connection_id: str,
    store: Dict[str, Any] = Depends(get_connections_store),
) -> None:
    """Remove a connection from the registry."""
    if connection_id not in store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )
    del store[connection_id]


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionResponse,
    summary="Re-test an existing connection",
)
async def test_connection(
    connection_id: str,
    store: Dict[str, Any] = Depends(get_connections_store),
) -> ConnectionResponse:
    """Re-run the connectivity probe for an existing connection."""
    record = store.get(connection_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connection '{connection_id}' not found.",
        )

    probe = await _test_connection(record["connector_type"], record.get("config", {}))
    record.update(probe)

    return ConnectionResponse(**{k: v for k, v in record.items() if k != "config"})
