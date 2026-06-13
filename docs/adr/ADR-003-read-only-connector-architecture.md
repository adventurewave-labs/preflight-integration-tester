# ADR-003: Read-Only Connector Architecture

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, Security Architect, Integration Team  
**Technical Story**: [PRD-002 §5.1](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight must connect to 15+ distinct enterprise systems — SAP S/4HANA, Oracle ERP, Salesforce, Snowflake, Databricks, PostgreSQL, MySQL, MongoDB, and others — each with:

- A unique authentication model (OAuth2, API key, JWT, SAML, basic auth, JDBC, RFC)
- A unique SDK or protocol for data access
- Different metadata introspection APIs for schema discovery
- Different concurrency limits, rate limits, and timeout behaviours
- Different handling of read-only constraints (some systems have no built-in read-only mode)

The connector layer must be:

- **Extensible**: new connectors added without changing core diagnostic logic.
- **Testable in isolation**: each connector should be unit-testable with mocked responses.
- **Read-only enforced**: zero path to write operations regardless of the underlying SDK.
- **Independently deployable**: connectors that require heavyweight native libraries (SAP RFC, Oracle OCI) should not force those dependencies on deployments that don't use those systems.

---

## Decision Drivers

- Safety: zero-write guarantee must be enforced at the architecture level, not just by policy
- Extensibility: new connectors must not require changes to the diagnostic engine
- Testability: connector behaviour must be mockable for unit and integration tests
- Isolation: heavyweight native dependencies must be optional
- Observability: connection health, latency, and errors must surface consistently across all connectors
- Credential security: credentials must not leak between connector instances

---

## Considered Options

### Option A: Abstract base connector with plugin pattern (chosen)
### Option B: Direct SDK usage inline in diagnostic agents
### Option C: Single monolithic connector with system-type dispatch
### Option D: GraphQL federation over all enterprise systems

---

## Decision Outcome

**Chosen option: Abstract base connector with plugin pattern**.

Each enterprise system is implemented as a concrete `BaseConnector` subclass registered in a connector registry. The diagnostic engine programmes to the `BaseConnector` interface only; it never imports a concrete connector class.

### Core Interface

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from preflight.core.domain import (
    ConnectionCredentials, SystemMetadata, SchemaSnapshot, QueryResult
)

class BaseConnector(ABC):
    """Read-only interface to an enterprise system."""

    system_type: str  # e.g. "salesforce", "sap_s4hana", "snowflake"

    @abstractmethod
    async def connect(self, credentials: ConnectionCredentials) -> None:
        """Establish and validate a read-only connection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Release all connection resources cleanly."""

    @abstractmethod
    async def introspect_schema(self) -> SchemaSnapshot:
        """Return metadata about all accessible entities and fields."""

    @abstractmethod
    async def execute_read_query(self, query: str, params: dict) -> QueryResult:
        """Execute a read-only query; raise ConnectorWriteAttemptError if query is not read-only."""

    @abstractmethod
    async def health_check(self) -> ConnectionStatus:
        """Verify the connection is live and the credential is still valid."""

    @abstractmethod
    async def stream_records(
        self, entity: str, filters: dict, page_size: int = 1000
    ) -> AsyncIterator[list[dict]]:
        """Stream records in pages to support large-scale schema sampling."""

    # Concrete enforcement — subclasses cannot override this
    final def assert_read_only(self, operation: str) -> None:
        if operation.upper().startswith(("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER")):
            raise ConnectorWriteAttemptError(
                f"Write operation '{operation}' blocked by Preflight read-only enforcement."
            )
```

### Plugin Registry

```python
# preflight/connectors/registry.py
_CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {}

def register_connector(system_type: str):
    def decorator(cls: type[BaseConnector]):
        _CONNECTOR_REGISTRY[system_type] = cls
        return cls
    return decorator

def get_connector(system_type: str) -> type[BaseConnector]:
    if system_type not in _CONNECTOR_REGISTRY:
        raise ConnectorNotFoundError(f"No connector registered for '{system_type}'")
    return _CONNECTOR_REGISTRY[system_type]
```

Concrete connectors self-register via decorator:

```python
@register_connector("salesforce")
class SalesforceConnector(BaseConnector):
    system_type = "salesforce"
    ...
```

### Positive Consequences

- **Zero-write guarantee at the type level**: `execute_read_query()` enforces read-only on every call; no connector can accidentally open a write path.
- **Open/closed**: new connectors are added by creating a new file in `preflight/connectors/` and applying the `@register_connector` decorator; zero changes to the diagnostic engine.
- **Independent deployment**: each connector's dependencies (e.g., `sap-rfc`) are optional extras in `pyproject.toml`; customers install only what their stack requires.
- **Uniform observability**: `health_check()`, latency tracking, and error normalisation are defined in `BaseConnector` and apply consistently to all concrete implementations.
- **Test doubles**: the diagnostic engine tests use `MockConnector(BaseConnector)` without importing any enterprise SDK.
- **Parallel introspection**: the diagnostic engine can call `introspect_schema()` on multiple connectors concurrently via `asyncio.gather()`.

### Negative Consequences

- Each new connector requires implementing and testing all abstract methods — a non-trivial one-time cost per system.
- The abstract interface must be versioned carefully; adding a new abstract method is a breaking change for all existing connectors.
- Some enterprise SDK patterns (synchronous-only, callback-based) must be wrapped with `asyncio.to_thread()`, adding overhead.

---

## Alternatives Considered

### Option B: Direct SDK Usage Inline in Diagnostic Agents

Direct calls to `simple_salesforce.Salesforce()`, `snowflake.connector.connect()`, etc., from within the diagnostic agent code.

| Criterion | Assessment |
|-----------|-----------|
| Safety | No read-only enforcement layer; SDK calls could write if misconfigured |
| Extensibility | Adding a new system requires modifying multiple agent files |
| Testability | Must mock every SDK call site individually |
| Isolation | All SDK dependencies always required |
| Verdict | **Rejected** — violates the zero-write safety requirement and produces tight coupling |

### Option C: Single Monolithic Connector with System-Type Dispatch

One `EnterpriseConnector` class with `if system_type == "salesforce": ...` branches.

| Criterion | Assessment |
|-----------|-----------|
| Safety | Read-only enforcement could be centralised |
| Extensibility | Every new system adds branches to a shared file; high merge conflict risk |
| Testability | Unit-testing one system requires the full monolith with all dependencies |
| Isolation | Not achievable; all systems bundled |
| Verdict | **Rejected** — does not scale beyond 3–4 systems; becomes a maintenance liability |

### Option D: GraphQL Federation over All Enterprise Systems

Deploy a GraphQL gateway that federates schema from all enterprise systems; diagnostic agents query the gateway.

| Criterion | Assessment |
|-----------|-----------|
| Safety | Gateway can enforce read-only operations |
| Extensibility | Adding a new system requires a new GraphQL subgraph |
| Operational complexity | Requires a running federation gateway for every diagnostic session |
| Schema fidelity | Federation schemas necessarily abstract away enterprise-system specifics that Preflight needs to analyse |
| Verdict | **Rejected** — the abstraction layer hides the schema inconsistencies Preflight is designed to detect |

---

## Implementation Notes

- Connector packages live in `preflight/connectors/<system_type>/`.
- Each package exports exactly one `BaseConnector` subclass.
- Optional dependency groups in `pyproject.toml`: `pip install preflight[salesforce]`, `pip install preflight[sap]`, `pip install preflight[all]`.
- `ConnectionCredentials` is a Pydantic v2 model with `SecretStr` fields; credentials are never logged.
- `SchemaSnapshot` is immutable (frozen Pydantic model); schema introspection results are cached in Redis (see ADR-006).

---

## Links

- [ADR-001: Core Language and Runtime](./ADR-001-core-language-and-runtime.md)
- [ADR-004: Schema Consistency Analysis Strategy](./ADR-004-schema-consistency-analysis-strategy.md)
- [ADR-007: Security and Credential Management](./ADR-007-security-and-credential-management.md)
- [DDD: Connectivity Context](../ddd/bounded-contexts.md)
