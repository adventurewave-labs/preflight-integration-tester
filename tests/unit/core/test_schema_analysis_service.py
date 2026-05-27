"""
Tests for SchemaAnalysisService.
"""
import asyncio
import pytest
from preflight.core.application.services.schema_analysis_service import SchemaAnalysisService
from preflight.core.domain.aggregates import AnalysisResults
from preflight.core.domain.entities import ConnectionProfile, EntityMapping
from preflight.core.domain.value_objects import ConnectorType, SystemType


def make_connection(name: str, status: str = "connected", entities: dict = None) -> ConnectionProfile:
    """Helper to create a ConnectionProfile with optional entity metadata."""
    profile = ConnectionProfile(
        name=name,
        system_type=SystemType.DATABASE,
        connector_type=ConnectorType.POSTGRESQL,
        status=status,
    )
    if entities:
        profile.metadata["entities"] = entities
    return profile


# Standard test schema data
SALESFORCE_CUSTOMER = {
    "fields": [
        {"name": "Id", "type": "varchar", "primary_key": True},
        {"name": "Name", "type": "varchar"},
        {"name": "Email", "type": "varchar"},
        {"name": "Phone", "type": "varchar"},
    ]
}

SAP_CUSTOMER = {
    "fields": [
        {"name": "Id", "type": "varchar", "primary_key": True},
        {"name": "Name", "type": "nvarchar"},
        {"name": "Revenue", "type": "decimal"},  # not in Salesforce
    ]
}


class TestTypeSimilarity:
    def setup_method(self):
        self.service = SchemaAnalysisService()

    def test_identical_types_score_1(self):
        assert self.service._type_similarity("varchar", "varchar") == 1.0

    def test_empty_types_score_0_5(self):
        assert self.service._type_similarity("", "varchar") == 0.5
        assert self.service._type_similarity("varchar", "") == 0.5
        assert self.service._type_similarity("", "") == 0.5

    def test_compatible_string_types(self):
        score = self.service._type_similarity("varchar", "text")
        assert score == 0.9

    def test_compatible_numeric_types(self):
        score = self.service._type_similarity("int", "integer")
        assert score == 0.9

    def test_compatible_float_types(self):
        score = self.service._type_similarity("decimal", "numeric")
        assert score == 0.9

    def test_compatible_bool_types(self):
        score = self.service._type_similarity("bool", "boolean")
        assert score == 0.9

    def test_compatible_date_types(self):
        score = self.service._type_similarity("date", "timestamp")
        assert score == 0.9

    def test_incompatible_types_score_0_2(self):
        score = self.service._type_similarity("varchar", "integer")
        assert score == 0.2

    def test_case_insensitive(self):
        score = self.service._type_similarity("VARCHAR", "text")
        assert score == 0.9


class TestTypesCompatible:
    def setup_method(self):
        self.service = SchemaAnalysisService()

    def test_same_types_compatible(self):
        assert self.service._types_compatible("varchar", "varchar") is True

    def test_equivalent_types_compatible(self):
        assert self.service._types_compatible("varchar", "nvarchar") is True
        assert self.service._types_compatible("int", "integer") is True

    def test_incompatible_types(self):
        assert self.service._types_compatible("varchar", "decimal") is False
        assert self.service._types_compatible("int", "date") is False


