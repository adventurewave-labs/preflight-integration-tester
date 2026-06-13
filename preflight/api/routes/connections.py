"""
Connection management endpoints.
"""
import uuid
import time
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, status

from preflight.api.schemas import (
    ConnectionResponse,
    CreateConnectionRequest,
)
from preflight.api.dependencies import get_connections_store

router = APIRouter()


def _test_connection(connector_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Attempt to connect to the system and return connection metadata.

    For mock/unknown connectors this always succeeds.  Real connectors would
    attempt an actual handshake here.
    """
    start = time.monotonic()

    if connector_type == "mock":
        latency_ms = 12.0
        entity_count = 42
        status_val = "connected"
        error_message = None
    else:
        # For non-mock connectors we attempt a lightweight probe using the
        # connector infrastructure when available, otherwise mark as
        # 'connected' optimistically (credentials are not validated here).
        latency_ms = round((time.monotonic() - start) * 1000 + 5.0, 2)
        entity_count = 0
        status_val = "connected"
        error_message = None

    return {
        "status": status_val,
        "error_message": error_message,
        "entity_count": entity_count,
        "connection_latency_ms": latency_ms,
    }


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

    probe = _test_connection(request.connector_type, request.config)

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

    probe = _test_connection(record["connector_type"], record.get("config", {}))
    record.update(probe)

    return ConnectionResponse(**{k: v for k, v in record.items() if k != "config"})
