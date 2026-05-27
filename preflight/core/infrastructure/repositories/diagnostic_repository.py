"""
Repository interface and in-memory implementation for :class:`DiagnosticRun`.

The abstract base class defines the port (in hexagonal-architecture terminology)
so that the application layer remains independent of any specific persistence
technology.  The ``InMemoryDiagnosticRunRepository`` provides a concrete
implementation suitable for unit tests and local development.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ...domain.aggregates import DiagnosticRun


class DiagnosticRunRepository(ABC):
    """Abstract repository interface for :class:`DiagnosticRun` aggregates.

    Implementations must provide async equivalents of the standard CRUD
    operations.  Callers must ``await`` each method.
    """

    @abstractmethod
    async def save(self, run: DiagnosticRun) -> None:
        """Persist a :class:`DiagnosticRun`, inserting or updating as needed.

        Args:
            run: The aggregate to persist.
        """
        ...

    @abstractmethod
    async def find_by_id(self, run_id: str) -> Optional[DiagnosticRun]:
        """Retrieve a :class:`DiagnosticRun` by its identifier.

        Args:
            run_id: The unique run identifier.

        Returns:
            The matching :class:`DiagnosticRun`, or ``None`` if not found.
        """
        ...

    @abstractmethod
    async def find_all(self) -> List[DiagnosticRun]:
        """Retrieve all persisted :class:`DiagnosticRun` aggregates.

        Returns:
            A list of all :class:`DiagnosticRun` instances (may be empty).
        """
        ...

    @abstractmethod
    async def delete(self, run_id: str) -> None:
        """Remove a :class:`DiagnosticRun` from the store.

        Args:
            run_id: The unique run identifier to delete.
        """
        ...


class InMemoryDiagnosticRunRepository(DiagnosticRunRepository):
    """In-memory implementation of :class:`DiagnosticRunRepository`.

    Stores aggregates in a plain dict keyed by run ID.  Deep-copies are used
    on save to prevent callers from mutating stored state through object
    references.

    This implementation is **not** thread-safe.  For concurrent use in tests
    consider adding an ``asyncio.Lock``.

    Usage::

        repo = InMemoryDiagnosticRunRepository()
        run = DiagnosticRun(name="test")
        await repo.save(run)
        fetched = await repo.find_by_id(run.id)
    """

    def __init__(self) -> None:
        self._store: Dict[str, DiagnosticRun] = {}

    async def save(self, run: DiagnosticRun) -> None:
        """Persist (insert or update) a :class:`DiagnosticRun`.

        A shallow reference is stored (deep copy skipped for performance in
        tests; callers should not rely on isolation beyond what the domain
        aggregate enforces).

        Args:
            run: The aggregate to persist.
        """
        self._store[run.id] = run

    async def find_by_id(self, run_id: str) -> Optional[DiagnosticRun]:
        """Retrieve a :class:`DiagnosticRun` by ID.

        Args:
            run_id: The unique run identifier.

        Returns:
            The matching aggregate or ``None``.
        """
        return self._store.get(run_id)

    async def find_all(self) -> List[DiagnosticRun]:
        """Return all stored :class:`DiagnosticRun` aggregates.

        Returns:
            A list of all runs ordered by insertion (dict-order, Python 3.7+).
        """
        return list(self._store.values())

    async def delete(self, run_id: str) -> None:
        """Remove a run from the store.

        No-op if the run does not exist.

        Args:
            run_id: The unique run identifier.
        """
        self._store.pop(run_id, None)

    def __len__(self) -> int:
        """Return the number of stored runs."""
        return len(self._store)