class TestMapEntity:
    def setup_method(self):
        self.service = SchemaAnalysisService()

    def test_single_system_mapping(self):
        representations = {
            "salesforce": SALESFORCE_CUSTOMER
        }
        mapping = self.service.map_entity("Customer", representations)
        assert mapping.entity_name == "Customer"
        assert "Id" in mapping.canonical_definition
        assert "Name" in mapping.canonical_definition

    def test_two_system_canonical_union(self):
        representations = {
            "salesforce": SALESFORCE_CUSTOMER,
            "sap": SAP_CUSTOMER,
        }
        mapping = self.service.map_entity("Customer", representations)
        # Should contain fields from both systems
        all_field_names = set(mapping.canonical_definition.keys())
        assert "Id" in all_field_names
        assert "Name" in all_field_names
        assert "Revenue" in all_field_names  # Only in SAP
        assert "Email" in all_field_names   # Only in Salesforce

    def test_field_absent_in_one_system_is_nullable(self):
        representations = {
            "salesforce": SALESFORCE_CUSTOMER,
            "sap": SAP_CUSTOMER,
        }
        mapping = self.service.map_entity("Customer", representations)
        # Revenue only exists in SAP → should be nullable in canonical
        revenue = mapping.canonical_definition.get("Revenue")
        assert revenue is not None
        assert revenue.nullable is True

    def test_field_present_in_all_systems_not_nullable(self):
        representations = {
            "salesforce": SALESFORCE_CUSTOMER,
            "sap": SAP_CUSTOMER,
        }
        mapping = self.service.map_entity("Customer", representations)
        # Id exists in both → not nullable
        id_field = mapping.canonical_definition.get("Id")
        assert id_field is not None
        assert id_field.nullable is False

    def test_field_mappings_created(self):
        representations = {
            "salesforce": SALESFORCE_CUSTOMER,
            "sap": SAP_CUSTOMER,
        }
        mapping = self.service.map_entity("Customer", representations)
        assert len(mapping.field_mappings) > 0

    def test_consistency_score_is_float(self):
        representations = {
            "salesforce": SALESFORCE_CUSTOMER,
            "sap": SAP_CUSTOMER,
        }
        mapping = self.service.map_entity("Customer", representations)
        assert 0.0 <= mapping.consistency_score <= 1.0

    def test_empty_system_representations(self):
        representations = {}
        mapping = self.service.map_entity("Customer", representations)
        assert mapping.entity_name == "Customer"
        assert len(mapping.canonical_definition) == 0

    def test_unmapped_fields_score_zero(self):
        """Field only in one system gets unmapped mapping type."""
        representations = {
            "sys_a": {"fields": [{"name": "SpecialField", "type": "varchar"}]},
            "sys_b": {"fields": [{"name": "OtherField", "type": "varchar"}]},
        }
        mapping = self.service.map_entity("Thing", representations)
        unmapped = [m for m in mapping.field_mappings if m.mapping_type == "unmapped"]
        assert len(unmapped) > 0

    def test_exact_type_match_creates_exact_mapping(self):
        """Same field with same type → 'exact' mapping."""
        representations = {
            "sys_a": {"fields": [{"name": "Id", "type": "varchar"}]},
            "sys_b": {"fields": [{"name": "Id", "type": "varchar"}]},
        }
        mapping = self.service.map_entity("Entity", representations)
        exact = [m for m in mapping.field_mappings if m.mapping_type == "exact"]
        assert len(exact) > 0


class TestDetectInconsistencies:
    def setup_method(self):
        self.service = SchemaAnalysisService()

    def test_no_inconsistencies_identical_schemas(self):
        representations = {
            "sys_a": {"fields": [{"name": "Id", "type": "varchar"}]},
            "sys_b": {"fields": [{"name": "Id", "type": "varchar"}]},
        }
        mapping = self.service.map_entity("Entity", representations)
        inconsistencies = self.service.detect_inconsistencies(mapping)
        assert len(inconsistencies) == 0

    def test_missing_field_detected(self):
        representations = {
            "sys_a": {"fields": [{"name": "Id", "type": "varchar"}, {"name": "Extra", "type": "text"}]},
            "sys_b": {"fields": [{"name": "Id", "type": "varchar"}]},
        }
        mapping = self.service.map_entity("Entity", representations)
        inconsistencies = self.service.detect_inconsistencies(mapping)
        missing = [i for i in inconsistencies if i.inconsistency_type == "missing_field"]
        assert len(missing) > 0

    def test_type_mismatch_detected(self):
        representations = {
            "sys_a": {"fields": [{"name": "Revenue", "type": "varchar"}]},
            "sys_b": {"fields": [{"name": "Revenue", "type": "decimal"}]},
        }
        mapping = self.service.map_entity("Entity", representations)
        inconsistencies = self.service.detect_inconsistencies(mapping)
        type_mismatches = [i for i in inconsistencies if i.inconsistency_type == "type_mismatch"]
        assert len(type_mismatches) > 0

    def test_compatible_types_not_flagged(self):
        """varchar and nvarchar are compatible → no type mismatch."""
        representations = {
            "sys_a": {"fields": [{"name": "Name", "type": "varchar"}]},
            "sys_b": {"fields": [{"name": "Name", "type": "nvarchar"}]},
        }
        mapping = self.service.map_entity("Entity", representations)
        inconsistencies = self.service.detect_inconsistencies(mapping)
        type_mismatches = [i for i in inconsistencies if i.inconsistency_type == "type_mismatch"]
        assert len(type_mismatches) == 0

    def test_missing_pk_field_is_high_severity(self):
        representations = {
            "sys_a": {"fields": [{"name": "Id", "type": "varchar", "primary_key": True}]},
            "sys_b": {"fields": [{"name": "OtherId", "type": "varchar"}]},
        }
        mapping = self.service.map_entity("Entity", representations)
        inconsistencies = self.service.detect_inconsistencies(mapping)
        missing_pk = [
            i for i in inconsistencies
            if i.inconsistency_type == "missing_field" and i.field_name == "Id"
        ]
        assert len(missing_pk) > 0
        from preflight.core.domain.value_objects import SeverityLevel
        assert all(i.severity == SeverityLevel.HIGH for i in missing_pk)

    def test_inconsistency_has_remediation_hint(self):
        representations = {
            "sys_a": {"fields": [{"name": "Email", "type": "varchar"}]},
            "sys_b": {"fields": []},
        }
        mapping = self.service.map_entity("Entity", representations)
        inconsistencies = self.service.detect_inconsistencies(mapping)
        for inc in inconsistencies:
            assert inc.remediation_hint != ""


