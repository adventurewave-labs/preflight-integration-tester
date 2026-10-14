"""Unit tests for the cross-system entity matcher."""
import pytest

from preflight.analysis.entity_matcher import (
    EntityMatcher,
    EntityGroup,
    SEED_VOCABULARY,
    normalize_table_name,
)


def _fields(*names):
    return [{"name": n, "type": "varchar(50)", "nullable": True} for n in names]


DEMO_SCHEMAS = {
    "salesforce": {
        "Account": _fields("Id", "Name", "Phone", "AnnualRevenue"),
        "Contact": _fields("Id", "AccountId", "LastName", "Email"),
    },
    "sap": {
        "KUNNR": _fields("KUNNR", "NAME1", "TELF1", "UMSAV"),
        "VBELN": _fields("VBELN", "KUNNR", "ERDAT"),
    },
}


class TestNormalizeTableName:
    def test_plural_stripped(self):
        assert normalize_table_name("customers") == "customer"

    def test_plural_not_stripped_for_ss_us_is(self):
        assert normalize_table_name("address") == "address"
        assert normalize_table_name("status") == "status"
        assert normalize_table_name("analysis") == "analysis"

    def test_prefix_stripped(self):
        assert normalize_table_name("tbl_customer") == "customer"
        assert normalize_table_name("dim_product") == "product"

    def test_uppercase_and_separators(self):
        assert normalize_table_name("CUST-MSTR") == "cust_mstr"


class TestVocabularyMatching:
    def test_account_vs_kunnr_grouped_as_customer(self):
        groups = EntityMatcher().build_entity_groups(DEMO_SCHEMAS)
        customer = [g for g in groups if g.canonical_name == "customer"]
        assert len(customer) == 1
        group = customer[0]
        assert group.tables == {"salesforce": "Account", "sap": "KUNNR"}
        assert group.method == "vocabulary"
        assert group.confidence == pytest.approx(0.9)

    def test_single_system_vocabulary_table_not_grouped(self):
        schemas = {"sap": {"KUNNR": _fields("KUNNR", "NAME1")}}
        groups = EntityMatcher().build_entity_groups(schemas)
        assert groups == []

    def test_seed_vocabulary_contains_core_entities(self):
        for entity in ("customer", "contact", "order", "product", "employee"):
            assert entity in SEED_VOCABULARY


class TestExactMatching:
    def test_same_name_across_systems(self):
        schemas = {
            "crm": {"customer": _fields("id", "name")},
            "erp": {"customer": _fields("customer_id", "name")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        exact = [g for g in groups if g.method == "exact"]
        assert len(exact) == 1
        assert exact[0].canonical_name == "customer"
        assert exact[0].confidence == 1.0

    def test_plural_and_singular_match(self):
        schemas = {
            "crm": {"customer": _fields("id", "name")},
            "erp": {"customers": _fields("id", "name")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        exact = [g for g in groups if g.method == "exact"]
        assert len(exact) == 1


class TestDeclaredEntityMatching:
    def test_declared_entity_links_tables(self):
        schemas = {
            "crm": {"client_master": _fields("client_id", "full_name", "email")},
            "billing": {"payer": _fields("payer_id", "payer_name", "email")},
        }
        declared = [{"name": "customer", "fields": ["id", "name", "email"]}]
        groups = EntityMatcher().build_entity_groups(schemas, declared)
        declared_groups = [g for g in groups if g.method == "declared"]
        assert len(declared_groups) == 1
        assert declared_groups[0].canonical_name == "customer"
        assert set(declared_groups[0].tables) == {"crm", "billing"}

    def test_below_threshold_not_matched(self):
        schemas = {
            "crm": {"audit_log": _fields("event", "ts", "payload")},
            "erp": {"shipments": _fields("port", "vessel", "tonnage")},
        }
        declared = [{"name": "customer", "fields": ["id", "name", "email"]}]
        groups = EntityMatcher().build_entity_groups(schemas, declared)
        assert [g for g in groups if g.method == "declared"] == []


class TestFieldOverlapMatching:
    def test_high_overlap_tables_paired(self):
        schemas = {
            "a": {"legacy_inv": _fields("invoice_num", "invoice_date", "amount", "currency")},
            "b": {"billing_hdr": _fields("invoice_num", "invoice_date", "amount", "currency")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        overlap = [g for g in groups if g.method == "field_overlap"]
        assert len(overlap) == 1
        assert overlap[0].confidence > 0.5

    def test_disjoint_tables_not_paired(self):
        schemas = {
            "a": {"users": _fields("login", "password_hash", "last_seen")},
            "b": {"weather": _fields("station", "temperature", "humidity")},
        }
        groups = EntityMatcher().build_entity_groups(schemas)
        assert groups == []


class TestEntityGroup:
    def test_as_match_info(self):
        group = EntityGroup(
            canonical_name="customer",
            tables={"salesforce": "Account", "sap": "KUNNR"},
            method="vocabulary",
            confidence=0.9,
        )
        info = group.as_match_info()
        assert info["canonical_name"] == "customer"
        assert info["matched_tables"]["sap"] == "KUNNR"
        assert info["method"] == "vocabulary"
        assert info["confidence"] == 0.9
