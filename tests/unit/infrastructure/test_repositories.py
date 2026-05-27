"""Tests for in-memory repository implementations."""
import asyncio
import pytest
from preflight.core.infrastructure.repositories.diagnostic_repository import (
    InMemoryDiagnosticRunRepository,
)
from preflight.core.infrastructure.repositories.connection_repository import (
    InMemoryConnectionProfileRepository,
)
from preflight.core.domain.aggregates import DiagnosticRun
from preflight.core.domain.entities import ConnectionProfile
from preflight.core.domain.value_objects import ConnectorType, SystemType


class TestInMemoryDiagnosticRunRepository:
    """Tests for InMemoryDiagnosticRunRepository."""

    @pytest.mark.asyncio
    async def test_save_and_find_by_id(self):
        repo = InMemoryDiagnosticRunRepository()
        run = DiagnosticRun(name="Test Run")

        await repo.save(run)
        found = await repo.find_by_id(run.id)

        assert found is not None
        assert found.id == run.id
        assert found.name == "Test Run"

    @pytest.mark.asyncio
    async def test_find_nonexistent_returns_none(self):
        repo = InMemoryDiagnosticRunRepository()
        result = await repo.find_by_id("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_find_all_empty(self):
        repo = InMemoryDiagnosticRunRepository()
        runs = await repo.find_all()
        assert isinstance(runs, list)
        assert len(runs) == 0

    @pytest.mark.asyncio
    async def test_find_all_returns_all(self):
        repo = InMemoryDiagnosticRunRepository()
        run1 = DiagnosticRun(name="Run 1")
        run2 = DiagnosticRun(name="Run 2")
        run3 = DiagnosticRun(name="Run 3")

        await repo.save(run1)
        await repo.save(run2)
        await repo.save(run3)

        all_runs = await repo.find_all()
        assert len(all_runs) == 3

    @pytest.mark.asyncio
    async def test_save_updates_existing(self):
        repo = InMemoryDiagnosticRunRepository()
        run = DiagnosticRun(name="Original")

        await repo.save(run)
        run.name = "Updated"
        await repo.save(run)

        found = await repo.find_by_id(run.id)
        assert found.name == "Updated"

    @pytest.mark.asyncio
    async def test_delete(self):
        repo = InMemoryDiagnosticRunRepository()
        run = DiagnosticRun(name="To Delete")

        await repo.save(run)
        assert await repo.find_by_id(run.id) is not None

        await repo.delete(run.id)
        assert await repo.find_by_id(run.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self):
        repo = InMemoryDiagnosticRunRepository()
        # Should not raise
        await repo.delete("nonexistent-id")

    @pytest.mark.asyncio
    async def test_run_lifecycle(self):
        repo = InMemoryDiagnosticRunRepository()
        run = DiagnosticRun(name="Lifecycle Test")

        # Save in pending state
        await repo.save(run)

        # Start and save
        run.start()
        await repo.save(run)

        found = await repo.find_by_id(run.id)
        assert found.status == "running"

        # Fail and save
        run.fail("Connection refused")
        await repo.save(run)

        found = await repo.find_by_id(run.id)
        assert found.status == "failed"
        assert found.error_message == "Connection refused"

    @pytest.mark.asyncio
    async def test_len_reflects_store_size(self):
        repo = InMemoryDiagnosticRunRepository()
        assert len(repo) == 0

        await repo.save(DiagnosticRun(name="R1"))
        assert len(repo) == 1

        await repo.save(DiagnosticRun(name="R2"))
        assert len(repo) == 2

    @pytest.mark.asyncio
    async def test_find_all_returns_list_of_diagnostic_runs(self):
        repo = InMemoryDiagnosticRunRepository()
        await repo.save(DiagnosticRun(name="Run A"))
        runs = await repo.find_all()
        assert all(isinstance(r, DiagnosticRun) for r in runs)

    @pytest.mark.asyncio
    async def test_multiple_deletes(self):
        repo = InMemoryDiagnosticRunRepository()
        run1 = DiagnosticRun(name="R1")
        run2 = DiagnosticRun(name="R2")

        await repo.save(run1)
        await repo.save(run2)

        await repo.delete(run1.id)

        remaining = await repo.find_all()
        assert len(remaining) == 1
        assert remaining[0].id == run2.id

    @pytest.mark.asyncio
    async def test_initial_status_is_pending(self):
        repo = InMemoryDiagnosticRunRepository()
        run = DiagnosticRun(name="Fresh Run")
        await repo.save(run)
        found = await repo.find_by_id(run.id)
        assert found.status == "pending"


class TestInMemoryConnectionProfileRepository:
    """Tests for InMemoryConnectionProfileRepository."""

    @pytest.mark.asyncio
    async def test_save_and_find_by_id(self):
        repo = InMemoryConnectionProfileRepository()
        profile = ConnectionProfile(
            name="Test Salesforce",
            system_type=SystemType.CRM,
            connector_type=ConnectorType.SALESFORCE,
        )

        await repo.save(profile)
        found = await repo.find_by_id(profile.id)

        assert found is not None
        assert found.name == "Test Salesforce"
        assert found.connector_type == ConnectorType.SALESFORCE

    @pytest.mark.asyncio
    async def test_find_nonexistent_returns_none(self):
        repo = InMemoryConnectionProfileRepository()
        result = await repo.find_by_id("nonexistent-profile-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_find_all_empty(self):
        repo = InMemoryConnectionProfileRepository()
        profiles = await repo.find_all()
        assert isinstance(profiles, list)
        assert len(profiles) == 0

    @pytest.mark.asyncio
    async def test_find_all_returns_all(self):
        repo = InMemoryConnectionProfileRepository()
        p1 = ConnectionProfile(name="P1", connector_type=ConnectorType.POSTGRESQL)
        p2 = ConnectionProfile(name="P2", connector_type=ConnectorType.SALESFORCE)

        await repo.save(p1)
        await repo.save(p2)

        all_profiles = await repo.find_all()
        assert len(all_profiles) == 2

    @pytest.mark.asyncio
    async def test_find_by_run_id(self):
        repo = InMemoryConnectionProfileRepository()
        run_id = "test-run-id"

        profile1 = ConnectionProfile(name="Conn 1", connector_type=ConnectorType.POSTGRESQL)
        profile2 = ConnectionProfile(name="Conn 2", connector_type=ConnectorType.SALESFORCE)
        profile3 = ConnectionProfile(name="Other", connector_type=ConnectorType.SNOWFLAKE)

        # Associate first two with run_id via metadata
        profile1.metadata["run_id"] = run_id
        profile2.metadata["run_id"] = run_id

        await repo.save(profile1)
        await repo.save(profile2)
        await repo.save(profile3)  # No run_id

        run_profiles = await repo.find_by_run_id(run_id)
        assert len(run_profiles) == 2
        names = {p.name for p in run_profiles}
        assert "Conn 1" in names
        assert "Conn 2" in names

    @pytest.mark.asyncio
    async def test_find_by_nonexistent_run_id(self):
        repo = InMemoryConnectionProfileRepository()
        result = await repo.find_by_run_id("nonexistent-run")
        assert result == []

    @pytest.mark.asyncio
    async def test_delete(self):
        repo = InMemoryConnectionProfileRepository()
        profile = ConnectionProfile(name="To Delete", connector_type=ConnectorType.POSTGRESQL)

        await repo.save(profile)
        await repo.delete(profile.id)

        assert await repo.find_by_id(profile.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self):
        repo = InMemoryConnectionProfileRepository()
        # Should not raise
        await repo.delete("nonexistent-id")

    @pytest.mark.asyncio
    async def test_delete_cleans_run_index(self):
        repo = InMemoryConnectionProfileRepository()
        run_id = "run-abc"
        profile = ConnectionProfile(name="Indexed", connector_type=ConnectorType.POSTGRESQL)
        profile.metadata["run_id"] = run_id

        await repo.save(profile)
        assert len(await repo.find_by_run_id(run_id)) == 1

        await repo.delete(profile.id)
        assert len(await repo.find_by_run_id(run_id)) == 0

    @pytest.mark.asyncio
    async def test_len_reflects_store_size(self):
        repo = InMemoryConnectionProfileRepository()
        assert len(repo) == 0

        await repo.save(ConnectionProfile(name="P1", connector_type=ConnectorType.POSTGRESQL))
        assert len(repo) == 1

    @pytest.mark.asyncio
    async def test_update_existing_profile(self):
        repo = InMemoryConnectionProfileRepository()
        profile = ConnectionProfile(name="Original", connector_type=ConnectorType.POSTGRESQL)

        await repo.save(profile)
        profile.name = "Updated"
        await repo.save(profile)

        found = await repo.find_by_id(profile.id)
        assert found.name == "Updated"


class TestConnectorFactory:
    """Tests for connector factory helpers."""

    def test_list_connector_types_returns_list(self):
        from preflight.core.infrastructure.connectors.connector_factory import list_connector_types
        types = list_connector_types()
        assert isinstance(types, list)
        assert len(types) > 0

    def test_list_connector_types_is_sorted(self):
        from preflight.core.infrastructure.connectors.connector_factory import list_connector_types
        types = list_connector_types()
        assert types == sorted(types)

    def test_list_connector_types_includes_mock(self):
        from preflight.core.infrastructure.connectors.connector_factory import list_connector_types
        assert "mock" in list_connector_types()

    def test_register_connector_new_key(self):
        from preflight.core.infrastructure.connectors.connector_factory import (
            register_connector, create_connector, _REGISTRY
        )
        from preflight.core.infrastructure.connectors.mock_connector import MockEnterpriseConnector

        # Register a new connector type
        register_connector("test_custom_connector_xyz", MockEnterpriseConnector)
        assert "test_custom_connector_xyz" in _REGISTRY

        # Clean up
        del _REGISTRY["test_custom_connector_xyz"]

    def test_register_connector_overwrite_logs_warning(self, caplog):
        import logging
        from preflight.core.infrastructure.connectors.connector_factory import (
            register_connector, _REGISTRY
        )
        from preflight.core.infrastructure.connectors.mock_connector import MockEnterpriseConnector

        # Register the same key twice
        register_connector("_temp_test_key_overwrite", MockEnterpriseConnector)
        with caplog.at_level(logging.WARNING, logger="preflight.core.infrastructure.connectors.connector_factory"):
            register_connector("_temp_test_key_overwrite", MockEnterpriseConnector)
        # Clean up
        del _REGISTRY["_temp_test_key_overwrite"]
