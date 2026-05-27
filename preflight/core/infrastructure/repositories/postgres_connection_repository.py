"""
PostgreSQL-backed ConnectionProfile repository using SQLAlchemy async ORM.
"""
import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from ..database.models import ConnectionProfileModel
from ...domain.entities import ConnectionProfile
from ...domain.value_objects import SystemType, ConnectorType
from .connection_repository import ConnectionProfileRepository

logger = logging.getLogger(__name__)


class PostgresConnectionProfileRepository(ConnectionProfileRepository):
    """PostgreSQL-backed implementation of ConnectionProfileRepository.

    Uses SQLAlchemy's async ORM to persist and retrieve
    :class:`~preflight.core.domain.entities.ConnectionProfile` entities.

    Args:
        session: An active :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, profile: ConnectionProfile) -> None:
        """Save or update a ConnectionProfile.

        Performs an upsert: if a record with the same ``id`` already exists
        it is updated in place; otherwise a new row is inserted.

        Args:
            profile: The :class:`ConnectionProfile` entity to persist.
        """
        existing = await self._session.get(ConnectionProfileModel, profile.id)

        model_data = self._to_model_dict(profile)

        if existing:
            for key, value in model_data.items():
                setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
        else:
            model = ConnectionProfileModel(**model_data)
            self._session.add(model)

        await self._session.flush()

    async def find_by_id(self, profile_id: str) -> Optional[ConnectionProfile]:
        """Find a ConnectionProfile by its unique identifier.

        Args:
            profile_id: The unique profile identifier.

        Returns:
            The matching :class:`ConnectionProfile`, or ``None`` if not found.
        """
        model = await self._session.get(ConnectionProfileModel, profile_id)
        if not model:
            return None
        return self._to_domain(model)

    async def find_all(self) -> List[ConnectionProfile]:
        """List all connection profiles ordered by creation time (newest first).

        Returns:
            A list of all :class:`ConnectionProfile` entities.
        """
        result = await self._session.execute(
            select(ConnectionProfileModel).order_by(ConnectionProfileModel.created_at.desc())
        )
        models = result.scalars().all()
        return [self._to_domain(m) for m in models]

    async def find_by_run_id(self, run_id: str) -> List[ConnectionProfile]:
        """Retrieve all connection profiles associated with a specific run.

        Args:
            run_id: The diagnostic run identifier.

        Returns:
            A list of :class:`ConnectionProfile` entities for the run.
        """
        result = await self._session.execute(
            select(ConnectionProfileModel)
            .where(ConnectionProfileModel.run_id == run_id)
            .order_by(ConnectionProfileModel.created_at.asc())
        )
        models = result.scalars().all()
        return [self._to_domain(m) for m in models]

    async def delete(self, profile_id: str) -> None:
        """Delete a connection profile.

        Args:
            profile_id: The unique profile identifier to delete.
        """
        await self._session.execute(
            delete(ConnectionProfileModel).where(ConnectionProfileModel.id == profile_id)
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _to_model_dict(self, profile: ConnectionProfile) -> dict:
        """Convert a domain entity to a flat dict for ORM hydration.

        Credentials are intentionally stripped — only the ``credential_ref``
        pointer is stored, never the raw secret.

        Args:
            profile: The :class:`ConnectionProfile` to convert.

        Returns:
            A dict suitable for constructing / updating a
            :class:`ConnectionProfileModel`.
        """
        # Extract run_id from metadata if present
        run_id: Optional[str] = profile.metadata.get("run_id") if profile.metadata else None

        # Credentials: store only the reference, never raw secrets
        credential_ref = None
        if profile.credentials:
            credential_ref = {
                "system_type": profile.credentials.system_type.value
                if hasattr(profile.credentials.system_type, "value")
                else str(profile.credentials.system_type),
                "host": profile.credentials.host,
                "port": profile.credentials.port,
                "database": profile.credentials.database,
                "username": profile.credentials.username,
                "credential_ref": profile.credentials.credential_ref,
            }

        # Store non-run_id metadata in metadata_json
        metadata_json = {k: v for k, v in (profile.metadata or {}).items() if k != "run_id"}

        return {
            "id": profile.id,
            "run_id": run_id,
            "name": profile.name,
            "system_type": profile.system_type.value
            if hasattr(profile.system_type, "value")
            else str(profile.system_type),
            "connector_type": profile.connector_type.value
            if hasattr(profile.connector_type, "value")
            else str(profile.connector_type),
            "status": profile.status,
            "error_message": profile.error_message,
            "entity_count": profile.entity_count,
            "connection_latency_ms": profile.connection_latency_ms,
            "credential_ref": credential_ref,
            "metadata_json": metadata_json or None,
            "connected_at": profile.connected_at,
        }

    def _to_domain(self, model: ConnectionProfileModel) -> ConnectionProfile:
        """Reconstruct a domain entity from an ORM model row.

        Args:
            model: The :class:`ConnectionProfileModel` ORM row.

        Returns:
            A :class:`ConnectionProfile` entity with fields populated.
        """
        # Restore metadata dict (including run_id if present)
        metadata: dict = {}
        if model.metadata_json:
            metadata.update(model.metadata_json)
        if model.run_id:
            metadata["run_id"] = model.run_id

        # Safely parse enum values, falling back to defaults
        try:
            system_type = SystemType(model.system_type)
        except ValueError:
            system_type = SystemType.DATABASE

        try:
            connector_type = ConnectorType(model.connector_type)
        except ValueError:
            connector_type = ConnectorType.POSTGRESQL

        return ConnectionProfile(
            id=model.id,
            name=model.name,
            system_type=system_type,
            connector_type=connector_type,
            credentials=None,  # never stored in plaintext
            status=model.status,
            error_message=model.error_message,
            entity_count=model.entity_count,
            connection_latency_ms=model.connection_latency_ms,
            connected_at=model.connected_at,
            metadata=metadata,
        )
