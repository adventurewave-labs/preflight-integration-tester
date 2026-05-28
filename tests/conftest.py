"""
Shared pytest fixtures for all tests.
"""
import pytest
from typing import Dict, Any, List


@pytest.fixture
def mock_salesforce_schema() -> Dict[str, List[Dict]]:
    """Realistic Salesforce schema for testing."""
    return {
        'Account': [
            {'name': 'Id', 'type': 'varchar(18)', 'nullable': False},
            {'name': 'Name', 'type': 'varchar(255)', 'nullable': False},
            {'name': 'BillingStreet', 'type': 'text', 'nullable': True},
            {'name': 'Phone', 'type': 'varchar(40)', 'nullable': True},
            {'name': 'AnnualRevenue', 'type': 'decimal', 'nullable': True},
            {'name': 'CreatedDate', 'type': 'timestamp', 'nullable': False},
        ],
        'Contact': [
            {'name': 'Id', 'type': 'varchar(18)', 'nullable': False},
            {'name': 'AccountId', 'type': 'varchar(18)', 'nullable': True},
            {'name': 'FirstName', 'type': 'varchar(40)', 'nullable': True},
            {'name': 'LastName', 'type': 'varchar(80)', 'nullable': False},
            {'name': 'Email', 'type': 'varchar(80)', 'nullable': True},
        ],
        'Opportunity': [
            {'name': 'Id', 'type': 'varchar(18)', 'nullable': False},
            {'name': 'AccountId', 'type': 'varchar(18)', 'nullable': True},
            {'name': 'Name', 'type': 'varchar(120)', 'nullable': False},
            {'name': 'Amount', 'type': 'decimal', 'nullable': True},
            {'name': 'CloseDate', 'type': 'timestamp', 'nullable': False},
            {'name': 'StageName', 'type': 'varchar(40)', 'nullable': False},
        ],
    }


@pytest.fixture
def mock_sap_schema() -> Dict[str, List[Dict]]:
    """Realistic SAP ERP schema for testing (intentionally inconsistent)."""
    return {
        'KUNNR': [  # Customer master — key mismatch with Salesforce!
            {'name': 'KUNNR', 'type': 'varchar(10)', 'nullable': False},  # Different ID format
            {'name': 'NAME1', 'type': 'nvarchar(35)', 'nullable': False},
            {'name': 'NAME2', 'type': 'nvarchar(35)', 'nullable': True},
            {'name': 'TELF1', 'type': 'varchar(16)', 'nullable': True},  # Phone format mismatch
            {'name': 'UMSAV', 'type': 'numeric(15,2)', 'nullable': True},
            {'name': 'ERDAT', 'type': 'varchar(8)', 'nullable': False},  # Date as YYYYMMDD string!
        ],
        'VBELN': [  # Sales order
            {'name': 'VBELN', 'type': 'varchar(10)', 'nullable': False},
            {'name': 'KUNNR', 'type': 'varchar(10)', 'nullable': False},
            {'name': 'ERDAT', 'type': 'varchar(8)', 'nullable': False},
            {'name': 'AUART', 'type': 'varchar(4)', 'nullable': True},
            {'name': 'NETWR', 'type': 'numeric(15,2)', 'nullable': True},
        ],
        'Contact': [  # Same entity name, different fields
            {'name': 'PERNR', 'type': 'varchar(8)', 'nullable': False},  # Different key!
            {'name': 'NACHN', 'type': 'nvarchar(40)', 'nullable': False},
            {'name': 'VORNA', 'type': 'nvarchar(40)', 'nullable': True},
            {'name': 'USRID_LONG', 'type': 'varchar(82)', 'nullable': True},
        ],
    }


@pytest.fixture
def multi_system_schemas(mock_salesforce_schema, mock_sap_schema) -> Dict[str, Dict]:
    """Combined schemas from multiple systems."""
    return {
        'salesforce': mock_salesforce_schema,
        'sap': mock_sap_schema,
    }


@pytest.fixture
def sample_scenario() -> Dict[str, Any]:
    """Sample AI deployment scenario config."""
    return {
        'name': 'Customer Service AI',
        'description': 'AI agent for customer service representatives',
        'systems': ['salesforce', 'sap'],
        'concurrent_users': 25,
        'queries_per_minute': 150,
        'peak_multiplier': 2.5,
        'response_time_target_ms': 500,
        'business_entities': ['customer', 'contact', 'order'],
    }


@pytest.fixture
def sample_middleware_gaps() -> List[Dict]:
    """Sample middleware gaps for testing."""
    return [
        {
            'id': 'gap_semantic_layer',
            'type': 'semantic_layer',
            'severity': 'CRITICAL',
            'blocking': True,
            'description': 'No semantic layer for AI agent entity comprehension',
            'effort_days': (10, 30),
        },
        {
            'id': 'gap_api_gateway',
            'type': 'api_gateway',
            'severity': 'HIGH',
            'blocking': False,
            'description': 'No API gateway for multi-system access',
            'effort_days': (5, 20),
        },
    ]


@pytest.fixture
def sample_schema_inconsistencies() -> List[Dict]:
    """Sample schema inconsistencies for testing."""
    return [
        {
            'entity': 'Contact',
            'type': 'key_mismatch',
            'severity': 'CRITICAL',
            'source': 'salesforce',
            'target': 'sap',
            'detail': "Key fields differ: ['Id'] vs ['PERNR']",
        },
        {
            'entity': 'KUNNR',
            'type': 'missing_field',
            'severity': 'HIGH',
            'source': 'salesforce',
            'target': 'sap',
            'detail': "Field 'BillingStreet' present in salesforce but missing in sap",
        },
    ]
