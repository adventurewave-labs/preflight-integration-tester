"""Factory for creating enterprise system connectors.

Use :func:`create_connector` as the single entry-point for instantiating any
connector type supported by Preflight.
"""

import logging
from typing import Any, Dict, Optional

from preflight.core.infrastructure.connectors.base import BaseConnector
from preflight.core.infrastructure.connectors.mock_connector import MockEnterpriseConnector
from preflight.core.infrastructure.connectors.postgresql_connector import (
    get_postgresql_connector,
)
from preflight.core.infrastructure.connectors.salesforce_connector import (
    get_salesforce_connector,
)
from preflight.core.infrastructure.connectors.snowflake_connector import (
    get_snowflake_connector,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Any] = {
    # Generic mock – always available
    "mock": MockEnterpriseConnector,
    # Real connectors (fall back to mocks when libraries absent)
    "postgresql": get_postgresql_connector,
    "postgres": get_postgresql_connector,
    "pg": get_postgresql_connector,
    "salesforce": get_salesforce_connector,
    "sfdc": get_salesforce_connector,
    "snowflake": get_snowflake_connector,
}


def create_connector(
    connector_type: str,
    config: Dict[str, Any],
    timeout_seconds: int = 30,
) -> BaseConnector:
    """Instantiate a connector for the given *connector_type*.

    Args:
        connector_type: Case-insensitive connector identifier, e.g.
            ``"postgresql"``, ``"salesforce"``, ``"snowflake"``, ``"mock"``.
        config: Connector-specific configuration dictionary (host, user,
            password, database, …).
        timeout_seconds: Default connection/query timeout in seconds.

    Returns:
        An uninitialised :class:`~preflight.core.infrastructure.connectors.base.BaseConnector`
        subclass.  Call ``await connector.connect()`` (or use it as an async
        context manager) before executing queries.

    Raises:
        ValueError: If *connector_type* is not registered.
    """
    key = connector_type.lower().strip()
    factory_or_cls = _REGISTRY.get(key)
    if factory_or_cls is None:
        supported = sorted(_REGISTRY.keys())
        raise ValueError(
            f"Unknown connector type: '{connector_type}'.  "
            f"Supported types: {supported}"
        )

    logger.debug("Creating connector for type=%s", key)

    # Some entries in the registry are factory functions (they handle the
    # real-vs-mock decision internally); others are plain classes.
    import inspect

    if inspect.isfunction(factory_or_cls):
        return factory_or_cls(config, timeout_seconds)
    else:
        return factory_or_cls(config, timeout_seconds)


def list_connector_types() -> list:
    """Return a sorted list of all registered connector type keys."""
    return sorted(_REGISTRY.keys())


def register_connector(key: str, factory_or_cls: Any) -> None:
    """Register a custom connector type at runtime.

    Args:
        key: Lowercase connector identifier.
        factory_or_cls: Either a :class:`BaseConnector` subclass or a factory
            function with signature ``(config: Dict, timeout_seconds: int) -> BaseConnector``.
    """
    key = key.lower().strip()
    if key in _REGISTRY:
        logger.warning("Overwriting existing connector registration for key='%s'", key)
    _REGISTRY[key] = factory_or_cls
    logger.info("Registered connector type: '%s'", key)
