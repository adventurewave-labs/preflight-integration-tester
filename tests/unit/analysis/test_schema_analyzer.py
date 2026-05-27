"""
Unit tests for SchemaAnalyzer.

Tests schema consistency detection across enterprise systems.
"""
import pytest
from preflight.analysis.schema_analyzer import SchemaAnalyzer, FieldComparison, EntityComparisonResult


class TestFieldNormalization:
    """Tests for field name normalization."""

    def setup_method(self):
        self.analyzer = SchemaAnalyzer()

    def test_camel_case_normalization(self):
        assert self.analyzer.normalize_field_name("FirstName") == "first_name"
        assert self.analyzer.normalize_field_name("AccountId") == "account_id"
        assert self.analyzer.normalize_field_name("BillingStreet") == "billing_street"

    def test_already_snake_case(self):
        result = self.analyzer.normalize_field_name("first_name")
        assert result == "first_name"

    def test_special_characters(self):
        result = self.analyzer.normalize_field_name("email-addr")
        assert result == "email_addr"

    def test_uppercase(self):
        result = self.analyzer.normalize_field_name("KUNNR")
        assert result == "k_u_n_n_r" or "kunnr" in result.lower()

    def test_empty_string(self):
        result = self.analyzer.normalize_field_name("")
        assert result == ""


class TestFieldSimilarity:
    """Tests for field name similarity scoring."""

    def setup_method(self):
        self.analyzer = SchemaAnalyzer()

    def test_identical_fields(self):
        score = self.analyzer.field_similarity("email", "email")
        assert score == 1.0

    def test_similar_fields(self):
        score = self.analyzer.field_similarity("email_address", "email_addr")
        assert score > 0.7

    def test_dissimilar_fields(self):
        score = self.analyzer.field_similarity("customer_id", "product_sku")
        assert score < 0.5

    def test_case_insensitive(self):
        score1 = self.analyzer.field_similarity("Email", "email")
        score2 = self.analyzer.field_similarity("email", "Email")
        assert score1 > 0.9
        assert score2 > 0.9

    def test_sap_vs_standard(self):
        # SAP field names like KUNNR should have low similarity to 'customer_id'
        score = self.analyzer.field_similarity("KUNNR", "customer_id")
        assert score < 0.7


class TestTypeCompatibility:
    """Tests for data type compatibility checking."""

    def setup_method(self):
        self.analyzer = SchemaAnalyzer()

    def test_identical_types(self):
        assert self.analyzer.types_compatible("varchar", "varchar") is True
        assert self.analyzer.types_compatible("integer", "integer") is True

    def test_compatible_aliases(self):
        assert self.analyzer.types_compatible("varchar", "text") is True
        assert self.analyzer.types_compatible("int", "integer") is True
        assert self.analyzer.types_compatible("decimal", "numeric") is True
        assert self.analyzer.types_compatible("timestamp", "datetime") is True

    def test_incompatible_types(self):
        assert self.analyzer.types_compatible("varchar", "integer") is False
        assert self.analyzer.types_compatible("boolean", "decimal") is False

    def test_case_insensitive(self):
        assert self.analyzer.types_compatible("VARCHAR", "text") is True
        assert self.analyzer.types_compatible("INT", "INTEGER") is True


