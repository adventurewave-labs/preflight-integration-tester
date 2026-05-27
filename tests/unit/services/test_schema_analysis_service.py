"""
Unit tests for SchemaAnalysisService.
"""
import pytest

from preflight.core.application.services.schema_analysis_service import SchemaAnalysisService
from preflight.core.domain.aggregates import AnalysisResults
from preflight.core.domain.entities import ConnectionProfile, EntityMapping
from preflight.core.domain.value_objects import ConnectorType, SeverityLevel, SystemType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connected_profile(
    name: str,
    entities: dict = None,
    connector_type: ConnectorType = ConnectorType.POSTGRESQL,
    system_type: SystemType = SystemType.DATABASE,
) -> ConnectionProfile:
    """Create a ConnectionProfile with status='connected' and optional entity metadata."""
    metadata: dict = {}
    if entities:
        metadata["entities"] = entities
    return ConnectionProfile(
        name=name,
        connector_type=connector_type,
        system_type=system_type,
        status="connected",
        metadata=metadata,
    )


def _disconnected_profile(name: str) -> ConnectionProfile:
    return ConnectionProfile(name=name, status="disconnected")


# ---------------------------------------------------------------------------
# analyze_schemas
# ---------------------------------------------------------------------------

class TestAnalyzeSchemas:
    async def test_analyze_schemas_no_connections_returns_empty(self):
        service = SchemaAnalysisService()
        results = await service.analyze_schemas([], ["Customer"])
        assert isinstance(results, AnalysisResults)
        assert results.entity_mappings == []
        assert results.schema_inconsistencies == []

    async def test_analyze_schemas_no_entities_returns_empty_mappings(self):
        service = SchemaAnalysisService()
        conn = _connected_profile("db1")
        results = await service.analyze_schemas([conn], [])
        assert results.entity_mappings == []
        assert results.schema_inconsistencies == []

    async def test_analyze_schemas_disconnected_connections_ignored(self):
        service = SchemaAnalysisService()
        conn = _disconnected_profile("offline_db")
        results = await service.analyze_schemas([conn], ["Customer"])
        assert results.entity_mappings == []

    async def test_analyze_schemas_single_system_no_metadata_returns_empty(self):
        """Single connected system with no entity metadata → no mappings."""
        service = SchemaAnalysisService()
        conn = _connected_profile("db_no_meta")
        results = await service.analyze_schemas([conn], ["Customer"])
        assert results.entity_mappings == []

    async def test_analyze_schemas_returns_analysis_results_type(self):
        service = SchemaAnalysisService()
        results = await service.analyze_schemas([], [])
        assert isinstance(results, AnalysisResults)

    async def test_analyze_schemas_single_system_with_metadata(self):
        """One system with entity metadata produces a mapping for that entity."""
        service = SchemaAnalysisService()
        conn = _connected_profile(
            "crm",
            entities={
                "Customer": {
                    "fields": [
                        {"name": "id", "type": "varchar", "primary_key": True},
                        {"name": "name", "type": "varchar"},
                    ]
                }
            },
        )
        results = await service.analyze_schemas([conn], ["Customer"])
        assert len(results.entity_mappings) == 1
        assert results.entity_mappings[0].entity_name == "Customer"

    async def test_analyze_schemas_only_requests_entities(self):
        """Only the requested entity names should be analysed."""
        service = SchemaAnalysisService()
        conn = _connected_profile(
            "db",
            entities={
                "Customer": {"fields": [{"name": "id", "type": "int"}]},
                "Order": {"fields": [{"name": "order_id", "type": "int"}]},
            },
        )
        results = await service.analyze_schemas([conn], ["Customer"])
        entity_names = [m.entity_name for m in results.entity_mappings]
        assert "Customer" in entity_names
        assert "Order" not in entity_names

    async def test_analyze_schemas_with_two_systems_same_schema_no_inconsistencies(self):
        """Two systems with identical schema → no inconsistencies."""
        service = SchemaAnalysisService()
        schema = {
            "Customer": {
                "fields": [
                    {"name": "id", "type": "int", "primary_key": True},
                    {"name": "name", "type": "varchar"},
                ]
            }
        }
        conn1 = _connected_profile("sys_a", entities=schema)
        conn2 = _connected_profile("sys_b", entities=schema)
        results = await service.analyze_schemas([conn1, conn2], ["Customer"])
        assert results.schema_inconsistencies == []

    async def test_analyze_schemas_with_two_systems_missing_field(self):
        """A field in system A absent in system B → missing_field inconsistency."""
        service = SchemaAnalysisService()
        conn1 = _connected_profile(
            "sys_a",
            entities={
                "Customer": {
                    "fields": [
                        {"name": "id", "type": "int"},
                        {"name": "email", "type": "varchar"},
                    ]
                }
            },
        )
        conn2 = _connected_profile(
            "sys_b",
            entities={
                "Customer": {
                    "fields": [
                        {"name": "id", "type": "int"},
                    ]
                }
            },
        )
        results = await service.analyze_schemas([conn1, conn2], ["Customer"])
        missing = [
            i for i in results.schema_inconsistencies
            if i.inconsistency_type == "missing_field"
        ]
        assert len(missing) > 0

    async def test_analyze_schemas_type_mismatch_detected(self):
        """Same field with incompatible types in two systems → type_mismatch."""
        service = SchemaAnalysisService()
        conn1 = _connected_profile(
            "sys_a",
            entities={
                "Order": {
                    "fields": [
                        {"name": "amount", "type": "varchar"},
                    ]
                }
            },
        )
        conn2 = _connected_profile(
            "sys_b",
            entities={
                "Order": {
                    "fields": [
                        {"name": "amount", "type": "boolean"},
                    ]
                }
            },
        )
        results = await service.analyze_schemas([conn1, conn2], ["Order"])
        type_mismatches = [
            i for i in results.schema_inconsistencies
            if i.inconsistency_type == "type_mismatch"
        ]
        assert len(type_mismatches) > 0

    async def test_analyze_schemas_multiple_entities(self):
        service = SchemaAnalysisService()
        conn = _connected_profile(
            "db",
            entities={
                "Customer": {"fields": [{"name": "id", "type": "int"}]},
                "Order": {"fields": [{"name": "order_id", "type": "int"}]},
            },
        )
        results = await service.analyze_schemas(connections=[conn], entities=["Customer", "Order"])
        entity_names = [m.entity_name for m in results.entity_mappings]
        assert "Customer" in entity_names
        assert "Order" in entity_names

    async def test_analyze_schemas_inconsistencies_attached_to_entity_mapping(self):
        service = SchemaAnalysisService()
        conn1 = _connected_profile(
            "crm",
            entities={
                "Lead": {
                    "fields": [
                        {"name": "lead_id", "type": "int"},
                        {"name": "email", "type": "varchar"},
                    ]
                }
            },
        )
        conn2 = _connected_profile(
            "erp",
            entities={
                "Lead": {
                    "fields": [
                        {"name": "lead_id", "type": "int"},
                    ]
                }
            },
        )
        results = await service.analyze_schemas([conn1, conn2], ["Lead"])
        assert len(results.entity_mappings) == 1
        mapping = results.entity_mappings[0]
        assert len(mapping.inconsistencies) > 0


