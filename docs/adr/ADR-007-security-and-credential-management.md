# ADR-007: Security and Credential Management

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, Security Architect, CISO Reviewer  
**Technical Story**: [PRD-002 §5.1, §3 Non-goals](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight must handle production enterprise credentials for systems that contain some of the most sensitive data an enterprise owns: ERP financial data, CRM customer records, data warehouse analytics. This creates a significant trust challenge.

Enterprise buyers will ask — and rightly so:

1. Who controls the credentials?
2. Where are credentials stored?
3. Can Preflight personnel see production data?
4. Is there an audit trail of every data access?
5. Does Preflight comply with SOC2 / ISO 27001 / GDPR?
6. Can we run this in our own VPC so data never leaves our perimeter?

The security architecture must provide unambiguous, evidence-based answers to all of these questions. A policy statement ("we don't look at your data") is insufficient; the architecture must make it technically impossible or immediately auditable.

Additionally, the zero-write guarantee (a core product promise) must be enforced at multiple layers — not just by documentation or policy.

---

## Decision Drivers

- Credential sovereignty: customers must control their own credentials
- Zero-write enforcement: enforced at architecture level, not just policy
- Data residency: enterprise buyers need deployment options where data never leaves their perimeter
- Auditability: every data access logged in an immutable, customer-accessible audit trail
- Least privilege: connectors request only the minimum permissions required for read operations
- SOC2 Type II readiness: security architecture must support audit evidence collection
- GDPR compliance: data subject rights and retention limits must be enforceable

---

## Considered Options

### Option A: Customer-controlled credentials, VPC-ready, zero-write enforcement (chosen)
### Option B: Preflight-managed credential vault
### Option C: OAuth2 proxy with delegated access
### Option D: Static credential configuration files

---

## Decision Outcome

**Chosen option: Customer-controlled credentials with VPC deployment option and multi-layer zero-write enforcement**.

### Credential Architecture

#### Principle 1 — Customer Credential Sovereignty

Preflight never stores credentials in Preflight-operated infrastructure. Credentials are encrypted by the customer and injected at diagnostic runtime.

```
Customer Environment                    Preflight Engine
────────────────────                    ────────────────
Customer HSM / KMS                      
       │                               
       ▼                               
Encrypted credential bundle   ──────▶  Decrypted in memory only
(AES-256-GCM)                          for duration of run
                                        │
                                        ├── BaseConnector.connect()
                                        └── Zeroed from memory on
                                            disconnect()
```

**Implementation**:
- Credentials submitted as `ConnectionCredentials` (Pydantic `SecretStr` fields).
- Never written to disk or database in plaintext — only `credential_reference_id` (opaque handle) stored in PostgreSQL.
- In VPC deployments, customer provides credentials via their own secrets manager (AWS Secrets Manager, Azure Key Vault, HashiCorp Vault); Preflight fetches at runtime.
- In SaaS deployments, customer-provided credentials are encrypted with a customer-specific KEK before transit; Preflight decrypts only in memory during the run.

#### Principle 2 — Zero-Write Enforcement (Multi-Layer)

```
Layer 1 — Connector Interface (ADR-003)
   BaseConnector.execute_read_query() calls assert_read_only()
   → Raises ConnectorWriteAttemptError on any DML/DDL keyword

Layer 2 — Database User Permissions
   Connector DB credentials provisioned with SELECT-only grants
   → Database engine rejects writes at the protocol level

Layer 3 — API Audit Logging
   Every connector call logged with query hash, system, timestamp
   → Immutable audit log for post-hoc verification

Layer 4 — Network Policy (VPC deployments)
   Egress rules allow outbound connections to customer systems
   Ingress rules block connections from Preflight to the internet
   → No exfiltration path even if code is compromised
```

#### Principle 3 — VPC / Self-Hosted Deployment

Enterprise buyers may deploy the Preflight diagnostic engine entirely within their own VPC:

```
Customer VPC
┌──────────────────────────────────────────────────┐
│  Preflight Engine (Docker / K8s)                 │
│  ┌─────────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ FastAPI     │  │ Celery   │  │ PostgreSQL  │ │
│  │ (API layer) │  │ Workers  │  │ (results)   │ │
│  └─────────────┘  └──────────┘  └─────────────┘ │
│            │           │                          │
│            └───────────┴──── Read-only ──────────┼──▶ ERP
│                                                  │
│                                                  ├──▶ CRM
│                                                  │
│                                                  └──▶ Warehouse
│                                                  │
│  No traffic leaves the customer VPC              │
└──────────────────────────────────────────────────┘
```

In VPC mode: no diagnostic data, no schema snapshots, and no credentials leave the customer's network perimeter.

#### Principle 4 — Audit Logging

Every connector call is logged to an append-only `audit_log` table:

```sql
audit_log (
    id           BIGSERIAL PRIMARY KEY,
    run_id       UUID NOT NULL,
    system_type  VARCHAR(64),
    operation    VARCHAR(32),  -- 'introspect_schema', 'execute_read_query', 'stream_records'
    entity       VARCHAR(255),
    query_hash   VARCHAR(64),  -- SHA-256 of the query; not the query itself
    duration_ms  INTEGER,
    row_count    INTEGER,
    timestamp    TIMESTAMPTZ DEFAULT now(),
    agent_id     VARCHAR(64)
);
```

- Append-only: no UPDATE or DELETE on `audit_log`.
- Exported to customer SIEM (Splunk, Datadog, etc.) in VPC deployments.

#### Principle 5 — Least Privilege Credential Guidance

Preflight provides per-system least-privilege credential setup guides:

| System | Required Permission Set |
|--------|------------------------|
| PostgreSQL | `GRANT SELECT ON ALL TABLES IN SCHEMA public TO preflight_user` |
| Salesforce | "Preflight Connected App" with `api`, `chatter_api` (read-only) |
| SAP S/4HANA | RFC authorisation: `S_RFC` with `FUNC_GROUP = SYST` (read) |
| Snowflake | `GRANT USAGE ON DATABASE, SELECT ON ALL TABLES` |

### Positive Consequences

- **Customer credential sovereignty**: Preflight cannot access customer systems without a credential actively provided for the current run.
- **Zero-write guarantee**: four independent enforcement layers; would require simultaneous failure of all four to write to a production system.
- **Data residency**: VPC deployment option means no customer data ever leaves the enterprise perimeter.
- **SOC2 readiness**: audit log, least-privilege guidance, and encryption controls provide SOC2 Type II evidence.
- **GDPR compliance**: retention policy applied to diagnostic results; customer can request deletion.

### Negative Consequences

- VPC deployment complexity: customers must provision and maintain the Docker/K8s infrastructure (mitigated by Helm chart and runbooks in ADR-010).
- Customer credential setup requires per-system least-privilege configuration; customers may find this time-consuming (mitigated by guided setup wizard in v1).
- In-memory-only credential handling requires careful memory zeroing; Python's garbage collector is not deterministic enough to guarantee immediate zeroing without explicit `ctypes` clearing.

---

## Alternatives Considered

### Option B: Preflight-Managed Credential Vault

Preflight operates a HashiCorp Vault instance; customers submit credentials which Preflight manages on their behalf.

| Criterion | Assessment |
|-----------|-----------|
| Customer control | None — Preflight has full access to all customer credentials |
| Trust requirement | Requires customers to trust Preflight with production credentials indefinitely |
| Compliance | Creates Preflight as a data processor under GDPR; significant compliance burden |
| Verdict | **Rejected** — eliminates the core customer trust proposition; would block procurement approval in security-conscious enterprises |

### Option C: OAuth2 Proxy with Delegated Access

Preflight acts as an OAuth2 client; customers grant a limited-scope OAuth2 token scoped to read-only operations.

| Criterion | Assessment |
|-----------|-----------|
| Standard protocol | OAuth2 is enterprise-standard |
| Coverage | Only applicable to OAuth2-capable systems; SAP RFC, JDBC do not support OAuth2 |
| Token management | OAuth2 refresh tokens must still be stored securely |
| Verdict | **Partially adopted** — used as the authentication mechanism for OAuth2-capable systems (Salesforce, cloud warehouses); not applicable universally |

### Option D: Static Credential Configuration Files

Credentials stored in `config.yml` on the Preflight deployment host.

| Criterion | Assessment |
|-----------|-----------|
| Simplicity | High for development; trivial to configure |
| Security | Credentials in plaintext on disk; violates basic security hygiene |
| Audit | No audit trail for when credentials are accessed |
| Verdict | **Rejected** for all non-development deployments — acceptable only for local development with non-production system credentials |

---

## Implementation Notes

- `ConnectionCredentials` uses Pydantic `SecretStr`; `repr()` returns `'**********'`; field never serialised to JSON.
- `cryptography` library (AES-256-GCM) used for credential bundle encryption in SaaS mode.
- `python-jose` manages JWT API tokens with 8-hour expiry.
- Audit log retention: 90 days by default; configurable to match customer compliance requirements.
- Zero-write assertion uses `re.match()` against the first token of every query; configurable blocklist in `config.yml` under `security.blocked_sql_keywords`.

---

## Links

- [ADR-003: Read-Only Connector Architecture](./ADR-003-read-only-connector-architecture.md)
- [ADR-010: Deployment and Containerization](./ADR-010-deployment-and-containerization.md)
- [DDD: Connectivity Context](../ddd/bounded-contexts.md)
- [Security Documentation](../security/)
