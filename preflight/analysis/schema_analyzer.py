"""
Schema Consistency Analyzer

Detects mismatches in how business entities are modeled across enterprise systems.
Uses fuzzy string matching + graph analysis to find entity relationships.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

@dataclass
class FieldComparison:
    source_field: str
    target_field: str
    source_type: str
    target_type: str
    similarity_score: float
    issues: List[str] = field(default_factory=list)

@dataclass
class EntityComparisonResult:
    entity_name: str
    source_system: str
    target_system: str
    field_comparisons: List[FieldComparison] = field(default_factory=list)
    missing_in_source: List[str] = field(default_factory=list)
    missing_in_target: List[str] = field(default_factory=list)
    type_mismatches: List[Tuple[str, str, str]] = field(default_factory=list)  # (field, src_type, tgt_type)
    overall_similarity: float = 0.0

class SchemaAnalyzer:
    """Analyzes schema consistency across multiple enterprise systems.

    Uses fuzzy string matching with normalization and caching for performance.
    Optimized for large enterprise schemas with many entities and fields.
    """

    # Type base extraction: strip length specifiers like varchar(255) → varchar
    _TYPE_BASE_RE = re.compile(r'^([a-z]+)', re.I)

    def __init__(self, similarity_threshold: float = 0.8):
        self.similarity_threshold = similarity_threshold
        # Normalization cache to avoid repeated regex work
        self._norm_cache: Dict[str, str] = {}
        # Similarity cache: (n1, n2) → score
        self._sim_cache: Dict[Tuple[str, str], float] = {}
        # Type compatibility matrix
        self._type_compat = {
            ('varchar', 'text'): True,
            ('varchar', 'char'): True,
            ('int', 'integer'): True,
            ('int', 'bigint'): True,
            ('int', 'smallint'): True,
            ('integer', 'bigint'): True,
            ('decimal', 'numeric'): True,
            ('decimal', 'float'): True,
            ('float', 'double'): True,
            ('timestamp', 'datetime'): True,
            ('timestamp', 'date'): True,
            ('boolean', 'bit'): True,
            ('boolean', 'tinyint'): True,
            ('nvarchar', 'varchar'): True,
            ('nvarchar', 'text'): True,
            ('nchar', 'char'): True,
        }

    def normalize_field_name(self, name: str) -> str:
        """Normalize field name for comparison (snake_case, lowercase). Cached."""
        if name in self._norm_cache:
            return self._norm_cache[name]
        normalized = re.sub(r'([A-Z])', r'_\1', name).lower()
        normalized = re.sub(r'[^a-z0-9]', '_', normalized)
        normalized = re.sub(r'_+', '_', normalized).strip('_')
        self._norm_cache[name] = normalized
        return normalized

    def _extract_type_base(self, type_str: str) -> str:
        """Extract base type, stripping precision specifiers (varchar(255) → varchar)."""
        m = self._TYPE_BASE_RE.match(type_str.strip())
        return m.group(1).lower() if m else type_str.lower().strip()

    def field_similarity(self, field1: str, field2: str) -> float:
        """Calculate similarity between two field names. Cached for performance."""
        n1 = self.normalize_field_name(field1)
        n2 = self.normalize_field_name(field2)
        if n1 == n2:
            return 1.0
        # Cache key (always smaller first for symmetry)
        key = (n1, n2) if n1 <= n2 else (n2, n1)
        if key in self._sim_cache:
            return self._sim_cache[key]
        # Fast length-ratio pre-filter: if lengths differ by more than 50%, score < 0.67
        len1, len2 = len(n1), len(n2)
        if len1 == 0 or len2 == 0:
            score = 0.0
        elif max(len1, len2) > min(len1, len2) * 3:
            score = 0.0  # Too different in length, skip expensive computation
        else:
            score = SequenceMatcher(None, n1, n2, autojunk=False).ratio()
        self._sim_cache[key] = score
        return score

    def types_compatible(self, type1: str, type2: str) -> bool:
        """Check if two data types are compatible."""
        t1 = self._extract_type_base(type1)
        t2 = self._extract_type_base(type2)
        if t1 == t2:
            return True
        return self._type_compat.get((t1, t2), self._type_compat.get((t2, t1), False))

    def _build_normalized_field_index(self, fields: Dict[str, Dict]) -> Dict[str, str]:
        """Pre-compute normalized names for all fields in one pass."""
        return {name: self.normalize_field_name(name) for name in fields}

    def compare_entity_across_systems(
        self,
        entity_name: str,
        system_schemas: Dict[str, List[Dict]]  # {system_name: [{name, type, nullable}]}
    ) -> List[EntityComparisonResult]:
        """Compare how an entity is defined across multiple systems.

        Optimized with:
        - Pre-cached normalized names per system
        - Similarity cache across all comparisons
        - Length-ratio pre-filter to skip obviously dissimilar fields
        """
        results = []
        systems = list(system_schemas.keys())

        # Pre-build field dicts and normalized indices once per system
        field_dicts: Dict[str, Dict[str, Dict]] = {}
        norm_indices: Dict[str, Dict[str, str]] = {}
        for sys_name in systems:
            field_dicts[sys_name] = {f['name']: f for f in system_schemas[sys_name]}
            norm_indices[sys_name] = self._build_normalized_field_index(field_dicts[sys_name])

        for i in range(len(systems)):
            for j in range(i + 1, len(systems)):
                src_sys = systems[i]
                tgt_sys = systems[j]
                src_fields = field_dicts[src_sys]
                tgt_fields = field_dicts[tgt_sys]
                src_norms = norm_indices[src_sys]
                tgt_norms = norm_indices[tgt_sys]

                # Pre-sort target normalized names for faster scanning
                tgt_items = list(tgt_fields.items())  # (name, def)

                result = EntityComparisonResult(
                    entity_name=entity_name,
                    source_system=src_sys,
                    target_system=tgt_sys,
                )

                # Find field matches using fuzzy similarity
                matched_src: set = set()
                matched_tgt: set = set()

                for src_name, src_def in src_fields.items():
                    best_match = None
                    best_score = 0.0
                    src_norm = src_norms[src_name]
                    src_len = len(src_norm)

                    for tgt_name, tgt_def in tgt_items:
                        tgt_norm = tgt_norms[tgt_name]

                        # Fast exact match
                        if src_norm == tgt_norm:
                            best_score = 1.0
                            best_match = (tgt_name, tgt_def)
                            break  # Can't do better than 1.0

                        # Length pre-filter: if too different, skip SequenceMatcher
                        tgt_len = len(tgt_norm)
                        if src_len == 0 or tgt_len == 0:
                            continue
                        if max(src_len, tgt_len) > min(src_len, tgt_len) * 3:
                            continue

                        # Cache key (symmetric)
                        key = (src_norm, tgt_norm) if src_norm <= tgt_norm else (tgt_norm, src_norm)
                        score = self._sim_cache.get(key)
                        if score is None:
                            score = SequenceMatcher(None, src_norm, tgt_norm, autojunk=False).ratio()
                            self._sim_cache[key] = score

                        if score > best_score and score >= self.similarity_threshold:
                            best_score = score
                            best_match = (tgt_name, tgt_def)

                    if best_match:
                        tgt_name, tgt_def = best_match
                        issues = []

                        if not self.types_compatible(src_def.get('type', ''), tgt_def.get('type', '')):
                            issues.append(f"Type mismatch: {src_def.get('type')} vs {tgt_def.get('type')}")
                            result.type_mismatches.append((src_name, src_def.get('type', ''), tgt_def.get('type', '')))

                        result.field_comparisons.append(FieldComparison(
                            source_field=src_name,
                            target_field=tgt_name,
                            source_type=src_def.get('type', ''),
                            target_type=tgt_def.get('type', ''),
                            similarity_score=best_score,
                            issues=issues,
                        ))
                        matched_src.add(src_name)
                        matched_tgt.add(tgt_name)

                result.missing_in_target = [f for f in src_fields if f not in matched_src]
                result.missing_in_source = [f for f in tgt_fields if f not in matched_tgt]

                # Calculate overall similarity
                total_fields = max(len(src_fields), len(tgt_fields))
                matched = len(result.field_comparisons)
                type_mismatches = len(result.type_mismatches)
                result.overall_similarity = max(0.0, (matched - type_mismatches * 0.5) / max(total_fields, 1))

                results.append(result)

        return results

    def analyze_all(
        self,
        system_schemas: Dict[str, Dict[str, List[Dict]]]  # {system: {entity: [fields]}}
    ) -> Dict:
        """Run full schema analysis across all systems."""
        # Find common entities across systems
        all_entities = set()
        for system_entities in system_schemas.values():
            all_entities.update(system_entities.keys())

        results = {}
        for entity in all_entities:
            entity_schemas = {}
            for system, entities in system_schemas.items():
                if entity in entities:
                    entity_schemas[system] = entities[entity]

            if len(entity_schemas) > 1:
                results[entity] = self.compare_entity_across_systems(entity, entity_schemas)

        return results

    def generate_inconsistency_report(self, analysis_results: Dict) -> List[Dict]:
        """Generate a list of inconsistencies sorted by severity."""
        inconsistencies = []

        for entity_name, comparisons in analysis_results.items():
            for comp in comparisons:
                # Missing fields
                for field in comp.missing_in_target:
                    inconsistencies.append({
                        'entity': entity_name,
                        'type': 'missing_field',
                        'severity': 'HIGH',
                        'source': comp.source_system,
                        'target': comp.target_system,
                        'detail': f"Field '{field}' present in {comp.source_system} but missing in {comp.target_system}",
                    })

                # Type mismatches
                for field, src_type, tgt_type in comp.type_mismatches:
                    inconsistencies.append({
                        'entity': entity_name,
                        'type': 'type_mismatch',
                        'severity': 'MEDIUM',
                        'source': comp.source_system,
                        'target': comp.target_system,
                        'detail': f"Field '{field}': {src_type} in {comp.source_system} vs {tgt_type} in {comp.target_system}",
                    })

                # Key mismatches (special case for id fields)
                id_fields_src = [f.source_field for f in comp.field_comparisons if 'id' in f.source_field.lower()]
                id_fields_tgt = [f.target_field for f in comp.field_comparisons if 'id' in f.target_field.lower()]
                if id_fields_src and id_fields_tgt:
                    if set(id_fields_src) != set(id_fields_tgt):
                        inconsistencies.append({
                            'entity': entity_name,
                            'type': 'key_mismatch',
                            'severity': 'CRITICAL',
                            'source': comp.source_system,
                            'target': comp.target_system,
                            'detail': f"Key fields differ: {id_fields_src} vs {id_fields_tgt}",
                        })

        # Sort by severity
        severity_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
        inconsistencies.sort(key=lambda x: severity_order.get(x['severity'], 99))
        return inconsistencies
