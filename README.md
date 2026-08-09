# Preflight Integration Tester

> Pre-purchase AI-readiness diagnostic that kills "pilot purgatory" before it starts

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Pre-Alpha](https://img.shields.io/badge/Status-Pre--Alpha-red.svg)](https://github.com/adventurewave-labs/preflight-integration-tester)

## What is Pilot Purgatory?

**Pilot purgatory** is the pattern where enterprises buy expensive AI software, fail to get it into production, and never identify why. Teams discover hidden schema inconsistencies, pipeline fragility, and missing middleware *after* signing the contract — when it's too late to back out and too expensive to fix properly.

Preflight solves this by stress-testing your actual enterprise systems *before* you commit, turning "are we ready?" from an opinion into a measured report.

## 🎯 Problem We Solve

Enterprise AI deployments fail not because the AI doesn't work, but because enterprise systems aren't ready:

- **Hidden Schema Chaos**: Customer data is modeled differently across ERP, CRM, and warehouse
- **Pipeline Fragility**: Existing data flows break under AI agent query volumes
- **Middleware Surprises**: Required integration layer discovered mid-pilot
- **Optimistic Demos**: Vendor POCs run on clean sample data, not your messy reality
- **No Objective Baseline**: Buyers have no vendor-independent readiness assessment

**Result**: Blown budgets, stalled pilots, and projects that quietly die after the license is paid.

## 🚀 How Preflight Works

### 1. **Connect** (Read-Only)
- Secure connectors to your ERP, CRM, database, and warehouse
- Customer-controlled credentials with least-privilege access
- Optional VPC/self-hosted deployment for maximum security

### 2. **Simulate**
- Describe your intended AI deployment (use case, systems, query volume)
- Diagnostic agents simulate that workload against your real systems
- Stress-test pipelines and discover breaking points

### 3. **Analyze**
- Map business entities across all connected systems
- Detect schema mismatches that would break cross-system AI agents
- Identify middleware gaps and pipeline bottlenecks

### 4. **Report**
- Single readiness score: **Go** / **Not Yet** / **Not Ready**
- Prioritized remediation backlog with effort estimates
- Executive summary for decision-makers
- Interactive cost modeling for different scenarios

## 📊 What You Get

### Readiness Assessment
```
┌─────────────────────┐
│ READINESS SCORE: 67%│
│ VERDICT: NOT YET    │
└─────────────────────┘

Critical Issues Found:
├── Customer ID mismatch (ERP vs CRM)
├── Pipeline latency spike at 50+ QPS
└── Missing order history integration

Estimated Remediation: 8-12 weeks
```

### Gap Analysis
- **Schema Inconsistencies**: Entity mapping conflicts ranked by impact
- **Pipeline Stress Points**: Throughput limits and latency spikes
- **Middleware Requirements**: Integration layer gaps with effort estimates
- **Data Quality Issues**: Hygiene problems that would break AI agents

### Executive Summary
- Cost of hidden integration work (vs. original budget)
- Timeline impact of remediation
- Risk assessment and mitigation priorities
- Vendor-independent perspective

## 🏗️ Architecture

```
┌────────────────┐     ┌────────────────┐     ┌────────────────┐
│   Enterprise    │────▶│   Diagnostic    │────▶│   Analysis      │
│   Connectors    │     │    Agents       │     │    Engine       │
│   (Read-Only)   │     │  (Simulation)   │     │  (Gap Finding)  │
└────────────────┘     └────────────────┘     └────────────────┘
                                                          │
                                                          ▼
┌────────────────┐     ┌────────────────┐     ┌────────────────┐
│   Reporting     │◀────│   Remediation   │◀────│Schema Consistency│
│    Engine       │     │    Planner      │     │    Analyzer     │
└────────────────┘     └────────────────┘     └────────────────┘
```

## 🛠️ Technology Stack

- **Core Engine**: Python (data analysis, schema mapping)
- **Connectors**: Enterprise SDKs and REST APIs
- **Frontend**: React dashboard with D3.js visualizations
- **Database**: PostgreSQL with time-series extensions
- **Deployment**: Docker/Kubernetes, VPC-ready
- **Security**: OAuth2, least-privilege access, encryption

## 💼 Target Customers

### Primary Buyer
- **CIO** / **VP of Data & Platform** / **Head of Enterprise Architecture**
- **Program Owner** accountable for AI initiative success
- **Procurement/Finance** teams de-risking large software spends

### Use Cases
- **Pre-purchase Due Diligence**: Before signing AI/ML platform contracts
- **Pilot Planning**: Scoping integration work for AI deployments
- **Vendor Evaluation**: Objective comparison of integration requirements
- **Budget Planning**: Realistic effort estimates for AI readiness work

## 🚦 Getting Started

### Prerequisites
```bash
# Python 3.9+
python --version

# Docker (for containerized deployment)
docker --version

# Enterprise system credentials (read-only)
```

### Quick Start
```bash
# Clone repository
git clone https://github.com/adventurewave-labs/preflight-integration-tester.git
cd preflight-integration-tester

# Install dependencies
pip install -r requirements.txt

# Configure enterprise connections
cp config.example.yml config.yml
# Edit config.yml with your system credentials

# Run diagnostic
python preflight.py run --config config.yml

# View report
open reports/readiness-assessment.html
```

### Enterprise Deployment
```bash
# VPC deployment
docker build -t preflight .
docker run -p 8080:8080 \
  -v /path/to/config:/app/config \
  -v /path/to/reports:/app/reports \
  preflight

# Or use Kubernetes manifests
kubectl apply -f k8s/
```

## 🔐 Security & Compliance

- **Read-Only Access**: Never requests write permissions to any system
- **Customer-Controlled**: All credentials managed by customer
- **Data Isolation**: Optional VPC deployment keeps data in your environment
- **Encryption**: All data encrypted in transit and at rest
- **Audit Logging**: Full activity log for compliance requirements

## 📈 Planned integrations

### Implemented today

The connectors that exist in this repository (see
`preflight/core/infrastructure/connectors/`):

- **Salesforce** (CRM)
- **Snowflake** (data warehouse)
- **PostgreSQL** (database)
- **Mock connector** (for demos and testing; the real connectors also fall
  back to mock mode when their client libraries are not installed)

### Planned (not yet implemented)

**ERP Platforms**
- SAP (S/4HANA, ECC)
- Oracle ERP Cloud
- Microsoft Dynamics 365
- NetSuite
- Workday

**CRM Systems**
- HubSpot
- Microsoft Dynamics CRM
- Pipedrive
- Zoho

**Data Warehouses**
- Databricks
- Amazon Redshift
- Google BigQuery
- Azure Synapse

**Databases**
- MySQL, SQL Server
- Oracle Database
- MongoDB, Cassandra
- Redis, Elasticsearch

## 📚 Documentation

- [Product Requirements Document](./plans/PRD-002-preflight-integration-tester.md)
- [Enterprise Connectors Guide](./docs/connectors/README.md) (Coming Soon)
- [Security & Compliance](./docs/security/README.md) (Coming Soon)
- [Deployment Guide](./docs/deployment/README.md) (Coming Soon)
- [API Reference](./docs/api/README.md) (Coming Soon)

## 🗺️ Roadmap

### v0 (MVP) — shipped
- [x] Project setup and architecture
- [x] CRM (Salesforce), data warehouse (Snowflake), and PostgreSQL connectors — ERP connectors are still planned
- [x] Schema consistency analysis
- [x] Static readiness report
- [x] Manual configuration

### v1 — in progress
- [x] Pipeline stress testing
- [x] Middleware gap estimation
- [ ] Interactive scenario modeling
- [ ] Self-service connection wizard
- [x] Executive reporting

### v2 - Q3 2026
- [ ] Expanded connector library (20+ systems)
- [ ] Continuous re-assessment
- [ ] Peer benchmarking (anonymized)
- [ ] Automated remediation recommendations
- [ ] API marketplace integration

### v3 - Q4 2026
- [ ] Real-time monitoring
- [ ] Predictive gap analysis
- [ ] Integration marketplace
- [ ] White-label deployment

## 🤝 Contributing

We welcome contributions from enterprise architecture and integration experts!

Priority areas:
- Additional enterprise system connectors
- Schema mapping algorithms
- Load testing methodologies
- Reporting and visualization
- Security and compliance features

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 💬 Contact & Support

- **Issues**: [GitHub Issues](https://github.com/adventurewave-labs/preflight-integration-tester/issues)
- **Discussions**: [GitHub Discussions](https://github.com/adventurewave-labs/preflight-integration-tester/discussions)

## 🙏 Acknowledgments

Built with insights from:
- Enterprise architects who've seen AI deployments succeed and fail
- Integration specialists dealing with legacy system complexity
- Procurement teams burned by optimistic vendor estimates

---

**Don't let pilot purgatory kill your AI initiative. Know before you buy.**
