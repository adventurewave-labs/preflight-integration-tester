# ADR-009: Reporting Engine Design

**Status**: Accepted  
**Date**: 2026-05-27  
**Deciders**: Engineering Lead, Product Lead, Design Lead  
**Technical Story**: [PRD-002 §5.6](../../plans/PRD-002-preflight-integration-tester.md)

---

## Context and Problem Statement

Preflight's output — the readiness report — is the primary deliverable that justifies the product's cost and drives the customer's purchase decision. The report must serve two fundamentally different audiences with incompatible needs:

**Audience 1: Executive Decision-Makers (CIO, VP of Platform, CFO)**
- Non-technical; does not want SQL schemas or latency percentiles.
- Needs a single clear verdict, a headline cost/risk figure, and a recommended action.
- Will print this and share it in a board or budget meeting.
- Values visual clarity and professional presentation.

**Audience 2: Enterprise Architects and Integration Engineers**
- Technical; needs exact schema inconsistencies, field-level diff details, pipeline latency numbers.
- Will build the remediation backlog from the detailed findings.
- May want to import findings into Jira, ServiceNow, or a custom tool.
- Values completeness and programmatic accessibility.

**Audience 3: Preflight Sales and Customer Success**
- Needs to share the report link externally without requiring a Preflight account.
- Wants the option to white-label the report for a consulting engagement.

A single reporting format cannot serve all three audiences. The reporting engine must produce at least two output formats from a single analysis result.

Additional requirements:
- Reports must be archivable: a report generated today must be reproducible and readable in 5 years without a running Preflight instance.
- Reports must be portable: customers should be able to share them outside their Preflight deployment.
- Report templates must be customisable: white-label deployments need branded output.

---

## Decision Drivers

- Dual-audience content: executive narrative + technical detail from same data
- Portability: reports must be self-contained and readable without a running service
- Printability: PDF output required for board-level presentations
- Machine readability: JSON output for programmatic consumption and tool integration
- Customisability: template system for white-labelling and customer branding
- Archivability: reports readable years after generation without service dependency
- Performance: report generation must complete within 2 minutes for any run size

---

## Considered Options

### Option A: Jinja2 HTML/PDF + JSON API output (chosen)
### Option B: Jupyter Notebooks
### Option C: Word documents (python-docx)
### Option D: Third-party BI tools (Metabase, Grafana)

---

## Decision Outcome

**Chosen option: Jinja2-templated HTML/PDF reports + structured JSON API output**.

The reporting engine produces three artefacts from a single `ReadinessReport` domain object:

1. **HTML report** — self-contained, single-file; all CSS and D3.js charts inlined; renders without network access.
2. **PDF report** — generated from the HTML report via headless Chromium (`playwright`); pixel-perfect print output.
3. **JSON report** — full machine-readable representation of all findings; consumed by API clients, CLI, and dashboard.

### Report Architecture

```
ReadinessReport (domain object)
        │
        ▼
ReportRenderer
  ├── HtmlRenderer
  │     ├── Jinja2Environment (templates/)
  │     ├── D3.js charts (inlined as base64 SVG)
  │     └── Produces: report-{run_id}.html (self-contained)
  │
  ├── PdfRenderer
  │     ├── playwright.chromium.launch()
  │     ├── Renders HTML to PDF
  │     └── Produces: report-{run_id}.pdf
  │
  └── JsonRenderer
        ├── Pydantic model .model_dump(mode="json")
        └── Produces: /api/v1/reports/{run_id} (API endpoint)
                      report-{run_id}.json (file download)
```

### Template Structure

```
preflight/reporting/templates/
├── base.html.j2              # Base layout with branding variables
├── executive_summary.html.j2 # Verdict, headline score, top-3 issues
├── schema_analysis.html.j2   # Entity mapping tables, inconsistency list
├── pipeline_results.html.j2  # Latency charts, breaking-point analysis
├── middleware_gaps.html.j2    # Gap descriptions, effort estimates
├── remediation_plan.html.j2  # Prioritised backlog table
└── partials/
    ├── verdict_badge.html.j2  # GO / NOT_YET / NOT_READY badge
    ├── score_gauge.html.j2    # D3.js radial gauge (inlined SVG)
    └── severity_table.html.j2 # Reusable severity-sorted findings table
```

### Key Template Variables

```python
class ReportContext(BaseModel):
    """Template context passed to Jinja2."""
    run_id: str
    generated_at: datetime
    customer_name: str
    use_case_description: str
    
    # Executive summary
    readiness_score: int          # 0–100
    verdict: ReadinessVerdict     # GO / NOT_YET / NOT_READY
    verdict_rationale: str        # Plain-English explanation
    estimated_remediation_weeks: tuple[int, int]  # range
    
    # Findings
    schema_inconsistencies: list[SchemaInconsistency]
    pipeline_bottlenecks: list[PipelineBottleneck]
    middleware_gaps: list[MiddlewareGap]
    
    # Remediation plan
    remediation_items: list[RemediationItem]
    
    # Branding (customisable)
    brand_name: str = "Preflight"
    brand_logo_b64: str | None = None
    brand_primary_colour: str = "#1a56db"
    brand_accent_colour: str = "#e02424"
```

