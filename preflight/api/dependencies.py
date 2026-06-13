"""
FastAPI dependency injection module.

Provides shared service instances and caches as FastAPI dependencies.
"""
from functools import lru_cache
from typing import Optional, Dict, Any
import uuid
from datetime import datetime


# ---------------------------------------------------------------------------
# In-memory stores (replace with persistent storage in production)
# ---------------------------------------------------------------------------

# Connections store: {id: ConnectionResponse-like dict}
_connections_store: Dict[str, Dict[str, Any]] = {}

# Diagnostic runs store: {id: DiagnosticRunResponse-like dict}
_runs_store: Dict[str, Dict[str, Any]] = {}

# Reports store: {run_id: ReadinessReportResponse-like dict}
_reports_store: Dict[str, Dict[str, Any]] = {}


def get_connections_store() -> Dict[str, Dict[str, Any]]:
    """Return the in-memory connections store.

    FastAPI dependency — inject with ``Depends(get_connections_store)``.
    """
    return _connections_store


def get_runs_store() -> Dict[str, Dict[str, Any]]:
    """Return the in-memory diagnostic runs store.

    FastAPI dependency — inject with ``Depends(get_runs_store)``.
    """
    return _runs_store


def get_reports_store() -> Dict[str, Dict[str, Any]]:
    """Return the in-memory reports store.

    FastAPI dependency — inject with ``Depends(get_reports_store)``.
    """
    return _reports_store
