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
from typing import Dict, List, Optional, Set

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
    confidence: float = 1.0

    def as_match_info(self) -> Dict:
        return {
            "canonical_name": self.canonical_name,
            "matched_tables": dict(self.tables),
            "method": self.method,
            "confidence": round(self.confidence, 3),
        }


def normalize_table_name(name: str) -> str:
    """Normalize a table name for comparison (strip prefixes, plural, case)."""
    n = name.strip().lower()
    n = re.sub(r"[^a-z0-9]+", "_", n).strip("_")
    n = _TABLE_SALT.sub("", n)
    if len(n) > 4 and n.endswith("s") and not n.endswith(("ss", "us", "is")):
        n = n[:-1]
    return n


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
    ):
        self.field_similarity_threshold = field_similarity_threshold
        self.group_field_threshold = group_field_threshold
        self.pair_field_threshold = pair_field_threshold

    # ------------------------------------------------------------------
    # Field-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_field(name: str) -> str:
        n = re.sub(r"([A-Z])", r"_\1", name).lower()
        n = re.sub(r"[^a-z0-9]", "_", n)
        return re.sub(r"_+", "_", n).strip("_")

    def _fields_similar(self, a: str, b: str) -> bool:
        na, nb = self._norm_field(a), self._norm_field(b)
        if not na or not nb:
            return False
        if na == nb:
            return True
        if max(len(na), len(nb)) > min(len(na), len(nb)) * 3:
            return False
        return (
            SequenceMatcher(None, na, nb, autojunk=False).ratio()
            >= self.field_similarity_threshold
        )

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
        all_names: Dict[str, List[str]] = {}
        for system, tables in norm_tables.items():
            for table in tables:
                all_names.setdefault(normalize_table_name(table), []).append(system)
        for norm_name, systems in all_names.items():
            distinct = sorted(set(systems))
            if len(distinct) < 2:
                continue
            tables: Dict[str, str] = {}
            for system in distinct:
                for table in norm_tables[system]:
                    if normalize_table_name(table) != norm_name:
                        continue
                    key = (system, norm_name)
                    if key in assigned:
                        continue
                    assigned.add(key)
                    tables[system] = table  # one table per system, first wins
                    break
            if len(tables) >= 2:
                groups.append(
                    EntityGroup(
                        canonical_name=norm_name,
                        tables=tables,
                        method="exact",
                        confidence=1.0,
                    )
                )

        # 2. Declared entities from the scenario config.
        for entity in declared_entities or []:
            name = entity.get("name") or ""
            declared_fields = entity.get("fields") or []
            if not name or not declared_fields:
                continue
            chosen: Dict[str, str] = {}
            scores: List[float] = []
            for system, tables in norm_tables.items():
                best_table, best_score = None, 0.0
                for table, fields in tables.items():
                    if (system, normalize_table_name(table)) in assigned:
                        continue
                    score = self.field_overlap_score(declared_fields, fields)
                    if score > best_score:
                        best_table, best_score = table, score
                if best_table is not None and best_score >= self.group_field_threshold:
                    chosen[system] = best_table
                    scores.append(best_score)
            if len(chosen) >= 2:
                for system, table in chosen.items():
                    assigned.add((system, normalize_table_name(table)))
                groups.append(
                    EntityGroup(
                        canonical_name=name,
                        tables=chosen,
                        method="declared",
                        confidence=sum(scores) / len(scores),
                    )
                )

        # 3. Seed vocabulary groups.
        for canonical, synonyms in SEED_VOCABULARY.items():
            chosen: Dict[str, str] = {}
            for system, tables in norm_tables.items():
                for table in tables:
                    key = (system, normalize_table_name(table))
                    if key in assigned:
                        continue
                    if normalize_table_name(table) in synonyms:
                        chosen[system] = table
                        break
            if len(chosen) >= 2:
                for system, table in chosen.items():
                    assigned.add((system, normalize_table_name(table)))
                groups.append(
                    EntityGroup(
                        canonical_name=canonical,
                        tables=chosen,
                        method="vocabulary",
                        confidence=0.9,
                    )
                )

        # 4. Field-overlap pairing for everything still unassigned.
        candidates = [
            (system, table, fields)
            for system, tables in norm_tables.items()
            for table, fields in tables.items()
            if (system, normalize_table_name(table)) not in assigned
        ]
        used: Set = set()
        for i, (sys_a, tab_a, fields_a) in enumerate(candidates):
            if (sys_a, normalize_table_name(tab_a)) in used:
                continue
            group_tables = {sys_a: tab_a}
            scores = []
            for sys_b, tab_b, fields_b in candidates[i + 1 :]:
                key_b = (sys_b, normalize_table_name(tab_b))
                if sys_b == sys_a or key_b in used:
                    continue
                score = self.table_pair_score(fields_a, fields_b)
                if score >= self.pair_field_threshold:
                    group_tables[sys_b] = tab_b
                    scores.append(score)
                    used.add(key_b)
            if len(group_tables) >= 2:
                used.add((sys_a, normalize_table_name(tab_a)))
                groups.append(
                    EntityGroup(
                        canonical_name=normalize_table_name(tab_a),
                        tables=group_tables,
                        method="field_overlap",
                        confidence=(sum(scores) / len(scores)) if scores else 0.5,
                    )
                )

        logger.info(
            "Entity matching: %d group(s) across %d system(s)",
            len(groups),
            len(system_schemas),
        )
        return groups