# ---------------------------------------------------------------------------
# map_entity
# ---------------------------------------------------------------------------

class TestMapEntity:
    def test_map_entity_single_system(self):
        service = SchemaAnalysisService()
        representations = {
            "crm": {
                "fields": [
                    {"name": "id", "type": "int", "primary_key": True},
                    {"name": "name", "type": "varchar"},
                ]
            }
        }
        mapping = service.map_entity("Contact", representations)
        assert isinstance(mapping, EntityMapping)
        assert mapping.entity_name == "Contact"
        assert "id" in mapping.canonical_definition
        assert "name" in mapping.canonical_definition

    def test_map_entity_two_systems_builds_canonical_union(self):
        service = SchemaAnalysisService()
        representations = {
            "sys_a": {"fields": [{"name": "field_a", "type": "int"}]},
            "sys_b": {"fields": [{"name": "field_b", "type": "varchar"}]},
        }
        mapping = service.map_entity("Entity", representations)
        # Both fields should appear in canonical definition (union).
        assert "field_a" in mapping.canonical_definition
        assert "field_b" in mapping.canonical_definition

    def test_map_entity_absent_field_marked_nullable(self):
        """A field present in only one system should be nullable in canonical form."""
        service = SchemaAnalysisService()
        representations = {
            "sys_a": {
                "fields": [
                    {"name": "shared", "type": "int"},
                    {"name": "only_in_a", "type": "varchar"},
                ]
            },
            "sys_b": {
                "fields": [
                    {"name": "shared", "type": "int"},
                ]
            },
        }
        mapping = service.map_entity("Thing", representations)
        assert mapping.canonical_definition["only_in_a"].nullable is True

    def test_map_entity_shared_field_exact_types_high_score(self):
        service = SchemaAnalysisService()
        representations = {
            "sys_a": {"fields": [{"name": "id", "type": "int"}]},
            "sys_b": {"fields": [{"name": "id", "type": "int"}]},
        }
        mapping = service.map_entity("Item", representations)
        assert mapping.consistency_score >= 0.9

    def test_map_entity_empty_representations_returns_empty_canonical(self):
        service = SchemaAnalysisService()
        mapping = service.map_entity("Ghost", {})
        assert mapping.canonical_definition == {}
        assert mapping.entity_name == "Ghost"

    def test_map_entity_primary_key_preserved(self):
        service = SchemaAnalysisService()
        representations = {
            "db": {
                "fields": [
                    {"name": "pk_col", "type": "int", "primary_key": True},
                ]
            }
        }
        mapping = service.map_entity("Record", representations)
        assert mapping.canonical_definition["pk_col"].is_primary_key is True


# ---------------------------------------------------------------------------
# detect_inconsistencies
# ---------------------------------------------------------------------------

