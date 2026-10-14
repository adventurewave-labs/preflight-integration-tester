"""Cross-system entity matching.

Real ERP/CRM/warehouse systems almost never name the same business entity the
same way (Salesforce ``Account`` vs SAP ``KUNNR`` vs a warehouse ``customer``
table), so same-name comparison alone finds nothing.  This module groups
tables from different systems that plausibly represent the same business
entity, using four signals in order of trust:

1. **exact**       — identical (normalized) table names in >= 2 systems
2. **declared**    — the scenario config declares the entity (name + expected
                     fields); tables are scored by fuzzy field overlap with
                     the declared field list
3. **vocabulary**  — a small seed vocabulary of well-known ERP/CRM table names
                     (SAP customer master = KUNNR, Salesforce account, …)
4. **field_overlap** — remaining tables are paired by fuzzy field-name overlap

Groups carry a method and a confidence so downstream reports can say *how* two
tables were linked.  Everything is heuristic; the matcher proposes, the
analyzer reports, and the report marks inferred matches as such.
"""
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Well-known table names per business entity.  Intentionally small: this is a
# seed, not a dictionary — extend via the vocabulary match of your own config
# or add entries here.  SAP names are included because SAP never uses English
# nouns for its master tables (KUNNR = customer master, MARA = material
# master, VBELN = sales document) and fuzzy name matching cannot recover that.
SEED_VOCABULARY: Dict[str, Set[str]] = {
    "customer": {
        "customer", "customers", "account", "accounts", "client", "clients",
        "kunnr", "debtor", "cust_mstr", "customer_master", "customer_master",
    },
    "contact": {"contact", "contacts", "person", "people", "cpd"},
    "order": {
        "order", "orders", "sales_order", "sales_orders", "vbeln",
        "so_header", "sales_doc", "sales_document",
    },
    "product": {
        "product", "products", "item", "items", "material", "materials",
        "mara", "sku", "article", "articles",
    },
    "employee": {"employee", "employees", "staff", "worker", "workers", "pernr"},
}

_TABLE_SALT = re.compile(r"^(tbl|dim|fact|stg|raw|v|vw)_")


@dataclass
class EntityGroup:
    """Tables from >= 2 systems believed to represent one business entity."""

    canonical_name: str
    tables: Dict[str, str] = field(default_factory=dict)  # system -> table name
    method: str = "exact"  # exact | declared | vocabulary | field_overlap
    # How EACH system joined (weakest method wins for `method` above when
    # a group mixes links, e.g. exact core + vocabulary extension).
    link_methods: Dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    # Internal bookkeeping for folding later attachments into `confidence`.
    _score_sum: float = 0.0
    _score_count: int = 0

    _METHOD_STRENGTH = {"exact": 4, "declared": 3, "vocabulary": 2, "field_overlap": 1}

    def note_link(self, system: str, method: str) -> None:
        """Record how a system joined; the overall method is the weakest link."""
        self.link_methods[system] = method
        self.method = min(
            (m for m in self.link_methods.values()),
            key=lambda m: self._METHOD_STRENGTH.get(m, 0),
        )

    def as_match_info(self) -> Dict:
        return {
            "canonical_name": self.canonical_name,
            "matched_tables": dict(self.tables),
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "link_methods": dict(self.link_methods) or None,
        }


def normalize_table_name(name: str) -> str:
    """Normalize a table name for comparison (strip prefixes, plural, case)."""
    n = name.strip().lower()
    n = re.sub(r"[^a-z0-9]+", "_", n).strip("_")
    n = _TABLE_SALT.sub("", n)
    if len(n) > 4 and n.endswith("s") and not n.endswith(("ss", "us", "is")):
        n = n[:-1]
    return n


_FIELD_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_FIELD_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")


def normalize_field_name(name: str) -> str:
    """Single source of truth for field-name comparison.

    Handles camelCase (AnnualRevenue → annual_revenue) and ERP all-caps
    conventions (NAME1 → name1, KUNNR → kunnr). Both EntityMatcher and
    SchemaAnalyzer normalize through this function — divergent copies have
    caused exactly the SAP-name mismatches this exists to prevent.
    """
    normalized = _FIELD_ACRONYM_RE.sub(r"\1_\2", name)  # XMLHttp → XML_Http
    normalized = _FIELD_CAMEL_RE.sub(r"\1_\2", normalized)  # camel boundary
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_")


