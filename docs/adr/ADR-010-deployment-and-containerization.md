# ADR-010: Deployment and Containerization

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, DevOps Lead, Sales Engineering  
**Technical Story**: [PRD-002 §3, §5.1](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight handles production enterprise credentials and touches live enterprise systems. Enterprise security teams require deployment options that address:

1. **Data residency**: production data and credentials must not leave the enterprise network perimeter.
2. **Network isolation**: the diagnostic tool must not have internet access during a run.
3. **Auditability**: infrastructure must be auditable and reproducible.
4. **Approval process**: enterprise procurement requires a known, approved deployment pattern.
5. **Vendor lock-in avoidance**: buyers are wary of solutions that require cloud-specific services they don't control.

Simultaneously, Preflight must be easy enough to deploy that a customer's infrastructure team can stand it up in a day — not a week-long professional services engagement.

The system components are:

| Component | Role |
|-----------|------|
| FastAPI application | REST API, WebSocket progress streaming |
| Celery workers (2–4) | Background diagnostic task execution |
| Redis | Task queue, schema cache, session state |
| PostgreSQL | Diagnostic results, reports, audit log |
| React frontend | Dashboard (served as static files by FastAPI or Nginx) |
| Flower (optional) | Celery monitoring UI |

These components must be orchestrated consistently across: local development, CI/CD, SaaS hosted, and customer VPC environments.

---

## Decision Drivers

- Enterprise security approval: VPC / on-prem deployment must be a first-class option
- Operational simplicity: one-day deployment for a customer's infrastructure team
- Environment consistency: dev, staging, and production must be identical except for configuration
- Horizontal scaling: Celery workers must scale independently of the API layer
- Cloud portability: no cloud-vendor-specific services in the core architecture
- Security hardening: minimal attack surface in containerised deployment
- Update mechanism: customers need a clear path to upgrade to new versions

---

## Considered Options

### Option A: Docker-first with Kubernetes manifests, VPC deployment option (chosen)
### Option B: PaaS only (Heroku / Render / Railway)
### Option C: Bare-metal install (pip install + systemd)
### Option D: Serverless (AWS Lambda / Google Cloud Run)

---

## Decision Outcome

**Chosen option: Docker-first with Kubernetes manifests and a Helm chart; VPC deployment as a first-class target**.

### Container Architecture

```
preflight/
├── Dockerfile                    # Multi-stage: builder + slim runtime image
├── docker-compose.yml            # Local development and single-node deployment
├── docker-compose.override.yml   # Dev-only overrides (hot-reload, debug ports)
└── k8s/
    ├── namespace.yaml
    ├── api/
    │   ├── deployment.yaml       # FastAPI; 2+ replicas
    │   ├── service.yaml
    │   └── hpa.yaml              # Horizontal Pod Autoscaler
    ├── worker/
    │   ├── deployment.yaml       # Celery workers; 2+ replicas
    │   └── hpa.yaml
    ├── redis/
    │   └── statefulset.yaml      # Redis (or external Redis via Secret)
    ├── postgres/
    │   └── statefulset.yaml      # PostgreSQL (or external via Secret)
    ├── configmap.yaml            # Non-secret configuration
    ├── secrets.yaml.template     # Secret keys (customer fills in)
    ├── network-policy.yaml       # Egress: enterprise systems only; deny internet
    └── ingress.yaml              # TLS termination
```

### Dockerfile (Multi-Stage)

```dockerfile
# Stage 1: Builder — install dependencies
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Stage 2: Runtime — minimal image
FROM python:3.11-slim AS runtime
WORKDIR /app

# Security: non-root user
RUN useradd --uid 1000 --no-create-home preflight
COPY --from=builder /root/.local /home/preflight/.local
COPY --chown=preflight:preflight . .
USER preflight

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
  CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080
CMD ["uvicorn", "preflight.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Docker Compose — Single-Node VPC Deployment

```yaml
# docker-compose.yml — production single-node
services:
  api:
    image: preflight:latest
    ports: ["8080:8080"]
    environment:
      DATABASE_URL: postgresql+asyncpg://preflight:${DB_PASSWORD}@postgres/preflight
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      SECRET_KEY: ${SECRET_KEY}
    depends_on: [postgres, redis]
    networks: [internal, enterprise-systems]

  worker:
    image: preflight:latest
    command: celery -A preflight.core.infrastructure.celery_app worker --concurrency=4
    environment: *api-environment
    depends_on: [postgres, redis]
    networks: [internal, enterprise-systems]
    deploy:
      replicas: 2

  postgres:
    image: postgres:16-alpine
    volumes: [postgres-data:/var/lib/postgresql/data]
    environment:
      POSTGRES_DB: preflight
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    networks: [internal]

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes: [redis-data:/data]
    networks: [internal]

networks:
  internal:
  enterprise-systems:
    external: true  # Customer-managed network with routes to enterprise systems

volumes:
  postgres-data:
  redis-data:
```

### Kubernetes — VPC Production Deployment

The K8s manifests provide:

- **Network Policy**: restricts Preflight pod egress to explicitly configured enterprise system CIDRs; blocks internet egress entirely.
- **Resource limits**: CPU and memory requests/limits defined per component to prevent resource contention.
- **HPA**: API pods scale on CPU (target 60%); worker pods scale on Celery queue depth (via Prometheus KEDA trigger).
- **Secret management**: K8s Secrets store DB passwords, API keys; ExternalSecrets operator integration for AWS Secrets Manager, Azure Key Vault, HashiCorp Vault.
- **PodDisruptionBudget**: minimum 1 API pod available during rolling updates.
- **Pod security context**: `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`.

### Deployment Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `docker-compose up` | Single host, all components | Customer evaluation, small deployments |
| `kubectl apply -f k8s/` | Multi-node K8s | Enterprise VPC production |
| Helm chart (`helm install preflight ./chart`) | Parameterised K8s | Standardised enterprise install |
| Preflight SaaS | Preflight-hosted | Customers without VPC infrastructure requirement |

### Upgrade Path

```bash
# Pull new image
docker pull preflight/preflight:1.2.0

# Run database migrations before updating app
docker run --rm preflight/preflight:1.2.0 alembic upgrade head

# Rolling update (K8s)
kubectl set image deployment/preflight-api api=preflight/preflight:1.2.0
```

### Positive Consequences

- **Enterprise approval**: Docker + K8s are universally approved deployment patterns in enterprise security reviews; no novel concepts for the customer's infrastructure team to approve.
- **VPC isolation**: the `network-policy.yaml` limits egress to only the enterprise systems specified in the run configuration; no internet connectivity during diagnostic runs.
- **Environment parity**: the same Docker image runs in development (docker-compose), CI (docker-compose), and production (K8s); eliminates "works on my machine" issues.
- **Independent scaling**: Celery worker replicas scale independently of the API layer; during a heavy diagnostic run, workers scale up without adding API capacity.
- **Non-root containers**: security-hardened base image with non-root user reduces container escape risk.
- **Helm chart**: parameterised install allows customers to override image registry, resource limits, and ingress configuration without editing raw YAML.

### Negative Consequences

- Kubernetes is operationally complex; customers without K8s experience need guidance (mitigated by docker-compose single-node option and deployment runbook).
- Multi-stage Docker build produces a large image (~800 MB) due to enterprise connector libraries; separate slim images per connector type are a future optimisation.
- Headless Chromium for PDF generation (ADR-009) adds ~200 MB to the image; it may be extracted to a separate `preflight-pdf` sidecar in v2.
- K8s manifest maintenance must stay in sync with application changes; HelmDocs generates documentation from chart values.

---

## Alternatives Considered

### Option B: PaaS Only (Heroku / Render / Railway)

| Criterion | Assessment |
|-----------|-----------|
| Operational simplicity | Very high for small deployments |
| Data residency | Impossible — data processed on PaaS provider infrastructure |
| Enterprise approval | Blocked by most enterprise security policies for production data |
| Customisation | Limited; cannot add custom network policies |
| Verdict | **Rejected** — data residency requirement cannot be met; most enterprise procurement will not approve |

### Option C: Bare-Metal Install (pip install + systemd)

| Criterion | Assessment |
|-----------|-----------|
| Simplicity | Low — managing Python virtualenvs, systemd services, and process supervision is error-prone |
| Environment parity | Low — production environment drifts from development |
| Dependency management | Enterprise system connector libraries have complex native dependencies (Oracle OCI, SAP RFC) that are hard to install without containers |
| Scaling | Manual process management; no auto-scaling |
| Verdict | **Rejected** — dependency management complexity and lack of scaling capability make this a support liability |

### Option D: Serverless (AWS Lambda / Google Cloud Run)

| Criterion | Assessment |
|-----------|-----------|
| Data residency | Limited control; data processed in cloud provider infrastructure |
| Cold start | Unacceptable for multi-hour diagnostic runs; functions time out |
| Stateful connections | Connector connections cannot persist across Lambda invocations |
| Cloud lock-in | Vendor-specific APIs conflict with cloud-neutral positioning |
| Verdict | **Rejected** — function timeout limits (15 min Lambda max) are incompatible with multi-hour diagnostic runs; cloud-vendor lock-in conflicts with enterprise portability requirement |

---

## Implementation Notes

- Base image: `python:3.11-slim` (Debian-based; smaller than `python:3.11`; avoids Alpine Python ABI issues with native connector libraries).
- CI/CD: GitHub Actions builds the image on every merge to `main`; images tagged with git SHA and semantic version; pushed to GitHub Container Registry.
- Health check: `GET /health` returns `{"status": "ok", "db": "ok", "redis": "ok", "celery": "ok"}` within 10 seconds.
- Secrets: never baked into the image; injected via environment variables (Docker Compose `env_file` or K8s Secrets).
- Helm chart values documented in `k8s/chart/values.yaml` with inline comments.

---

## Links

- [ADR-006: Data Storage Architecture](./ADR-006-data-storage-architecture.md)
- [ADR-007: Security and Credential Management](./ADR-007-security-and-credential-management.md)
- [ADR-008: Async Processing Architecture](./ADR-008-async-processing-architecture.md)
- [Kubernetes manifests](../../k8s/)
- [Deployment Guide](../deployment/)