### JSON Report Schema (excerpt)

```json
{
  "schema_version": "1.0",
  "run_id": "f47ac10b-...",
  "generated_at": "2026-05-27T14:32:00Z",
  "readiness_score": 67,
  "verdict": "NOT_YET",
  "connected_systems": ["salesforce", "sap_s4hana", "snowflake"],
  "schema_inconsistencies": [
    {
      "id": "si-001",
      "entity_a": {"system": "salesforce", "name": "Account"},
      "entity_b": {"system": "sap_s4hana", "name": "KNA1"},
      "match_confidence": 0.87,
      "inconsistency_type": "key_type_mismatch",
      "severity": "HIGH",
      "impact_score": 92,
      "description": "Customer identifier is a string UUID in Salesforce but a 10-digit integer in SAP; cross-system agent lookups will fail without a translation layer.",
      "remediation_item_ids": ["ri-003"]
    }
  ],
  "remediation_plan": [...]
}
```

### Positive Consequences

- **Dual audience**: the HTML template separates executive summary (first page) from technical appendix (remainder); the PDF prints as a professional document.
- **Self-contained HTML**: all charts and styles are inlined; the report file is shareable without hosting infrastructure.
- **Archivable**: HTML/PDF/JSON files stored in PostgreSQL as blobs and as filesystem files; readable without a running Preflight instance.
- **Machine-readable JSON**: API consumers can import findings into Jira, ServiceNow, or internal tooling; exact field names are stable across versions.
- **Customisable branding**: template variables cover logo, colours, and brand name; white-label requires no code changes.
- **Version-controlled templates**: template changes are tracked in git; report appearance can be audited historically.

### Negative Consequences

- Headless Chromium for PDF generation adds ~200 MB to the Docker image and increases report generation time by 30–60 seconds vs. pure HTML.
- Self-contained HTML reports with inlined D3.js charts and all CSS can reach 5–15 MB for large schema analyses; this is acceptable for email/share but not ideal for repeated web display (mitigated by serving the JSON endpoint for the live dashboard).
- Jinja2 template maintenance requires both Python and HTML/CSS competency; the template layer is not easily edited by non-engineers (a report builder UI is a v2 feature).

---

## Alternatives Considered

### Option B: Jupyter Notebooks

Generate diagnostic results as a Jupyter notebook; render with nbconvert for HTML/PDF output.

| Criterion | Assessment |
|-----------|-----------|
| Technical audience appeal | High for data engineers who use notebooks |
| Executive suitability | Very low; notebooks have a "code" aesthetic incompatible with C-suite presentations |
| Customisability | Low; notebook appearance is standardised |
| Archivability | Moderate; notebooks require a Python runtime for interactivity |
| Verdict | **Rejected** — wrong aesthetic for the executive buyer; limited customisability |

### Option C: Word Documents (python-docx)

Generate `.docx` files via `python-docx`; customers open in Microsoft Word.

| Criterion | Assessment |
|-----------|-----------|
| Enterprise familiarity | High — Word is universally available |
| Programmatic generation | `python-docx` handles basic formatting but complex charts require embedded Excel |
| Portability | `.docx` without Word or compatible viewer is not portable |
| Machine readability | None |
| Customisability | Templates exist but are unwieldy at scale |
| Verdict | **Rejected** — no machine-readable output path; chart generation complexity high; PDF quality inferior to HTML→PDF |

### Option D: Third-Party BI Tools (Metabase, Grafana)

Push diagnostic results to Metabase or Grafana; generate shareable dashboard links.

| Criterion | Assessment |
|-----------|-----------|
| Interactivity | Excellent |
| Self-contained / archivable | No; requires running Metabase/Grafana instance |
| Portability | Dashboard links expire; not shareable without platform access |
| Control | Dependent on third-party SaaS availability and feature roadmap |
| VPC compatibility | Metabase can be self-hosted; adds significant operational complexity |
| Verdict | **Rejected** — archivability and portability requirements cannot be met; a self-hosted BI stack adds more operational burden than in-house Jinja2 templates |

---

## Implementation Notes

- Template engine: `Jinja2` 3.x with `autoescape=True` to prevent XSS in customer-provided field names.
- PDF generation: `playwright` (async) with headless Chromium; PDF paper size A4 for international compatibility.
- Chart rendering: D3.js charts generated server-side as SVG strings and embedded inline; no external CDN dependency.
- Report storage: HTML and PDF stored as files in `/data/reports/`; path recorded in `readiness_reports.report_file_path`; JSON available via API.
- White-label: `ReportContext.brand_*` fields read from `config.yml` or the `DiagnosticRun.customer_config` object.
- Report generation runs as the final phase of the Celery diagnostic task (see ADR-008).

---

## Links

- [ADR-008: Async Processing Architecture](./ADR-008-async-processing-architecture.md)
- [ADR-006: Data Storage Architecture](./ADR-006-data-storage-architecture.md)
- [DDD: Readiness Reporting Context](../ddd/bounded-contexts.md)
- [DDD: Domain Model — ReadinessReport, RemediationItem](../ddd/domain-model.md)
