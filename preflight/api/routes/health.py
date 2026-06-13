"""
Health check endpoints.
"""
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from preflight.api.schemas import HealthResponse

router = APIRouter()

VERSION = "0.1.0"


@router.get("", response_model=HealthResponse, summary="Health check")
async def health_check() -> HealthResponse:
    """Return basic service health information."""
    return HealthResponse(
        status="ok",
        version=VERSION,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/ready", summary="Readiness probe")
async def readiness_probe() -> JSONResponse:
    """Kubernetes-style readiness probe.

    Returns HTTP 200 when the service is ready to accept traffic.
    """
    return JSONResponse(
        content={"ready": True, "timestamp": datetime.now(timezone.utc).isoformat()},
        status_code=200,
    )
