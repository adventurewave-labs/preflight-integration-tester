# ADR-002: API Framework Selection

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, Backend Team  
**Technical Story**: [PRD-002 §5](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight exposes a REST API consumed by three distinct clients:

1. **React dashboard**: live job status, schema maps, pipeline charts, report rendering.
2. **CLI tool**: headless execution for enterprise scripting and CI environments.
3. **External integrations**: customer-facing API for programmatic access to diagnostic results.

The API layer must handle:

- Long-running background jobs (diagnostic runs take minutes to hours) with polling or streaming progress.
- Strongly typed request/response bodies derived from the domain model.
- Interactive OpenAPI documentation for enterprise buyers who want to evaluate the API before integrating.
- High-concurrency simultaneous connector calls during a single diagnostic session.
- Strict input validation to prevent misconfigured connector credentials from causing opaque errors.

---

## Decision Drivers

- Native async I/O to match Python 3.11's `asyncio` concurrency model
- Automatic OpenAPI 3.x schema generation from Python type annotations
- Runtime request/response validation with meaningful error messages
- WebSocket or Server-Sent Events support for job progress streaming
- Developer experience: rapid iteration without boilerplate
- Production maturity and community support

---

## Considered Options

### Option A: FastAPI + Pydantic v2 (chosen)
### Option B: Flask + Marshmallow
### Option C: Django REST Framework
### Option D: Tornado

---

## Decision Outcome

**Chosen option: FastAPI with Pydantic v2**, because it is the only option that provides async-first execution, automatic OpenAPI generation, and Pydantic v2 schema validation as a unified, zero-configuration bundle.

### Positive Consequences

- **Automatic OpenAPI 3.x docs**: Swagger UI and ReDoc available at `/docs` and `/redoc` with no additional configuration; enterprise buyers can explore the API without a separate documentation site.
- **Pydantic v2 validation**: every request body and response is validated at runtime using Pydantic models that are also the domain model's value objects — a single source of truth.
- **Async-native**: `async def` route handlers integrate directly with `asyncio`, `aiohttp`, and `asyncio.to_thread()` for connector calls; no WSGI wrapper required.
- **WebSocket support**: FastAPI's native WebSocket routes enable real-time job progress streaming to the React dashboard.
- **Dependency injection**: FastAPI's `Depends()` system cleanly manages database sessions, connector pools, and authentication context per request.
- **Background tasks**: `BackgroundTasks` handles lightweight fire-and-forget jobs; heavy jobs delegate to Celery (see ADR-008).
- **Testing**: `httpx.AsyncClient` + `pytest-asyncio` provides ergonomic async test coverage.

### Negative Consequences

- FastAPI is a micro-framework; batteries (admin UI, ORM, auth) must be assembled from separate packages.
- Pydantic v2 has a steeper learning curve than v1; some legacy connector SDK patterns require adapter layers.
- No built-in job queue; Celery integration must be maintained separately (see ADR-008).
- WebSocket handling at scale requires careful connection lifecycle management.

---

## Alternatives Considered

### Option B: Flask + Marshmallow

| Criterion | Assessment |
|-----------|-----------|
| Async support | WSGI-native; async requires Quart fork or awkward workarounds |
| Schema generation | Not automatic; requires `flask-smorest` or `flasgger` plugins with manual annotation |
| Validation | Marshmallow is capable but a separate dependency layer from the domain model |
| Maturity | Very mature; large ecosystem |
| Verdict | **Rejected** — WSGI's synchronous nature is architecturally incompatible with concurrent connector I/O; the async workarounds add complexity without eliminating the limitation |

### Option C: Django REST Framework

| Criterion | Assessment |
|-----------|-----------|
| Async support | Django 4.1+ adds async views; DRF itself is still largely synchronous |
| Schema generation | `drf-spectacular` provides good OpenAPI generation |
| Validation | Serializers provide validation but are not the domain model |
| Maturity | Very mature; batteries included (ORM, auth, admin) |
| Coupling | Django's ORM and admin presuppose a project structure that conflicts with the DDD architecture |
| Verdict | **Rejected** — DRF's synchronous serializer pipeline and Django's monolithic assumptions clash with the async-first, DDD-modular architecture |

### Option D: Tornado

| Criterion | Assessment |
|-----------|-----------|
| Async support | Async-native; predates asyncio but now integrated |
| Schema generation | No built-in OpenAPI; requires manual `apispec` integration |
| Validation | No built-in Pydantic integration; would require custom middleware |
| Maturity | Mature but declining community investment |
| Verdict | **Rejected** — lacks the developer-experience advantages of FastAPI with no compensating benefits for this use case |

---

## Implementation Notes

- **Pydantic v2** models are defined in `preflight/core/domain/` and imported directly into FastAPI route definitions — domain models serve as both API contracts and internal type constraints.
- **Router organisation**: routes are split by bounded context — `/api/v1/connections/`, `/api/v1/diagnostics/`, `/api/v1/reports/`, `/api/v1/scenarios/`.
- **Versioning**: URL path versioning (`/api/v1/`) to maintain enterprise client stability.
- **Authentication**: OAuth2 Bearer + API key support via FastAPI's `oauth2_scheme` dependency; see ADR-007 for credential management details.
- **OpenAPI customisation**: `tags`, `summary`, and `description` fields are populated on every route to produce enterprise-grade API documentation.

---

## Links

- [ADR-001: Core Language and Runtime](./ADR-001-core-language-and-runtime.md)
- [ADR-007: Security and Credential Management](./ADR-007-security-and-credential-management.md)
- [ADR-008: Async Processing Architecture](./ADR-008-async-processing-architecture.md)
- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [Pydantic v2 documentation](https://docs.pydantic.dev/latest/)
