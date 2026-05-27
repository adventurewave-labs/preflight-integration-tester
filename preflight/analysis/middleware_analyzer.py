"""
Middleware Gap Analyzer

Identifies missing integration components required for the proposed AI deployment.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

@dataclass
class IntegrationPattern:
    """A recognized integration pattern."""
    name: str
    description: str
    required_for: List[str]  # use cases
    effort_days: Tuple[int, int]  # min, max

# Known integration patterns library
INTEGRATION_PATTERNS = {
    'rest_api': IntegrationPattern(
        name='REST API Gateway',
        description='HTTP/REST interface for AI agent queries',
        required_for=['real_time_lookup', 'agent_integration'],
        effort_days=(3, 10),
    ),
    'message_queue': IntegrationPattern(
        name='Message Queue / Event Bus',
        description='Async event streaming for AI agent notifications',
        required_for=['event_driven', 'async_processing'],
        effort_days=(5, 15),
    ),
    'etl_pipeline': IntegrationPattern(
        name='ETL/ELT Data Pipeline',
        description='Data synchronization between systems',
        required_for=['data_consistency', 'unified_data_model'],
        effort_days=(10, 40),
    ),
    'api_gateway': IntegrationPattern(
        name='Enterprise API Gateway',
        description='Centralized API management with auth/rate limiting',
        required_for=['multi_system_access', 'security'],
        effort_days=(5, 20),
    ),
    'data_virtualization': IntegrationPattern(
        name='Data Virtualization Layer',
        description='Unified query interface across heterogeneous systems',
        required_for=['unified_data_model', 'cross_system_query'],
        effort_days=(15, 60),
    ),
    'identity_federation': IntegrationPattern(
        name='Identity Federation / SSO',
        description='Unified authentication across enterprise systems',
        required_for=['security', 'agent_authentication'],
        effort_days=(3, 15),
    ),
    'semantic_layer': IntegrationPattern(
        name='Business Semantic Layer',
        description='Maps technical data models to business concepts',
        required_for=['ai_comprehension', 'entity_resolution'],
        effort_days=(10, 30),
    ),
}

class MiddlewareAnalyzer:
    """Analyzes what middleware is missing for the proposed AI deployment."""

    def __init__(self):
        self._identified_gaps = []

    def analyze(
        self,
        connected_systems: List[Dict],
        scenario: Dict,
        schema_analysis: Optional[Dict] = None,
        existing_middleware: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Identify middleware gaps for the proposed deployment.

        Returns list of gap dictionaries sorted by priority.
        """
        gaps = []
        existing = set(existing_middleware or [])
        system_types = {s.get('type', '') for s in connected_systems}

        # Check for API gateway need
        if len(connected_systems) > 2 and 'api_gateway' not in existing:
            gaps.append({
                'id': f'gap_api_gateway',
                'type': 'api_gateway',
                'severity': 'HIGH',
                'blocking': len(connected_systems) > 4,
                'description': f'No API gateway detected for {len(connected_systems)} systems',
                'pattern': INTEGRATION_PATTERNS['api_gateway'],
                'systems': [s['name'] for s in connected_systems],
                'effort_days': INTEGRATION_PATTERNS['api_gateway'].effort_days,
            })

        # Check for data consistency needs
        if schema_analysis and len(schema_analysis.get('inconsistencies', [])) > 3:
            if 'etl_pipeline' not in existing:
                gaps.append({
                    'id': 'gap_etl_pipeline',
                    'type': 'etl_pipeline',
                    'severity': 'HIGH',
                    'blocking': True,
                    'description': 'Schema inconsistencies require ETL pipeline for data harmonization',
                    'pattern': INTEGRATION_PATTERNS['etl_pipeline'],
                    'effort_days': INTEGRATION_PATTERNS['etl_pipeline'].effort_days,
                })

        # Check for semantic layer (critical for AI agents)
        if 'semantic_layer' not in existing:
            critical_count = len([i for i in schema_analysis.get('inconsistencies', []) if i.get('type') == 'key_mismatch']) if schema_analysis else 0
            gaps.append({
                'id': 'gap_semantic_layer',
                'type': 'semantic_layer',
                'severity': 'CRITICAL' if critical_count > 0 else 'HIGH',
                'blocking': critical_count > 0,
                'description': 'AI agents need a semantic layer to understand business entities across systems',
                'pattern': INTEGRATION_PATTERNS['semantic_layer'],
                'effort_days': INTEGRATION_PATTERNS['semantic_layer'].effort_days,
            })

        # Check for real-time API availability
        has_erp = any(s.get('type') == 'ERP' for s in connected_systems)
        has_crm = any(s.get('type') == 'CRM' for s in connected_systems)
        if has_erp and has_crm and 'rest_api' not in existing:
            gaps.append({
                'id': 'gap_rest_api',
                'type': 'rest_api',
                'severity': 'MEDIUM',
                'blocking': False,
                'description': 'ERP systems typically lack REST APIs suitable for real-time AI agent queries',
                'pattern': INTEGRATION_PATTERNS['rest_api'],
                'effort_days': INTEGRATION_PATTERNS['rest_api'].effort_days,
            })

        # Sort by severity then blocking status
        severity_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
        gaps.sort(key=lambda g: (severity_order.get(g['severity'], 99), not g.get('blocking', False)))

        self._identified_gaps = gaps
        return gaps

    def estimate_total_effort(self, gaps: List[Dict]) -> Tuple[int, int]:
        """Estimate total effort range in days for all gaps."""
        min_days = sum(g['effort_days'][0] for g in gaps)
        max_days = sum(g['effort_days'][1] for g in gaps)
        return min_days, max_days
