"""Enterprise system connectors for Preflight.

All connectors are read-only and follow the :class:`BaseConnector` interface.
Use :func:`create_connector` as the primary factory for instantiating connectors.

Example::

    from preflight.core.infrastructure.connectors import create_connector

    config = {"host": "localhost", "database": "mydb", "username": "ro_user", "password": "..."}
    async with create_connector("postgresql", config) as conn:
        meta = await conn.get_metadata()
        print(meta.entity_count)
"""

from preflight.core.infrastructure.connectors.base import (
    BaseConnector,
    QueryResult,
    SystemMetadata,
    TableSchema,
)
from preflight.core.infrastructure.connectors.connector_factory import (
    create_connector,
    list_connector_types,
    register_connector,
)
from preflight.core.infrastructure.connectors.connection_manager import (
    ConnectionManager,
    ConnectionSpec,
    ConnectionStatus,
    ManagerProgress,
)
from preflight.core.infrastructure.connectors.mock_connector import MockEnterpriseConnector
from preflight.core.infrastructure.connectors.postgresql_connector import (
    PostgreSQLConnector,
    MockPostgreSQLConnector,
    get_postgresql_connector,
)
from preflight.core.infrastructure.connectors.salesforce_connector import (
    SalesforceConnector,
    MockSalesforceConnector,
    get_salesforce_connector,
)
from preflight.core.infrastructure.connectors.snowflake_connector import (
    SnowflakeConnector,
    MockSnowflakeConnector,
    get_snowflake_connector,
)

__all__ = [
    # Base types
    "BaseConnector",
    "QueryResult",
    "SystemMetadata",
    "TableSchema",
    # Factory
    "create_connector",
    "list_connector_types",
    "register_connector",
    # Connection manager
    "ConnectionManager",
    "ConnectionSpec",
    "ConnectionStatus",
    "ManagerProgress",
    # Concrete connectors
    "MockEnterpriseConnector",
    "PostgreSQLConnector",
    "MockPostgreSQLConnector",
    "get_postgresql_connector",
    "SalesforceConnector",
    "MockSalesforceConnector",
    "get_salesforce_connector",
    "SnowflakeConnector",
    "MockSnowflakeConnector",
    "get_snowflake_connector",
]
