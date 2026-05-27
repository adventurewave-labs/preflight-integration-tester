"""Abstract base connector for enterprise system integrations.

All connectors must be read-only, handle errors gracefully, and support
timeout/retry logic with typed schema information.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, AsyncIterator
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class TableSchema:
    """Schema information for a database table/object."""

    name: str
    columns: List[Dict[str, Any]]  # [{name, type, nullable, pk, fk_to}]
    row_count_estimate: Optional[int] = None
    description: Optional[str] = None
    sample_data: Optional[List[Dict]] = None  # small sample, anonymized


@dataclass
class SystemMetadata:
    """Metadata about the connected system."""

    system_name: str
    system_version: Optional[str] = None
    tables: List[TableSchema] = field(default_factory=list)
    entity_count: int = 0
    total_row_estimate: Optional[int] = None
    connection_latency_ms: Optional[float] = None
    collected_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class QueryResult:
    """Result of a read-only query."""

    query: str
    row_count: int
    execution_time_ms: float
    columns: List[str]
    rows: List[Dict[str, Any]]
    error: Optional[str] = None


class BaseConnector(ABC):
    """Abstract base for all enterprise system connectors.

    Subclasses must implement connect, disconnect, get_metadata,
    execute_read_query, and ping.  The validate_read_only helper blocks
    any destructive SQL before it reaches the target system.
    """

    SYSTEM_NAME: str = "unknown"

    def __init__(self, config: Dict[str, Any], timeout_seconds: int = 30):
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._connected = False
        self._connection = None
        logger.info("Initializing %s connector", self.SYSTEM_NAME)

    @abstractmethod
    async def connect(self) -> bool:
        """Establish read-only connection. Returns True on success."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly close connection."""
        ...

    @abstractmethod
    async def get_metadata(self) -> SystemMetadata:
        """Get schema/metadata without any writes."""
        ...

    @abstractmethod
    async def execute_read_query(
        self, query: str, params: Optional[Dict] = None
    ) -> QueryResult:
        """Execute a SELECT query only."""
        ...

    @abstractmethod
    async def ping(self) -> float:
        """Ping the system, return latency in ms."""
        ...

    async def validate_read_only(self, query: str) -> bool:
        """Ensure a query is read-only before executing.

        Checks for forbidden keywords that would mutate data.  Returns
        False and logs a warning if a forbidden keyword is found.
        """
        forbidden = [
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "CREATE",
            "ALTER",
            "TRUNCATE",
            "EXEC",
            "EXECUTE",
        ]
        query_upper = query.upper().strip()
        for keyword in forbidden:
            if query_upper.startswith(keyword) or f" {keyword} " in query_upper:
                logger.warning("Blocked write query attempt containing: %s", keyword)
                return False
        return True

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def __repr__(self):
        return (
            f"{self.__class__.__name__}"
            f"(system={self.SYSTEM_NAME}, connected={self._connected})"
        )
