"""
Schema analysis application service.

Responsible for comparing how business entities are represented across
multiple connected enterprise systems and surfacing inconsistencies.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...domain.aggregates import AnalysisResults
from ...domain.entities import (
    ConnectionProfile,
    EntityMapping,
    SchemaInconsistency,
)
from ...domain.value_objects import (
    EntityField,
    SchemaFieldMapping,
    SeverityLevel,
)

logger = logging.getLogger(__name__)


class SchemaAnalysisService:
    """Service for schema consistency analysis across enterprise systems.

    This service operates in read-only mode against already-connected systems.
    It does not open or close connections; those are managed by the infrastructure
    layer and passed in as :class:`ConnectionProfile` instances.

    Usage::

        service = SchemaAnalysisService()
        results = await service.analyze_schemas(connections, ["Customer", "Order"])
    """

    # Pairs of data types considered equivalent across different systems.
    _TYPE_EQUIVALENCES: List[frozenset] = [
        frozenset({"varchar", "text", "string", "nvarchar", "char"}),
        frozenset({"int", "integer", "bigint", "smallint", "number"}),
        frozenset({"float", "double", "decimal", "numeric", "real"}),
        frozenset({"bool", "boolean", "bit", "tinyint"}),
        frozenset({"date", "datetime", "timestamp", "datetime2"}),
    ]

    async def analyze_schemas(
        self,
        connections: List[ConnectionProfile],
        entities: List[str],
    ) -> AnalysisResults:
        """Analyse schema consistency for the given entities across all connections.

        For each named entity the service introspects each connected system,
        constructs an :class:`EntityMapping`, detects inconsistencies, and
        aggregates everything into an :class:`AnalysisResults` object.

        Args:
            connections: Active connection profiles to inspect.
            entities: Names of business entities to analyse.

        Returns:
            An :class:`AnalysisResults` instance populated with entity mappings
            and schema inconsistencies.
        """
        results = AnalysisResults()
        active = [c for c in connections if c.status == "connected"]

        logger.info(
            "Starting schema analysis for %d entities across %d systems",
            len(entities),
            len(active),
        )

        for entity_name in entities:
            # Gather per-system representations from metadata attached during
            # the connection phase (populated by the connector layer).
            system_representations: Dict[str, Dict[str, Any]] = {}
            for conn in active:
                entity_meta = conn.metadata.get("entities", {}).get(entity_name)
                if entity_meta:
                    system_representations[conn.name] = entity_meta

            if not system_representations:
                logger.debug("No schema metadata found for entity '%s'", entity_name)
                continue

            mapping = self.map_entity(entity_name, system_representations)
            mapping.inconsistencies = self.detect_inconsistencies(mapping)
            results.entity_mappings.append(mapping)
            results.schema_inconsistencies.extend(mapping.inconsistencies)

        logger.info(
            "Schema analysis complete: %d entity mappings, %d inconsistencies",
            len(results.entity_mappings),
            len(results.schema_inconsistencies),
        )
        return results

    def map_entity(
        self,
        entity_name: str,
        system_representations: Dict[str, Dict[str, Any]],
    ) -> EntityMapping:
        """Build an :class:`EntityMapping` from per-system schema metadata.

        The canonical definition is derived by taking the union of all fields
        found across systems, flagging each as nullable if it is absent in any
        one system (which itself represents an inconsistency).

        Args:
            entity_name: Name of the business entity.
            system_representations: Mapping of system name → raw schema dict.
                Each dict should contain a ``"fields"`` key with a list of field
                descriptor dicts, each having at least ``"name"`` and ``"type"``.

        Returns:
            A populated :class:`EntityMapping` (without inconsistency list —
            call :meth:`detect_inconsistencies` separately).
        """
        canonical: Dict[str, EntityField] = {}
        field_mappings: List[SchemaFieldMapping] = []

        # Build canonical field set from the union of all system fields.
        all_field_names: set = set()
        per_system_fields: Dict[str, Dict[str, Dict[str, Any]]] = {}

        for system_name, schema in system_representations.items():
            raw_fields = schema.get("fields", [])
            per_system_fields[system_name] = {}
            for f in raw_fields:
                fname = f.get("name", "")
                if fname:
                    all_field_names.add(fname)
                    per_system_fields[system_name][fname] = f

        for fname in all_field_names:
            # Determine presence across systems to decide nullability.
            present_in = [
                s for s, fields in per_system_fields.items() if fname in fields
            ]
            absent_in = [
                s for s in system_representations if s not in present_in
            ]
            nullable = len(absent_in) > 0

            # Use the data type from the first system that has this field.
            data_type = "unknown"
            is_pk = False
            for s in present_in:
                field_meta = per_system_fields[s][fname]
                data_type = field_meta.get("type", "unknown")
                is_pk = field_meta.get("primary_key", False)
                break

            canonical[fname] = EntityField(
                name=fname,
                data_type=data_type,
                nullable=nullable,
                is_primary_key=is_pk,
            )

        # Build cross-system field mappings for fields present in multiple systems.
        systems = list(system_representations.keys())
        for i, src_sys in enumerate(systems):
            for tgt_sys in systems[i + 1 :]:
                for fname in all_field_names:
                    src_has = fname in per_system_fields.get(src_sys, {})
                    tgt_has = fname in per_system_fields.get(tgt_sys, {})

                    if src_has and tgt_has:
                        src_field = per_system_fields[src_sys][fname]
                        tgt_field = per_system_fields[tgt_sys][fname]
                        score = self._type_similarity(
                            src_field.get("type", ""),
                            tgt_field.get("type", ""),
                        )
                        mapping_type = "exact" if score >= 0.9 else "semantic"
                    elif src_has and not tgt_has:
                        score = 0.0
                        mapping_type = "unmapped"
                    else:
                        continue

                    field_mappings.append(
                        SchemaFieldMapping(
                            source_system=src_sys,
                            source_field=fname,
                            target_system=tgt_sys,
                            target_field=fname if tgt_has else "",
                            similarity_score=score,
                            mapping_type=mapping_type,
                        )
                    )

        # Compute overall consistency score.
        if field_mappings:
            avg_similarity = sum(m.similarity_score for m in field_mappings) / len(
                field_mappings
            )
        else:
            avg_similarity = 1.0

        return EntityMapping(
            entity_name=entity_name,
            canonical_definition=canonical,
            system_representations=system_representations,
            field_mappings=field_mappings,
            consistency_score=avg_similarity,
        )

    def detect_inconsistencies(
        self, entity_mapping: EntityMapping
    ) -> List[SchemaInconsistency]:
        """Detect schema inconsistencies within an :class:`EntityMapping`.

        Checks performed:
        - **missing_field**: field present in one system but absent in another.
        - **type_mismatch**: same field name, incompatible data types.

        Args:
            entity_mapping: A populated :class:`EntityMapping`.

        Returns:
            A list of :class:`SchemaInconsistency` objects.
        """
        inconsistencies: List[SchemaInconsistency] = []
        systems = list(entity_mapping.system_representations.keys())
        per_system = self._extract_per_system_fields(entity_mapping)

        all_fields: set = set()
        for fields in per_system.values():
            all_fields.update(fields.keys())

        for fname in all_fields:
            present_in = [s for s in systems if fname in per_system.get(s, {})]
            absent_in = [s for s in systems if fname not in per_system.get(s, {})]

            # Missing field inconsistencies.
            for src in present_in:
                for tgt in absent_in:
                    severity = (
                        SeverityLevel.HIGH
                        if per_system[src][fname].get("primary_key")
                        else SeverityLevel.MEDIUM
                    )
                    inconsistencies.append(
                        SchemaInconsistency(
                            entity_name=entity_mapping.entity_name,
                            source_system=src,
                            target_system=tgt,
                            inconsistency_type="missing_field",
                            field_name=fname,
                            source_definition=str(per_system[src][fname]),
                            target_definition=None,
                            severity=severity,
                            impact_description=(
                                f"Field '{fname}' exists in {src} but is absent in {tgt}. "
                                "Queries spanning both systems may fail or return nulls."
                            ),
                            remediation_hint=(
                                f"Add field '{fname}' to {tgt} or implement a "
                                "transformation/default in the integration layer."
                            ),
                        )
                    )

            # Type mismatch inconsistencies.
            if len(present_in) >= 2:
                for i, src in enumerate(present_in):
                    for tgt in present_in[i + 1 :]:
                        src_type = per_system[src][fname].get("type", "")
                        tgt_type = per_system[tgt][fname].get("type", "")
                        if src_type and tgt_type and not self._types_compatible(
                            src_type, tgt_type
                        ):
                            inconsistencies.append(
                                SchemaInconsistency(
                                    entity_name=entity_mapping.entity_name,
                                    source_system=src,
                                    target_system=tgt,
                                    inconsistency_type="type_mismatch",
                                    field_name=fname,
                                    source_definition=src_type,
                                    target_definition=tgt_type,
                                    severity=SeverityLevel.HIGH,
                                    impact_description=(
                                        f"Field '{fname}' has type '{src_type}' in {src} "
                                        f"but '{tgt_type}' in {tgt}. "
                                        "Data type coercion may silently corrupt values."
                                    ),
                                    remediation_hint=(
                                        f"Align the type of '{fname}' between {src} and {tgt}, "
                                        "or add explicit casting in the ETL pipeline."
                                    ),
                                )
                            )

        return inconsistencies

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_per_system_fields(
        self, entity_mapping: EntityMapping
    ) -> Dict[str, Dict[str, Any]]:
        """Return a dict of system → {field_name → field_meta} from the mapping."""
        result: Dict[str, Dict[str, Any]] = {}
        for sys_name, schema in entity_mapping.system_representations.items():
            result[sys_name] = {
                f.get("name", ""): f
                for f in schema.get("fields", [])
                if f.get("name")
            }
        return result

    def _type_similarity(self, type_a: str, type_b: str) -> float:
        """Return a similarity score (0–1) between two SQL/schema data type strings."""
        if not type_a or not type_b:
            return 0.5
        a = type_a.lower().strip()
        b = type_b.lower().strip()
        if a == b:
            return 1.0
        for equivalence_group in self._TYPE_EQUIVALENCES:
            if a in equivalence_group and b in equivalence_group:
                return 0.9
        return 0.2

    def _types_compatible(self, type_a: str, type_b: str) -> bool:
        """Return True if the two type strings are considered compatible."""
        return self._type_similarity(type_a, type_b) >= 0.9