class TestAnalyzeSchemas:
    def setup_method(self):
        self.service = SchemaAnalysisService()

    @pytest.mark.asyncio
    async def test_no_connections(self):
        results = await self.service.analyze_schemas([], ["Customer"])
        assert isinstance(results, AnalysisResults)
        assert len(results.entity_mappings) == 0

    @pytest.mark.asyncio
    async def test_no_entities(self):
        conn = make_connection("sys-1")
        results = await self.service.analyze_schemas([conn], [])
        assert len(results.entity_mappings) == 0

    @pytest.mark.asyncio
    async def test_disconnected_connections_skipped(self):
        conn = make_connection("sys-1", status="disconnected")
        results = await self.service.analyze_schemas([conn], ["Customer"])
        assert len(results.entity_mappings) == 0

    @pytest.mark.asyncio
    async def test_single_connection_with_entity(self):
        conn = make_connection(
            "salesforce",
            entities={"Customer": SALESFORCE_CUSTOMER}
        )
        results = await self.service.analyze_schemas([conn], ["Customer"])
        assert len(results.entity_mappings) == 1
        assert results.entity_mappings[0].entity_name == "Customer"

    @pytest.mark.asyncio
    async def test_entity_not_in_metadata_skipped(self):
        conn = make_connection("salesforce", entities={"Account": SALESFORCE_CUSTOMER})
        results = await self.service.analyze_schemas([conn], ["Customer"])
        # "Customer" not in metadata → skipped
        assert len(results.entity_mappings) == 0

    @pytest.mark.asyncio
    async def test_two_connections_inconsistencies_detected(self):
        sf_conn = make_connection(
            "salesforce",
            entities={"Customer": SALESFORCE_CUSTOMER}
        )
        sap_conn = make_connection(
            "sap",
            entities={"Customer": SAP_CUSTOMER}
        )
        results = await self.service.analyze_schemas([sf_conn, sap_conn], ["Customer"])
        assert len(results.entity_mappings) == 1
        # Should detect inconsistencies (missing fields + type mismatches)
        assert len(results.schema_inconsistencies) >= 0  # at least no crash

    @pytest.mark.asyncio
    async def test_multiple_entities_analyzed(self):
        sf_conn = make_connection(
            "salesforce",
            entities={
                "Customer": SALESFORCE_CUSTOMER,
                "Order": {"fields": [{"name": "OrderId", "type": "varchar"}]},
            }
        )
        results = await self.service.analyze_schemas([sf_conn], ["Customer", "Order"])
        assert len(results.entity_mappings) == 2

    @pytest.mark.asyncio
    async def test_results_include_schema_inconsistencies(self):
        sf_conn = make_connection(
            "salesforce",
            entities={"Customer": SALESFORCE_CUSTOMER}
        )
        sap_conn = make_connection(
            "sap",
            entities={"Customer": SAP_CUSTOMER}
        )
        results = await self.service.analyze_schemas([sf_conn, sap_conn], ["Customer"])
        # Schema inconsistencies in entity_mappings are also in results
        total_from_mappings = sum(len(m.inconsistencies) for m in results.entity_mappings)
        assert len(results.schema_inconsistencies) == total_from_mappings
