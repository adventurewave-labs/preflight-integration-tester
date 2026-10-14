# ADR-011: Cross-System Entity Matching

**Status**: Accepted
**Date**: 2026-10-14
**Context**: The "real connectors" wiring effort (CLI + API now drive the
connector registry instead of hardcoded demo data) exposed a gap in
ADR-004's schema-consistency strategy: `SchemaAnalyzer.analyze_all` only
compares entities that share the same table name across systems. Real
ERP/CRM/warehouse estates never align on names — the very problem the
product exists to detect (Salesforce `Account` vs SAP `KUNNR` vs a
warehouse `dim_customer`) produced zero findings.

## Decision

Add an `EntityMatcher` (preflight/analysis/entity_matcher.py) that groups
tables from different systems into candidate business entities using four
signals, in order of trust:

1. **exact** — identical normalized table names (confidence 1.0)
2. **declared** — the scenario config declares the entity (name + expected
   fields); tables are scored by fuzzy field overlap with the declared list
3. **vocabulary** — a small seed vocabulary of well-known ERP/CRM names
   (`KUNNR` = customer master, `MARA` = material, `VBELN` = sales document,
   `Account` = customer, …), because SAP's abbreviated German table names
   cannot be recovered by fuzzy string matching alone
4. **field_overlap** — remaining tables paired by fuzzy field-name overlap

`SchemaAnalyzer.analyze_entity_groups` renames grouped tables to the
group's canonical entity name and reuses the existing comparison logic.
Every inconsistency emitted from an inferred match carries an
`entity_match` block (`method` + `confidence` + `matched_tables`) so reports
state *how* two tables were linked, not just that they differ.

Field-name normalization now handles ERP all-caps conventions
(`NAME1` → `name1`, previously `n_a_m_e1`), which had made camelCase-vs-SAP
field comparisons impossible.

## Consequences

- The `--mock` demo keeps its frozen 97.0/GO output (same-name analysis
  only); the matcher runs on real (non-mock) diagnostics. The demo GIF and
  README numbers remain reproducible.
- Vocabulary matches are heuristics with a documented seed set; false links
  are possible and are always labeled with method + confidence for human
  review. The seed vocabulary is intentionally small and extensible.
- Declared entities (from `scenario.entities` in the config) are the
  highest-precision path for users who know their domain model.