class TestEntityComparison:
    """Tests for cross-system entity comparison."""

    def setup_method(self):
        self.analyzer = SchemaAnalyzer(similarity_threshold=0.7)

    def test_identical_schemas(self):
        schemas = {
            'system_a': [
                {'name': 'id', 'type': 'integer'},
                {'name': 'email', 'type': 'varchar'},
                {'name': 'name', 'type': 'text'},
            ],
            'system_b': [
                {'name': 'id', 'type': 'integer'},
                {'name': 'email', 'type': 'varchar'},
                {'name': 'name', 'type': 'text'},
            ],
        }
        results = self.analyzer.compare_entity_across_systems('customer', schemas)
        assert len(results) == 1
        assert results[0].overall_similarity > 0.9
        assert len(results[0].missing_in_source) == 0
        assert len(results[0].missing_in_target) == 0

    def test_detects_missing_fields(self, mock_salesforce_schema, mock_sap_schema):
        schemas = {
            'salesforce': mock_salesforce_schema['Account'],
            'sap': mock_sap_schema['KUNNR'],
        }
        results = self.analyzer.compare_entity_across_systems('Account', schemas)
        assert len(results) == 1
        # SAP KUNNR is missing BillingStreet equivalent
        all_missing = results[0].missing_in_source + results[0].missing_in_target
        assert len(all_missing) > 0

    def test_detects_type_mismatches(self):
        schemas = {
            'system_a': [
                {'name': 'id', 'type': 'integer'},
                {'name': 'created_date', 'type': 'timestamp'},
            ],
            'system_b': [
                {'name': 'id', 'type': 'integer'},
                {'name': 'created_date', 'type': 'varchar(8)'},  # SAP-style date string
            ],
        }
        results = self.analyzer.compare_entity_across_systems('entity', schemas)
        assert len(results) == 1
        assert len(results[0].type_mismatches) > 0

    def test_three_system_comparison(self, multi_system_schemas):
        # Add a third system
        multi_system_schemas['warehouse'] = {
            'Contact': [
                {'name': 'contact_id', 'type': 'bigint'},
                {'name': 'last_name', 'type': 'varchar'},
                {'name': 'first_name', 'type': 'varchar'},
                {'name': 'email', 'type': 'varchar'},
            ]
        }
        results = self.analyzer.analyze_all(multi_system_schemas)
        assert 'Contact' in results
        # Should produce multiple pairwise comparisons
        assert len(results['Contact']) >= 1


class TestFullAnalysis:
    """Tests for full schema analysis pipeline."""

    def setup_method(self):
        self.analyzer = SchemaAnalyzer()

    def test_analyze_all_returns_entity_results(self, multi_system_schemas):
        results = self.analyzer.analyze_all(multi_system_schemas)
        assert isinstance(results, dict)
        assert len(results) > 0

    def test_only_analyzes_common_entities(self, multi_system_schemas):
        results = self.analyzer.analyze_all(multi_system_schemas)
        # 'Contact' is in both salesforce and sap schemas
        assert 'Contact' in results
        # 'VBELN' is only in SAP, should not appear
        assert 'Opportunity' not in results  # Only in Salesforce

    def test_generate_inconsistency_report(self, multi_system_schemas):
        results = self.analyzer.analyze_all(multi_system_schemas)
        report = self.analyzer.generate_inconsistency_report(results)

        assert isinstance(report, list)
        # Should detect inconsistencies given the intentionally mismatched schemas
        assert len(report) > 0

        # All items should have required fields
        for item in report:
            assert 'entity' in item
            assert 'severity' in item
            assert 'type' in item
            assert 'detail' in item

    def test_report_sorted_by_severity(self, multi_system_schemas):
        results = self.analyzer.analyze_all(multi_system_schemas)
        report = self.analyzer.generate_inconsistency_report(results)

        if len(report) >= 2:
            severity_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'INFO': 4}
            for i in range(len(report) - 1):
                curr = severity_order.get(report[i]['severity'], 99)
                nxt = severity_order.get(report[i+1]['severity'], 99)
                assert curr <= nxt, f"Not sorted at index {i}: {report[i]['severity']} > {report[i+1]['severity']}"

    def test_key_mismatch_is_critical(self, multi_system_schemas):
        results = self.analyzer.analyze_all(multi_system_schemas)
        report = self.analyzer.generate_inconsistency_report(results)

        key_mismatches = [r for r in report if r.get('type') == 'key_mismatch']
        for mismatch in key_mismatches:
            assert mismatch['severity'] == 'CRITICAL'
