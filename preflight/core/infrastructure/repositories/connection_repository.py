"""
Repository interface and in-memory implementation for :class:`ConnectionProfile`.

Follows the same hexagonal-architecture pattern as
:class:`~preflight.core.infrastructure.repositories.diagnostic_repository.DiagnosticRunRepository`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ...domain.entities import ConnectionProfile


class ConnectionProfileRepository(ABC):
    """Abstract repository interface for :class:`ConnectionProfile` entities.

    Implementations must provide async equivalents of the standard CRUD
    operations.
    """

    @abstractmethod
    async def save(self, profile: ConnectionProfile) -> None:
        """Persist a :class:`ConnectionProfile`, inserting or updating as needed.

        Args:
            profile: The entity to persist.
        """
        ...

    @abstractmethod
    async def find_by_id(self, profile_id: str) -> Optional[ConnectionProfile]:
        """Retrieve a :class:`ConnectionProfile` by its identifier.

        Args:
            profile_id: The unique profile identifier.

        Returns:
            The matching :class:`ConnectionProfile`, or ``None`` if not found.
        """
        ...

    @abstractmethod
    async def find_all(self) -> List[ConnectionProfile]:
        """Retrieve all persisted :class:`ConnectionProfile` entities.

        Returns:
            A list of all :class:`ConnectionProfile` instances.
        """
        ...

    @abstractmethod
    async def find_by_run_id(self, run_id: str) -> List[ConnectionProfile]:
        """Retrieve all connection profiles associated with a specific run.

        Args:
            run_id: The diagnostic run identifier.

        Returns:
            A list of :class:`ConnectionProfile` instances for the run.
        """
        ...

    @abstractmethod
    async def delete(self, profile_id: str) -> None:
        """Remove a :class:`ConnectionProfile` from the store.

        Args:
            profile_id: The unique profile identifier to delete.
        """
        ...


class InMemoryConnectionProfileRepository(ConnectionProfileRepository):
    """In-memory implementation of :class:`ConnectionProfileRepository`.

    Profiles are stored in a plain dict keyed by profile ID.  An auxiliary
    index on ``run_id`` (stored in ``profile.metadata["run_id"]``) supports
    efficient lookup by run.

    Usage::

        repo = InMemoryConnectionProfileRepository()
        profile = ConnectionProfile(name="Salesforce Prod")
        await repo.save(profile)
        fetched = await repo.find_by_id(profile.id)
    """

    def __init__(self) -> None:
        self._store: Dict[str, ConnectionProfile] = {}
        # Secondary index: run_id → set of profile_ids
        self._run_index: Dict[str, set] = {}

    async def save(self, profile: ConnectionProfile) -> None:
        """Persist (insert or update) a :class:`ConnectionProfile`.

        If the profile has a ``run_id`` key in its ``metadata`` dict it is
        indexed for fast retrieval via :meth:`find_by_run_id`.

        Args:
            profile: The entity to persist.
        """
        self._store[profile.id] = profile

        run_id: Optional[str] = profile.metadata.get("run_id")
        if run_id:
            self._run_index.setdefault(run_id, set()).add(profile.id)

    async def find_by_id(self, profile_id: str) -> Optional[ConnectionProfile]:
        """Retrieve a :class:`ConnectionProfile` by ID.

        Args:
            profile_id: The unique profile identifier.

        Returns:
            The matching entity or ``None``.
        """
        return self._store.get(profile_id)

    async def find_all(self) -> List[ConnectionProfile]:
        """Return all stored :class:`ConnectionProfile` entities.

        Returns:
            A list of all profiles in insertion order.
        """
        return list(self._store.values())

    async def find_by_run_id(self, run_id: str) -> List[ConnectionProfile]:
        """Return all profiles associated with a diagnostic run.

        Looks up profiles whose ``metadata["run_id"]`` equals the given
        ``run_id``.

        Args:
            run_id: The diagnostic run identifier.

        Returns:
            A list of matching :class:`ConnectionProfile` entities.
        """
        profile_ids = self._run_index.get(run_id, set())
        return [self._store[pid] for pid in profile_ids if pid in self._store]

    async def delete(self, profile_id: str) -> None:
        """Remove a profile from the store and all secondary indexes.

        No-op if the profile does not exist.

        Args:
            profile_id: The unique profile identifier.
        """
        profile = self._store.pop(profile_id, None)
        if profile is not None:
            run_id: Optional[str] = profile.metadata.get("run_id")
            if run_id and run_id in self._run_index:
                self._run_index[run_id].discard(profile_id)
                if not self._run_index[run_id]:
                    del self._run_index[run_id]

    def __len__(self) -> int:
        """Return the number of stored profiles."""
        return len(self._store)
