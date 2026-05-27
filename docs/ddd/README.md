# Domain-Driven Design Documentation — Preflight Integration Tester

This directory contains the Domain-Driven Design (DDD) documentation for Preflight: the pre-purchase AI-readiness diagnostic platform. It describes the domain model, bounded contexts, ubiquitous language, and application services that implement the core diagnostic capabilities.

---

## What Is DDD and Why Preflight Uses It

Domain-Driven Design is a software design philosophy that places the business domain at the centre of technical decisions. Rather than organising code around technical layers (database, API, UI), DDD organises code around *domain concepts* — the entities, processes, and rules that exist in the real business.

Preflight uses DDD because:

- **The domain is the differentiator**: Preflight's value comes from accurately modelling how enterprise systems relate to one another, not from being a technically elegant HTTP server.
- **Multiple expert perspectives**: the codebase must be understandable to data engineers, enterprise architects, and product managers — a shared domain vocabulary prevents miscommunication.
- **Long-term maintainability**: the domain model is stable even as the technical implementation evolves; new connectors, analysis algorithms, or report formats slot in without restructuring.
- **Clear boundaries**: the system spans many conceptually distinct activities (connecting, simulating, analysing, reporting); bounded contexts keep these separate without becoming a distributed monolith.

---

## Domain Model Overview

Preflight's domain revolves around a single central concept: the **DiagnosticRun** — a time-bounded assessment of whether an enterprise's systems are ready for a specific AI deployment.

```
                         ┌─────────────────────────────┐
                         │       DiagnosticRun          │
                         │  (aggregate root)            │
                         │                              │
                         │  ┌──────────────────────┐   │
                         │  │  ConnectionSet        │   │
                         │  │  (which systems)      │   │
                         │  └──────────────────────┘   │
                         │  ┌──────────────────────┐   │
                         │  │  SimulationScenario   │   │
                         │  │  (what AI workload)   │   │
                         │  └──────────────────────┘   │
                         │  ┌──────────────────────┐   │
                         │  │  AnalysisResults      │   │
                         │  │  (what was found)     │   │
                         │  └──────────────────────┘   │
                         │  ┌──────────────────────┐   │
                         │  │  ReadinessReport      │   │
                         │  │  (verdict + plan)     │   │
                         │  └──────────────────────┘   │
                         └─────────────────────────────┘
```

A DiagnosticRun progresses through five phases:

```
PENDING ──▶ CONNECTING ──▶ SIMULATING ──▶ ANALYSING ──▶ REPORTING ──▶ COMPLETED
                │                │                │               │
                ▼                ▼                ▼               ▼
           ConnectionFailed  SimulationFailed  AnalysisFailed  ReportFailed
                │                │                │               │
                └────────────────┴────────────────┴───────────────┘
                                        │
                                        ▼
                                     FAILED
```

---

## Bounded Contexts

Preflight is structured into seven bounded contexts. Each context has its own:
- Domain model (entities, value objects, aggregates)
- Application services and use-case implementations
- Repository interfaces
- Set of domain events it publishes and subscribes to

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Connectivity   │────▶│   Simulation     │────▶│ Schema Analysis  │
│     Context      │     │     Context      │     │     Context      │
│                  │     │                  │     │                  │
│ Manages system   │     │ Models and runs  │     │ Maps entities,   │
│ connections and  │     │ AI workload      │     │ detects schema   │
│ credentials      │     │ simulations      │     │ inconsistencies  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
         │                        │                        │
         └────────────────────────┴────────────────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    │             │             │
         ┌──────────▼──┐  ┌──────▼──────┐  ┌──▼───────────────┐
         │  Pipeline   │  │ Middleware  │  │    Readiness     │
         │   Testing   │  │ Assessment  │  │    Reporting     │
         │   Context   │  │   Context   │  │     Context      │
         │             │  │             │  │                  │
         │ Stress tests│  │ Identifies  │  │ Aggregates all   │
         │ data pipes  │  │ integration │  │ findings into    │
         │ at AI load  │  │ layer gaps  │  │ score + report   │
         └─────────────┘  └─────────────┘  └──────────────────┘
                                  │
                         ┌────────▼─────────┐
                         │    Scenario      │
                         │    Modeling      │
                         │    Context       │
                         │                  │
                         │ What-if analysis │
                         │ and cost model   │
                         └──────────────────┘
