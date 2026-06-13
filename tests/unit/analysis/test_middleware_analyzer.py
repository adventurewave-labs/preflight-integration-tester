"""
Unit tests for MiddlewareAnalyzer.
"""
import pytest
from preflight.analysis.middleware_analyzer import MiddlewareAnalyzer, INTEGRATION_PATTERNS


class TestMiddlewareAnalyzer:
    """Tests for middleware gap analysis."""

    def setup_method(self):
        self.analyzer = MiddlewareAnalyzer()

    def test_no_gaps_when_all_middleware_exists(self):
        connections = [{'name': 'system1', 'type': 'ERP'}, {'name': 'system2', 'type': 'CRM'}]
        existing = list(INTEGRATION_PATTERNS.keys())  # All patterns exist
        gaps = self.analyzer.analyze(
            connected_systems=connections,
            scenario={'name': 'test'},
            existing_middleware=existing,
        )
        assert len(gaps) == 0

    def test_semantic_layer_always_required(self):
        connections = [{'name': 'system1', 'type': 'ERP'}]
        gaps = self.analyzer.analyze(
            connected_systems=connections,
            scenario={'name': 'test'},
            existing_middleware=[],
        )
        gap_types = [g['type'] for g in gaps]
        assert 'semantic_layer' in gap_types

    def test_api_gateway_required_for_many_systems(self):
        connections = [
            {'name': f'system{i}', 'type': 'ERP'} for i in range(5)
        ]
        gaps = self.analyzer.analyze(
            connected_systems=connections,
            scenario={'name': 'test'},
            existing_middleware=[],
        )
        gap_types = [g['type'] for g in gaps]
        assert 'api_gateway' in gap_types

    def test_api_gateway_not_required_for_few_systems(self):
        connections = [{'name': 'system1', 'type': 'ERP'}]
        gaps = self.analyzer.analyze(
            connected_systems=connections,
            scenario={'name': 'test'},
            existing_middleware=[],
        )
        gap_types = [g['type'] for g in gaps]
        assert 'api_gateway' not in gap_types

    def test_etl_required_when_many_inconsistencies(self):
        connections = [{'name': 'system1', 'type': 'ERP'}, {'name': 'system2', 'type': 'CRM'}]
        schema_analysis = {
            'inconsistencies': [
                {'type': 'key_mismatch', 'severity': 'CRITICAL'},
                {'type': 'missing_field', 'severity': 'HIGH'},
                {'type': 'type_mismatch', 'severity': 'MEDIUM'},
                {'type': 'missing_field', 'severity': 'HIGH'},
            ]
        }
        gaps = self.analyzer.analyze(
            connected_systems=connections,
            scenario={'name': 'test'},
            schema_analysis=schema_analysis,
            existing_middleware=[],
        )
        gap_types = [g['type'] for g in gaps]
        assert 'etl_pipeline' in gap_types

    def test_gaps_sorted_by_severity(self):
        connections = [{'name': f'system{i}', 'type': 'ERP'} for i in range(5)]
        gaps = self.analyzer.analyze(
            connected_systems=connections,
            scenario={'name': 'test'},
            existing_middleware=[],
        )
        if len(gaps) >= 2:
            severity_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
            for i in range(len(gaps) - 1):
                curr = severity_order.get(gaps[i]['severity'], 99)
                nxt = severity_order.get(gaps[i+1]['severity'], 99)
                assert curr <= nxt

    def test_effort_estimate(self, sample_middleware_gaps):
        min_days, max_days = self.analyzer.estimate_total_effort(sample_middleware_gaps)
        assert min_days > 0
        assert max_days >= min_days

    def test_blocking_gaps_identified(self):
        connections = [{'name': 'erp', 'type': 'ERP'}, {'name': 'crm', 'type': 'CRM'}]
        schema_analysis = {
            'inconsistencies': [
                {'type': 'key_mismatch', 'severity': 'CRITICAL'},
                {'type': 'key_mismatch', 'severity': 'CRITICAL'},
                {'type': 'key_mismatch', 'severity': 'CRITICAL'},
                {'type': 'key_mismatch', 'severity': 'CRITICAL'},
            ]
        }
        gaps = self.analyzer.analyze(
            connected_systems=connections,
            scenario={'name': 'test'},
            schema_analysis=schema_analysis,
            existing_middleware=[],
        )
        blocking = [g for g in gaps if g.get('blocking', False)]
        # With many key mismatches, should have at least one blocking gap
        assert len(blocking) >= 1

    def test_gap_structure(self):
        connections = [{'name': 'system1', 'type': 'ERP'}]
        gaps = self.analyzer.analyze(connections, {'name': 'test'})
        for gap in gaps:
            assert 'id' in gap
            assert 'type' in gap
            assert 'severity' in gap
            assert 'blocking' in gap
            assert 'description' in gap
            assert 'effort_days' in gap
            assert isinstance(gap['effort_days'], tuple)
            assert len(gap['effort_days']) == 2