class EntityMatcher:
    """Groups same-entity tables across systems.

    Args:
        field_similarity_threshold: minimum fuzzy score for two field names
            to count as "the same field" when computing overlap.
        group_field_threshold: minimum field-overlap score for a table to be
            linked to a declared entity.
        pair_field_threshold: minimum field-overlap score for two tables from
            different systems to be paired by the field_overlap pass.
    """

    def __init__(
        self,
        field_similarity_threshold: float = 0.8,
        group_field_threshold: float = 0.3,
        pair_field_threshold: float = 0.35,
        max_field_overlap_candidates: int = 400,
    ):
        self.field_similarity_threshold = field_similarity_threshold
        self.group_field_threshold = group_field_threshold
        self.pair_field_threshold = pair_field_threshold
        # Step 4 (field-overlap pairing) is quadratic in table count; beyond
        # this many candidate tables it is skipped (exact/declared/vocabulary
        # passes still run) and a note is logged — better a partial match set
        # than a frozen diagnostic.
        self.max_field_overlap_candidates = max_field_overlap_candidates
        # Per-instance caches: the matcher is short-lived and normalization
        # plus pairwise similarity are pure functions of the names, so a
        # large real estate pays the SequenceMatcher cost once per pair.
        self._norm_cache: Dict[str, str] = {}
        self._sim_cache: Dict[Tuple[str, str], Optional[float]] = {}

    # ------------------------------------------------------------------
    # Field-level helpers
    # ------------------------------------------------------------------

    def _norm_field(self, name: str) -> str:
        cached = self._norm_cache.get(name)
        if cached is None:
            cached = normalize_field_name(name)
            self._norm_cache[name] = cached
        return cached

    def _pair_similarity(self, na: str, nb: str) -> Optional[float]:
        """Similarity of two *normalized* names, cached; None = prefiltered."""
        key = (na, nb) if na <= nb else (nb, na)
        cached = self._sim_cache.get(key)
        if cached is not None:
            return cached
        if max(len(na), len(nb)) > min(len(na), len(nb)) * 3:
            score = 0.0
        else:
            score = SequenceMatcher(None, na, nb, autojunk=False).ratio()
        self._sim_cache[key] = score
        return score

    def _fields_similar(self, a: str, b: str) -> bool:
        na, nb = self._norm_field(a), self._norm_field(b)
        if not na or not nb:
            return False
        if na == nb:
            return True
        score = self._pair_similarity(na, nb)
        return score is not None and score >= self.field_similarity_threshold

    def field_overlap_score(self, declared: List[str], table_fields: List[str]) -> float:
        """Fraction of *declared* fields present (fuzzily) in the table."""
        if not declared:
            return 0.0
        matched = sum(
            1 for d in declared if any(self._fields_similar(d, t) for t in table_fields)
        )
        return matched / len(declared)

    def table_pair_score(self, fields_a: List[str], fields_b: List[str]) -> float:
        """Fuzzy Jaccard-style overlap between two field lists."""
        if not fields_a or not fields_b:
            return 0.0
        matched_a = sum(1 for a in fields_a if any(self._fields_similar(a, b) for b in fields_b))
        matched_b = sum(1 for b in fields_b if any(self._fields_similar(b, a) for a in fields_a))
        return (matched_a + matched_b) / (len(fields_a) + len(fields_b))

    # ------------------------------------------------------------------
    # Grouping
    # ------------------------------------------------------------------

    def build_entity_groups(
        self,
        system_schemas: Dict[str, Dict[str, List[Dict]]],
        declared_entities: Optional[List[Dict]] = None,
    ) -> List[EntityGroup]:
        """Build entity groups from ``{system: {table: [field dicts]}}``.

        ``declared_entities`` follows the ``scenario.entities`` config shape:
        ``[{"name": "customer", "fields": ["id", "name", ...]}, ...]``.
        """
        groups: List[EntityGroup] = []
        assigned: Set = set()  # (system, normalized_table) already grouped

        norm_tables: Dict[str, Dict[str, List[str]]] = {
            system: {
                table: [f.get("name", "") for f in fields]
                for table, fields in tables.items()
            }
            for system, tables in system_schemas.items()
        }

        # 1. Exact same-name groups.
        # Assignment keys are RAW (system, table): keying by the normalized
        # name made sibling tables ('customer' and 'customers' in one
        # system) collide, silently excluding the sibling from every later
        # pass.
        all_names: Dict[str, List[str]] = {}
        for system, tables in norm_tables.items():
            for table in tables:
                all_names.setdefault(normalize_table_name(table), []).append(system)
        for norm_name, systems in all_names.items():
            distinct = sorted(set(systems))
            if len(distinct) < 2:
                continue
            tables: Dict[str, str] = {}
            claimed: Set = set()
            for system in distinct:
                for table in norm_tables[system]:
                    if normalize_table_name(table) != norm_name:
                        continue
                    if system in claimed:
                        continue
                    claimed.add(system)
                    assigned.add((system, table))
                    tables[system] = table  # one table per system, first wins
                    break

            if len(tables) >= 2:
                exact_group = EntityGroup(
                    canonical_name=norm_name,
                    tables=tables,
                    method="exact",
                    confidence=1.0,
                )
                for system in tables:
                    exact_group.note_link(system, "exact")
                groups.append(exact_group)

        # 2. Declared entities from the scenario config.
        for entity in declared_entities or []:
            if not isinstance(entity, dict):
                continue  # tolerate string entries; nothing to match on
            name = entity.get("name") or ""
            declared_fields = entity.get("fields") or []
            if not isinstance(declared_fields, (list, tuple)):
                continue
            if not name or not declared_fields:
                continue
            chosen: Dict[str, str] = {}
            scores: List[float] = []
            for system, tables in norm_tables.items():
                best_table, best_score = None, 0.0
                for table, fields in tables.items():
                    if (system, table) in assigned:
                        continue
                    score = self.field_overlap_score(declared_fields, fields)
                    if score > best_score:
                        best_table, best_score = table, score
                if best_table is not None and best_score >= self.group_field_threshold:
                    chosen[system] = best_table
                    scores.append(best_score)
            if len(chosen) >= 2:
                for system, table in chosen.items():
                    assigned.add((system, table))
                groups.append(
                    EntityGroup(
                        canonical_name=name,
                        tables=chosen,
                        method="declared",
                        confidence=sum(scores) / len(scores),
                    )
                )

        # 3. Seed vocabulary groups. When a group with this canonical name
        # already exists (e.g. an exact group over two systems), additional
        # vocabulary-identified tables EXTEND it with per-link provenance
        # instead of being silently excluded from analysis.
        for canonical, synonyms in SEED_VOCABULARY.items():
            chosen: Dict[str, str] = {}
            for system, tables in norm_tables.items():
                for table in tables:
                    if (system, table) in assigned:
                        continue
                    if normalize_table_name(table) in synonyms:
                        chosen[system] = table
                        break
            existing = next(
                (g for g in groups if g.canonical_name == canonical), None
            )
            if existing is not None:
                for system, table in chosen.items():
                    if system in existing.tables:
                        continue
                    existing.tables[system] = table
                    existing.note_link(system, "vocabulary")
                    existing.confidence = min(existing.confidence, 0.9)
                    assigned.add((system, table))
            elif len(chosen) >= 2:
                vocab_group = EntityGroup(
                    canonical_name=canonical,
                    tables=chosen,
                    method="vocabulary",
                    confidence=0.9,
                )
                for system in chosen:
                    vocab_group.note_link(system, "vocabulary")
                for system, table in chosen.items():
                    assigned.add((system, table))
                groups.append(vocab_group)

        # 4. Field-overlap pairing for everything still unassigned.
        # Prefilter: only tables that share at least one EXACT normalized
        # field name are compared with the (quadratic) fuzzy scoring —
        # same-estate variants (staging mirrors, *_v2, per-region copies)
        # always share column names, while genuinely different entities do
        # not. Cross-name entities are the vocabulary/declared passes' job.
        unassigned = [
            (system, table, fields)
            for system, tables in norm_tables.items()
            for table, fields in tables.items()
            if (system, table) not in assigned
        ]
        # Reset per call: callers (the runner) read this to warn that the
        # resulting match set — and therefore the findings — are partial.
        self.matching_was_capped = False
        if len(unassigned) > self.max_field_overlap_candidates:
            logger.info(
                "Entity matching: %d unassigned tables exceed the "
                "field-overlap budget (%d); skipping the quadratic pass",
                len(unassigned),
                self.max_field_overlap_candidates,
            )
            self.matching_was_capped = True
            unassigned = []
        norm_field_sets = {
            (system, table): {self._norm_field(f) for f in fields}
            for system, table, fields in unassigned
        }

        def _has_cross_system_overlap(system: str, table: str) -> bool:
            own = norm_field_sets[(system, table)]
            return any(
                o_sys != system and own & o_set
                for (o_sys, _o_table), o_set in norm_field_sets.items()
            )

        candidates = [c for c in unassigned if _has_cross_system_overlap(c[0], c[1])]
        used: Set = set()
        for i, (sys_a, tab_a, fields_a) in enumerate(candidates):
            if (sys_a, tab_a) in used:
                continue
            set_a = norm_field_sets[(sys_a, tab_a)]
            group_tables = {sys_a: tab_a}
            scores = []
            for sys_b, tab_b, fields_b in candidates[i + 1 :]:
                key_b = (sys_b, tab_b)
                if sys_b == sys_a or sys_b in group_tables or key_b in used:
                    # one table per system, first wins — do NOT mark the
                    # skipped table as used, the second sweep may still
                    # attach it elsewhere
                    continue
                if not (set_a & norm_field_sets[key_b]):
                    continue  # prefilter: no exact shared field name
                score = self.table_pair_score(fields_a, fields_b)
                if score >= self.pair_field_threshold:
                    group_tables[sys_b] = tab_b
                    scores.append(score)
                    used.add(key_b)
            if len(group_tables) >= 2:
                used.add((sys_a, tab_a))
                groups.append(
                    EntityGroup(
                        canonical_name=normalize_table_name(tab_a),
                        tables=group_tables,
                        method="field_overlap",
                        confidence=(sum(scores) / len(scores)) if scores else 0.5,
                        _score_sum=sum(scores),
                        _score_count=len(scores),
                    )
                )

        # 4b. Second sweep: the greedy anchor pass can strand a table that
        # matches a *member* of an existing group but not that group's anchor
        # (the anchor may already have been consumed as a member elsewhere).
        # Only field_overlap groups are extended — attaching a table via a
        # 0.35-overlap score to a vocabulary/declared/exact group would
        # mislabel the provenance of every finding from that table (ADR-011:
        # method + confidence must state how the link was actually made).
        # Bounded like step 4: only the (post-cap, prefilter-surviving)
        # `unassigned` candidates are considered, and a member is only
        # fuzzy-scored when it shares >= 1 exact normalized field with the
        # candidate.
        all_norm_sets = {
            (system, table): {self._norm_field(f) for f in fields}
            for system, tables in norm_tables.items()
            for table, fields in tables.items()
        }
        for system, table, fields in unassigned:
            key = (system, table)
            if key in assigned or key in used:
                continue
            norm_set = all_norm_sets[key]
            best_group, best_score = None, 0.0
            for group in groups:
                if group.method != "field_overlap":
                    continue
                if system in group.tables:
                    continue
                member_scores = [
                    self.table_pair_score(fields, norm_tables[ms][mt])
                    for ms, mt in group.tables.items()
                    if norm_set & all_norm_sets[(ms, mt)]
                ]
                score = max(member_scores) if member_scores else 0.0
                if score > best_score:
                    best_group, best_score = group, score
            if best_group is not None and best_score >= self.pair_field_threshold:
                best_group.tables[system] = table
                best_group._score_sum += best_score
                best_group._score_count += 1
                best_group.confidence = (
                    best_group._score_sum / best_group._score_count
                )
                used.add(key)

        groups = [g for g in groups if len(g.tables) >= 2]
        logger.info(
            "Entity matching: %d group(s) across %d system(s)",
            len(groups),
            len(system_schemas),
        )
        return groups
