# PRD 2 — Pre-Flight Integration Stress-Tester

**Working name:** Preflight
**Category:** Pre-purchase AI-readiness diagnostic / integration assurance
**Status:** Draft v1
**Owner:** [Product]
**Last updated:** May 2026

---

## 1. Summary

An automated diagnostic environment that an enterprise runs *before* committing to a large AI software license. Diagnostic agents simulate the intended deployment against the company's real systems: they stress-test existing data pipelines, check for schema inconsistencies across ERP and CRM, and produce a realistic, evidence-backed assessment of the middleware and data-hygiene work the deployment will actually require.

The product exists to kill **"pilot purgatory"** — the pattern where enterprises buy AI software, fail to get it into production, and never identify why. Preflight forces a ruthless data-readiness phase before a single production agent is deployed, and turns "are we ready?" from an opinion into a measured report.

## 2. Problem

Enterprises sign large AI/agentic software contracts on the assumption that their data and integration plumbing can support the use case. They frequently can't. The result is stalled pilots, blown budgets, and projects that quietly die after the license is already paid for.

Specific failure modes:

- **Hidden schema inconsistency.** The same entity (customer, order, product) is modeled differently across ERP, CRM, and data warehouse. Agents that span systems break on these mismatches.
- **Pipeline fragility.** Existing data pipelines were never load-tested for the query volume an autonomous agent generates.
- **Unbudgeted middleware.** The integration layer required to make the deployment work is discovered only mid-pilot, after the software is bought.
- **Optimistic vendor demos.** Vendor proofs-of-concept run on clean sample data, not the customer's actual messy production landscape, so they systematically understate the work required.
- **No objective readiness baseline.** Buyers have no way to assess readiness independent of the vendor selling them the software.

## 3. Goals and non-goals

**Goals**
- Give enterprise buyers an honest, vendor-independent readiness assessment before they sign.
- Quantify the integration and data-hygiene work a proposed AI deployment will require, in time and effort.
- Provide a prioritized remediation plan for the gaps found.
- Serve as a low-cost, low-risk first engagement that earns trust for larger follow-on work.

**Non-goals**
- Not a production integration platform; Preflight diagnoses, it does not run live workloads.
- Not a data-cleaning tool — it identifies hygiene problems and scopes them, it does not fix them in v1.
- Not tied to one AI vendor; deliberately vendor-neutral.
- Does not require write access to any production system.

## 4. Target users and buyer

- **Buyer:** CIO, VP of Data/Platform, Head of Enterprise Architecture, or the program owner accountable for an AI initiative's success.
- **Influencers:** procurement and finance, who want to de-risk a large software spend.
- **Why this sells fast:** it is *pre-purchase* — cheap relative to the license it de-risks, low-risk because it is read-only, and easy to approve because it protects a much larger budget decision. It is a natural land-and-expand wedge ahead of a bigger modernization engagement.

## 5. Requirements

### 5.1 System connectivity
- Read-only connectors to common enterprise systems: major ERP and CRM platforms, relational databases, and cloud data warehouses.
- Connect through customer-controlled credentials with least-privilege, read-only scopes; never request write access.
- Support a sandboxed/VPC deployment so the customer's data never leaves their environment.

### 5.2 Deployment simulation
- Let the user describe the intended AI deployment (use case, which systems it touches, expected request volume).
- Agents construct a simulation of that workload and run it against the connected systems in read-only mode.

### 5.3 Schema consistency analysis
- Map how key business entities are represented across every connected system.
- Detect mismatches: differing keys, field semantics, value formats, cardinality, and missing relationships.
- Rank inconsistencies by how badly they would break the proposed deployment.

### 5.4 Pipeline stress testing
- Profile existing data pipelines for latency, throughput, and failure behavior under the simulated agent load.
- Identify bottlenecks and breaking points.

### 5.5 Middleware gap analysis
- Determine what integration/middleware layer the deployment would require that does not exist today.
- Estimate the effort to build it.

### 5.6 Readiness report
- A single readiness score plus a clear go / not-yet / not-ready verdict.
- A prioritized remediation backlog: each gap with severity, estimated effort, and recommended sequence.
- An executive summary written for non-technical decision-makers, explicitly framing the hidden cost the buyer would otherwise discover mid-pilot.

### 5.7 Scenario modeling (sales-enabling)
- An interactive view that lets the buyer adjust assumptions (data volume, number of integrated systems, agent step-count) and see projected integration and consumption cost change in response — making the cost of ignoring the plumbing visible.

## 6. Success metrics

- **Speed:** time from connection to delivered readiness report (target: days, not weeks).
- **Accuracy:** % of identified gaps later confirmed as real during the actual deployment.
- **Decision impact:** % of engagements where the report changed the buyer's purchase decision or timeline.
- **Expansion:** % of Preflight engagements that convert into a follow-on remediation or modernization engagement.
- **Commercial:** number of paid diagnostics per quarter; average contract value.

## 7. Phasing

- **v0 (MVP):** Connectors for one ERP + one CRM + one warehouse; schema consistency analysis; static readiness report. Manual setup acceptable.
- **v1:** Pipeline stress testing, middleware gap estimation, interactive scenario modeling, self-serve connection.
- **v2:** Broader connector library, continuous re-assessment (run again as remediation progresses), benchmarking against anonymized peer data.

## 8. Risks and open questions

- **Connector breadth.** Enterprise landscapes are heterogeneous; the value depends on covering the customer's actual stack. Mitigation: prioritize the most common ERP/CRM/warehouse combinations first, sell only where coverage exists.
- **Estimate credibility.** Effort estimates that prove wrong destroy trust. Mitigation: present ranges, show the evidence behind each estimate, and refine estimates from closed-loop data.
- **Security posture.** Buyers will scrutinize any tool touching production data. Mitigation: read-only by design, VPC/self-hosted deployment, clear data-handling documentation.
- **Open question:** is the offering positioned as software (self-serve SaaS) or as a productized service (delivered with a consultant)? The fast-sale path may favor the latter initially.

## 9. Go-to-market notes

Position as cheap insurance on an expensive decision: "spend a fraction of the license cost to find out if the license will work." Sell to the program owner who is personally accountable for the AI initiative. Use the engagement as a trust-building wedge — Preflight finds the gaps; a follow-on engagement fixes them.