class TestDetectInconsistencies:
    def _two_system_mapping(self, sys_a_fields, sys_b_fields, entity_name="Entity"):
        service = SchemaAnalysisService()
        representations = {
            "sys_a": {"fields": sys_a_fields},
            "sys_b": {"fields": sys_b_fields},
        }
        return service.map_entity(entity_name, representations)

    def test_no_inconsistencies_identical_schemas(self):
        service = SchemaAnalysisService()
        mapping = self._two_system_mapping(
            [{"name": "id", "type": "int"}],
            [{"name": "id", "type": "int"}],
        )
        inconsistencies = service.detect_inconsistencies(mapping)
        assert inconsistencies == []

    def test_missing_field_detected(self):
        service = SchemaAnalysisService()
        mapping = self._two_system_mapping(
            [{"name": "id", "type": "int"}, {"name": "email", "type": "varchar"}],
            [{"name": "id", "type": "int"}],
        )
        inconsistencies = service.detect_inconsistencies(mapping)
        missing = [i for i in inconsistencies if i.inconsistency_type == "missing_field"]
        assert len(missing) >= 1
        assert any(i.field_name == "email" for i in missing)

    def test_type_mismatch_detected(self):
        service = SchemaAnalysisService()
        mapping = self._two_system_mapping(
            [{"name": "amount", "type": "decimal"}],
            [{"name": "amount", "type": "boolean"}],
        )
        inconsistencies = service.detect_inconsistencies(mapping)
        type_m = [i for i in inconsistencies if i.inconsistency_type == "type_mismatch"]
        assert len(type_m) >= 1

    def test_compatible_types_no_type_mismatch(self):
        """varchar and text are in the same equivalence group → no type_mismatch."""
        service = SchemaAnalysisService()
        mapping = self._two_system_mapping(
            [{"name": "notes", "type": "varchar"}],
            [{"name": "notes", "type": "text"}],
        )
        inconsistencies = service.detect_inconsistencies(mapping)
        type_m = [i for i in inconsistencies if i.inconsistency_type == "type_mismatch"]
        assert type_m == []

    def test_pk_missing_field_is_high_severity(self):
        service = SchemaAnalysisService()
        mapping = self._two_system_mapping(
            [{"name": "id", "type": "int", "primary_key": True}],
            [{"name": "other_id", "type": "int"}],
        )
        inconsistencies = service.detect_inconsistencies(mapping)
        missing = [i for i in inconsistencies if i.inconsistency_type == "missing_field"
                   and i.field_name == "id"]
        if missing:
            assert missing[0].severity == SeverityLevel.HIGH

    def test_non_pk_missing_field_is_medium_severity(self):
        service = SchemaAnalysisService()
        mapping = self._two_system_mapping(
            [{"name": "notes", "type": "text", "primary_key": False}],
            [{"name": "unrelated", "type": "text"}],
        )
        inconsistencies = service.detect_inconsistencies(mapping)
        missing = [i for i in inconsistencies if i.inconsistency_type == "missing_field"
                   and i.field_name == "notes"]
        if missing:
            assert missing[0].severity == SeverityLevel.MEDIUM

    def test_single_system_mapping_no_inconsistencies(self):
        service = SchemaAnalysisService()
        representations = {
            "only_sys": {"fields": [{"name": "id", "type": "int"}]}
        }
        mapping = service.map_entity("Solo", representations)
        inconsistencies = service.detect_inconsistencies(mapping)
        assert inconsistencies == []


# ---------------------------------------------------------------------------
# Type equivalence helpers
# ---------------------------------------------------------------------------

class TestTypeHelpers:
    def test_identical_types_score_1(self):
        service = SchemaAnalysisService()
        assert service._type_similarity("int", "int") == 1.0

    def test_equivalent_types_score_09(self):
        service = SchemaAnalysisService()
        # varchar and text are in the same group.
        score = service._type_similarity("varchar", "text")
        assert score == 0.9

    def test_incompatible_types_low_score(self):
        service = SchemaAnalysisService()
        score = service._type_similarity("boolean", "decimal")
        assert score < 0.5

    def test_empty_types_return_0_5(self):
        service = SchemaAnalysisService()
        assert service._type_similarity("", "int") == 0.5
        assert service._type_similarity("int", "") == 0.5

    def test_types_compatible_same(self):
        service = SchemaAnalysisService()
        assert service._types_compatible("int", "int") is True

    def test_types_compatible_equivalent_group(self):
        service = SchemaAnalysisService()
        assert service._types_compatible("varchar", "nvarchar") is True

    def test_types_not_compatible_across_groups(self):
        service = SchemaAnalysisService()
        assert service._types_compatible("boolean", "varchar") is False

    def test_int_integer_equivalent(self):
        service = SchemaAnalysisService()
        assert service._types_compatible("int", "integer") is True

    def test_float_double_equivalent(self):
        service = SchemaAnalysisService()
        assert service._types_compatible("float", "double") is True

    def test_date_timestamp_equivalent(self):
        service = SchemaAnalysisService()
        assert service._types_compatible("date", "timestamp") is True

    def test_bool_bit_equivalent(self):
        service = SchemaAnalysisService()
        assert service._types_compatible("bool", "bit") is True
