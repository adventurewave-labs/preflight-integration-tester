"""
Integration tests for mock connectors.

These tests exercise the full connector interface without real enterprise connections.
"""
import asyncio
import pytest
from preflight.core.infrastructure.connectors.mock_connector import MockEnterpriseConnector
from preflight.core.infrastructure.connectors.connector_factory import create_connector
from preflight.core.infrastructure.connectors.base import BaseConnector, SystemMetadata


class TestMockConnector:
    """Integration tests for MockEnterpriseConnector."""

    @pytest.mark.asyncio
    async def test_connect_succeeds(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        result = await connector.connect()
        assert result is True
        assert connector.is_connected

    @pytest.mark.asyncio
    async def test_get_metadata_returns_tables(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        metadata = await connector.get_metadata()

        assert isinstance(metadata, SystemMetadata)
        assert len(metadata.tables) > 0
        assert metadata.entity_count > 0
        assert metadata.system_name == connector.SYSTEM_NAME

    @pytest.mark.asyncio
    async def test_metadata_has_valid_tables(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        metadata = await connector.get_metadata()

        for table in metadata.tables:
            assert table.name
            assert len(table.columns) > 0
            for col in table.columns:
                assert 'name' in col
                assert 'type' in col

    @pytest.mark.asyncio
    async def test_ping_returns_latency(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        latency = await connector.ping()

        assert isinstance(latency, float)
        assert latency >= 0

    @pytest.mark.asyncio
    async def test_read_query_executes(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        result = await connector.execute_read_query('SELECT * FROM customers LIMIT 10')

        assert result is not None
        assert result.row_count >= 0
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_write_query_blocked(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        # Write queries should be blocked
        is_readonly = await connector.validate_read_only('INSERT INTO customers VALUES (1, 2)')
        assert is_readonly is False

    @pytest.mark.asyncio
    async def test_update_query_blocked(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        is_readonly = await connector.validate_read_only('UPDATE customers SET name = "test"')
        assert is_readonly is False

    @pytest.mark.asyncio
    async def test_delete_query_blocked(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        is_readonly = await connector.validate_read_only('DELETE FROM customers')
        assert is_readonly is False

    @pytest.mark.asyncio
    async def test_select_query_allowed(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        is_readonly = await connector.validate_read_only('SELECT * FROM customers WHERE id = 1')
        assert is_readonly is True

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with MockEnterpriseConnector({'name': 'test'}) as connector:
            assert connector.is_connected
            metadata = await connector.get_metadata()
            assert metadata is not None
        # After context exit, connection should be closed
        assert not connector.is_connected

    @pytest.mark.asyncio
    async def test_disconnect(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        await connector.connect()
        assert connector.is_connected
        await connector.disconnect()
        assert not connector.is_connected

    @pytest.mark.asyncio
    async def test_repr(self):
        connector = MockEnterpriseConnector({'name': 'test'})
        repr_str = repr(connector)
        assert 'MockEnterpriseConnector' in repr_str


class TestConnectorFactory:
    """Tests for the connector factory."""

    def test_create_mock_connector(self):
        connector = create_connector('mock', {'name': 'test'})
        assert isinstance(connector, BaseConnector)

    def test_create_postgresql_connector(self):
        connector = create_connector('postgresql', {
            'host': 'localhost',
            'port': 5432,
            'database': 'test',
        })
        assert isinstance(connector, BaseConnector)

    def test_create_salesforce_connector(self):
        connector = create_connector('salesforce', {
            'username': 'test@test.com',
        })
        assert isinstance(connector, BaseConnector)

    def test_create_snowflake_connector(self):
        connector = create_connector('snowflake', {
            'account': 'test.snowflakecomputing.com',
        })
        assert isinstance(connector, BaseConnector)

    def test_invalid_connector_type_raises(self):
        with pytest.raises((ValueError, KeyError)):
            create_connector('invalid_system_xyz', {})

    def test_case_insensitive_connector_type(self):
        connector_lower = create_connector('postgresql', {})
        connector_upper = create_connector('POSTGRESQL', {})
        # Both should create the same type
        assert type(connector_lower) == type(connector_upper)


class TestConnectionManager:
    """Tests for ConnectionManager."""

    @pytest.mark.asyncio
    async def test_manage_multiple_connections(self):
        from preflight.core.infrastructure.connectors.connection_manager import (
            ConnectionManager, ConnectionSpec
        )

        specs = [
            ConnectionSpec(
                connector_id=f'system_{i}',
                connector_type='mock',
                config={'name': f'system_{i}'},
            )
            for i in range(3)
        ]

        manager = ConnectionManager(specs=specs, max_concurrency=3)
        connected = await manager.connect_all()
        assert len(connected) > 0

        # All should be healthy
        healthy = list(manager.iter_healthy())
        assert len(healthy) > 0

        await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_summary(self):
        from preflight.core.infrastructure.connectors.connection_manager import (
            ConnectionManager, ConnectionSpec
        )

        specs = [
            ConnectionSpec(
                connector_id='test_system',
                connector_type='mock',
                config={'name': 'test'},
            )
        ]
        manager = ConnectionManager(specs=specs)
        await manager.connect_all()

        summary = manager.summary()
        assert isinstance(summary, dict)

        await manager.disconnect_all()