```

Full context map and relationship details: [bounded-contexts.md](./bounded-contexts.md)

---

## DDD Documentation Index

| Document | Contents |
|----------|----------|
| [ubiquitous-language.md](./ubiquitous-language.md) | Complete glossary of all domain terms used throughout the codebase, documentation, and team communication |
| [bounded-contexts.md](./bounded-contexts.md) | All seven bounded contexts with full definitions, responsibilities, and the context map showing inter-context relationships |
| [domain-model.md](./domain-model.md) | Complete domain model: all aggregates, entities, value objects, and their invariants |
| [domain-events.md](./domain-events.md) | Full event catalogue: every domain event with payload schema, producing aggregate, and consuming contexts |
| [repositories.md](./repositories.md) | Repository interfaces: all persistence abstractions used by the domain layer |
| [application-services.md](./application-services.md) | Application services: the use-case orchestration layer that coordinates domain objects |

---

## How to Read This Documentation

**If you are new to the project**, read in this order:
1. [ubiquitous-language.md](./ubiquitous-language.md) — establish shared vocabulary
2. [bounded-contexts.md](./bounded-contexts.md) — understand the high-level structure
3. [domain-model.md](./domain-model.md) — understand the entities and their rules
4. [domain-events.md](./domain-events.md) — understand how contexts communicate

**If you are implementing a new feature**, read:
1. The relevant bounded context section in [bounded-contexts.md](./bounded-contexts.md)
2. The domain model entities in [domain-model.md](./domain-model.md) that the feature touches
3. Any new domain events the feature must produce or consume in [domain-events.md](./domain-events.md)
4. The repository interface in [repositories.md](./repositories.md) for persistence

**If you are adding a new connector**, read:
1. [bounded-contexts.md](./bounded-contexts.md) — Connectivity Context section
2. [ADR-003](../adr/ADR-003-read-only-connector-architecture.md) — connector plugin pattern

---

## DDD Layers

Preflight's code is structured in four layers following the DDD layered architecture:

```
┌───────────────────────────────────────┐
│           Interface Layer             │ FastAPI routes, CLI commands
│    (preflight/api/, preflight/cli/)   │
├───────────────────────────────────────┤
│         Application Layer            │ Use-case orchestration, DTOs
│   (preflight/*/application/)         │ Celery task coordination
├───────────────────────────────────────┤
│           Domain Layer               │ Aggregates, entities, value objects,
│    (preflight/core/domain/)          │ domain events, domain services,
│                                      │ repository interfaces
├───────────────────────────────────────┤
│        Infrastructure Layer          │ SQLAlchemy, Redis, connector SDKs,
│  (preflight/core/infrastructure/)    │ Jinja2, Playwright (PDF), Celery
└───────────────────────────────────────┘
```

The domain layer has **zero dependencies** on the infrastructure layer. Repository interfaces are defined in the domain layer and implemented in the infrastructure layer (Dependency Inversion Principle).

---

## Key DDD Patterns Applied

| Pattern | Where Used |
|---------|-----------|
| **Aggregate** | `DiagnosticRun`, `ConnectionProfile`, `AnalysisResult` — consistency boundaries |
| **Value Object** | `ReadinessScore`, `ReadinessVerdict`, `ConnectionCredentials`, `SeverityLevel` — immutable, identity-less |
| **Repository** | `DiagnosticRunRepository`, `ConnectionProfileRepository` — persistence abstraction |
| **Domain Event** | `DiagnosticRunStarted`, `ReadinessReportGenerated`, etc. — state change notifications |
| **Anti-Corruption Layer** | Connector adapters translate enterprise SDK data into domain model types |
| **Bounded Context** | Seven contexts with explicit interfaces and event-based integration |
| **Shared Kernel** | Common value objects (`SeverityLevel`, `EffortEstimate`) shared across contexts |
| **Context Map** | Upstream/downstream, ACL, and conformist relationships documented in bounded-contexts.md |
