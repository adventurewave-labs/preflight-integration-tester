# ADR-001: Core Language and Runtime

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, Architecture Review Board  
**Technical Story**: [PRD-002 §5](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight must connect read-only to 15+ enterprise systems, simulate AI workload patterns, analyse schema consistency across heterogeneous data stores, and produce richly formatted diagnostic reports — all in a single coherent codebase. The choice of primary language sets the ecosystem for libraries, connector SDKs, developer hiring, and long-term maintainability.

Key constraints:

- **Enterprise connector availability**: SAP, Salesforce, Oracle, Snowflake, Databricks, and major RDBMS all need SDK-level integration.
- **Data analysis**: schema mapping, graph-based entity analysis, fuzzy string matching, and statistical pipeline profiling require mature numerical and ML libraries.
- **Async I/O**: simultaneous read-only connections to multiple systems during a single diagnostic run demand non-blocking I/O.
- **Type safety**: complex domain models (ReadinessScore, PipelineMetrics, EntityMapping) must be expressed reliably to prevent category errors at the API layer.
- **Team familiarity**: the initial team has deep Python expertise; a language switch carries ramp-up cost.

---

## Decision Drivers

- Richness of enterprise connector SDK ecosystem
- Native data science and graph-analysis library support
- First-class async I/O support
- Strong typing and validation tooling
- Community size and long-term viability
- Time-to-market for the MVP

---

## Considered Options

### Option A: Python 3.11 (chosen)
### Option B: Go 1.22
### Option C: Java 21 (LTS)
### Option D: Node.js 20 LTS

---

## Decision Outcome

**Chosen option: Python 3.11**, because it provides the richest ecosystem for every dimension of the problem:

- Enterprise connectors (`salesforce-api`, `cx-oracle`, `snowflake-connector-python`, `sap-rfc`) are mature and actively maintained.
- Data analysis and ML (`pandas`, `numpy`, `scikit-learn`, `networkx`, `fuzzywuzzy`) are best-in-class.
- `asyncio` and `aiohttp` provide production-grade async I/O for concurrent connector calls.
- Pydantic v2 (with Rust-backed validation) provides type-safe domain modelling without sacrificing ergonomics.
- Python 3.11 specifically introduced significant performance improvements (10–25% faster interpreter, better error messages) over 3.9/3.10.

### Positive Consequences

- Immediate access to enterprise SDK library catalogue without custom wrappers.
- `pandas` + `networkx` enable graph-based schema entity mapping out of the box.
- `asyncio` enables concurrent reads across many connectors without thread-pool management.
- Pydantic v2 models serve as both runtime validators and OpenAPI schema generators (via FastAPI).
- Large hiring pool; extensive open-source tooling.
- `Celery` + `Redis` job queue and `Jinja2` templating for reporting are production-proven Python idioms.

### Negative Consequences

- Python's GIL constrains true CPU parallelism; CPU-bound analysis work must use `multiprocessing` or be delegated to NumPy/Rust-backed libraries.
- Memory overhead is higher than Go or Java for the same concurrency level; container resource limits must be set thoughtfully.
- Dynamic typing increases the importance of rigorous Pydantic model coverage and test discipline.
- Deployment container size is larger than Go's single-binary option.

---

## Alternatives Considered

### Option B: Go 1.22

| Criterion | Assessment |
|-----------|-----------|
| Enterprise connectors | Very few; requires writing custom REST/SQL wrappers for SAP, Salesforce, Oracle |
| Data analysis | No native equivalent of pandas/networkx; would require CGo calls or external services |
| Async I/O | Excellent via goroutines and channels |
| Typing | Strong static typing |
| Verdict | **Rejected** — connector and analysis library gaps are blockers for the domain |

### Option C: Java 21 (LTS)

| Criterion | Assessment |
|-----------|-----------|
| Enterprise connectors | Mature JDBC and vendor SDKs; best SAP RFC support |
| Data analysis | Apache Spark, Weka; heavyweight for a diagnostic tool |
| Async I/O | Virtual threads (Project Loom) are excellent in Java 21 |
| Typing | Strong static typing |
| Deployment | JVM startup time and memory footprint are high; container warm-up is a UX concern |
| Verdict | **Rejected** — JVM overhead conflicts with lightweight VPC deployment goal; team expertise mismatch |

### Option D: Node.js 20 LTS

| Criterion | Assessment |
|-----------|-----------|
| Enterprise connectors | Thin wrappers around REST APIs; no native SAP RFC or Oracle OCI |
| Data analysis | No native numerical library; would require Python subprocess or WASM |
| Async I/O | Excellent; event loop native |
| Typing | TypeScript adds types but not Python/Pydantic-level schema generation |
| Verdict | **Rejected** — data analysis capability gap is a fundamental blocker |

---

## Implementation Notes

- Minimum Python version pinned at **3.11.x** in `pyproject.toml` and Docker base image (`python:3.11-slim`).
- All new code uses type annotations; `mypy --strict` is run in CI.
- CPU-intensive analysis (graph layout, fuzzy matching at scale) uses `ProcessPoolExecutor` or delegates to Rust-backed library internals (`rapidfuzz`, `polars`).
- `asyncio` is the default concurrency model; blocking SDK calls are wrapped with `asyncio.to_thread()`.

---

## Links

- [Requirements](../../requirements.txt)
- [ADR-002: API Framework](./ADR-002-api-framework-selection.md)
- [ADR-008: Async Processing Architecture](./ADR-008-async-processing-architecture.md)
