# Preflight Integration Tester — CLAUDE.md

## Project Overview

**Preflight** is a pre-purchase AI readiness diagnostic tool for enterprises. It runs read-only analysis across ERP, CRM, and data warehouse systems to assess whether an organization's data infrastructure can support a proposed AI deployment.

**Core value proposition**: Turn "are we ready for AI?" from an opinion into a measured score with a prioritized remediation plan — before signing the contract.

## Architecture

```
preflight/
├── core/
│   ├── domain/          # Pure domain model (no dependencies)
│   │   ├── value_objects.py    # ReadinessScore, EffortEstimate, etc.
│   │   ├── entities.py         # SchemaInconsistency, MiddlewareGap, etc.
│   │   ├── aggregates.py       # DiagnosticRun (root), ReadinessReport
│   │   └── events.py           # Domain events
│   ├── application/
│   │   └── services/           # DiagnosticService, ReportService
│   └── infrastructure/
│       ├── connectors/         # Enterprise system connectors
│       ├── repositories/       # Data persistence
│       └── cache.py            # Redis/in-memory cache
├── analysis/
│   ├── schema_analyzer.py      # Cross-system schema comparison
│   ├── pipeline_tester.py      # Load simulation
│   ├── middleware_analyzer.py  # Gap detection
│   ├── data_quality.py         # Data quality checks
│   └── readiness_calculator.py # Score aggregation
├── api/
│   ├── app.py                  # FastAPI application factory
│   ├── schemas.py              # Pydantic request/response models
│   └── routes/                 # health, connections, diagnostics, reports
├── reporting/
│   ├── html_reporter.py        # HTML/PDF report generation
│   └── executive_summary.py   # Executive summary generation
└── cli/
    └── main.py                 # Click CLI
```

## Key Commands

### Install
```bash
pip install -e ".[dev]"
```

### Run Tests
```bash
# Unit tests only (fast)
pytest tests/unit/ -v

# All tests with coverage
pytest tests/ --cov=preflight --cov-report=term-missing

# Benchmarks
pytest tests/benchmarks/ -v -m benchmark

# Integration tests (need Docker services)
pytest tests/integration/ -v
```

### Run CLI Demo
```bash
python preflight.py run --config config.example.yml --mock
```

### Start API Server
```bash
uvicorn preflight.api.app:app --reload --port 8080
# API docs at: http://localhost:8080/docs
```

### Docker
```bash
docker-compose up -d
docker-compose logs -f preflight
```

## Domain Language (Ubiquitous Language)

- **DiagnosticRun**: A complete assessment session for one enterprise
- **ReadinessScore**: A 0–100 score representing AI deployment readiness
- **ReadinessVerdict**: GO (≥80), NOT_YET (50–79), NOT_READY (<50)
- **SchemaInconsistency**: A mismatch in how a business entity is defined across systems
- **PipelineBottleneck**: A performance degradation point under simulated AI load
- **MiddlewareGap**: A missing integration component required for the deployment
- **RemediationItem**: A specific action in the prioritized remediation plan
- **SimulationScenario**: The AI deployment use case and workload parameters
- **EntityMapping**: How a business entity (Customer, Order) is represented across systems

## Security Principles

**NEVER**:
- Execute write queries (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER)
- Store credentials in plain text (use env vars / secret refs)
- Log raw query data containing PII

**ALWAYS**:
- Validate all queries as read-only before execution
- Use least-privilege credentials
- Support VPC/self-hosted deployment

## Development Conventions

- Python type hints on all functions
- Docstrings on all classes and public methods
- Domain layer has ZERO external dependencies (pure Python)
- Application services may use infrastructure interfaces (via DI)
- Tests: unit tests in `tests/unit/`, integration in `tests/integration/`
- Async: use `asyncio` for I/O-bound operations (connector calls, cache)
- Config: YAML via `config.yml`, secrets via environment variables

## ADRs

See `docs/adr/` for all Architecture Decision Records:
- ADR-001: Python 3.11 as core language
- ADR-002: FastAPI with Pydantic v2
- ADR-003: Read-only connector plugin architecture
- ADR-004: Graph-based schema consistency analysis
- ADR-005: Async load simulation for pipeline testing
- ADR-006: PostgreSQL + Redis storage
- ADR-007: Zero-write security model
- ADR-008: Celery + Redis for background jobs
- ADR-009: Jinja2 HTML/PDF reporting
- ADR-010: Docker-first with K8s manifests

## DDD Documentation

See `docs/ddd/` for Domain-Driven Design documentation:
- Bounded context map
- Domain model (aggregates, entities, value objects)
- Ubiquitous language glossary
- Domain events catalogue
- Repository interfaces
- Application services
