"""Tests for API authentication."""
import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from preflight.api.auth import (
    generate_api_key,
    hash_api_key,
    register_dev_key,
    verify_api_key,
    optional_auth,
)


class TestApiKeyGeneration:
    def test_key_format(self):
        key = generate_api_key()
        assert key.startswith("pfk_")
        assert len(key) > 20

    def test_keys_are_unique(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100  # All unique

    def test_hash_is_sha256(self):
        key = generate_api_key()
        hash_val = hash_api_key(key)
        assert len(hash_val) == 64  # SHA-256 hex is 64 chars
        assert all(c in "0123456789abcdef" for c in hash_val)

    def test_same_key_same_hash(self):
        key = generate_api_key()
        assert hash_api_key(key) == hash_api_key(key)

    def test_different_keys_different_hashes(self):
        key1 = generate_api_key()
        key2 = generate_api_key()
        assert hash_api_key(key1) != hash_api_key(key2)

    def test_key_prefix_is_pfk(self):
        for _ in range(10):
            key = generate_api_key()
            assert key.startswith("pfk_")

    def test_hash_deterministic_for_known_input(self):
        import hashlib
        known_key = "pfk_testkey123"
        expected = hashlib.sha256(known_key.encode()).hexdigest()
        assert hash_api_key(known_key) == expected

    def test_dev_mode_auth(self):
        """In dev mode, health endpoint should be accessible."""
        from fastapi.testclient import TestClient
        from preflight.api.app import create_app

        # Ensure dev mode
        old_val = os.environ.get("PREFLIGHT_DEV_MODE")
        os.environ["PREFLIGHT_DEV_MODE"] = "true"

        try:
            app = create_app()
            with TestClient(app) as client:
                response = client.get("/health")
                assert response.status_code == 200
        finally:
            if old_val is None:
                os.environ.pop("PREFLIGHT_DEV_MODE", None)
            else:
                os.environ["PREFLIGHT_DEV_MODE"] = old_val

    def test_auth_key_length_sufficient(self):
        """Generated keys should have enough entropy (at least 43 chars after prefix)."""
        key = generate_api_key()
        # pfk_ (4) + token_urlsafe(32) which is ~43 chars
        assert len(key) >= 40


# ---------------------------------------------------------------------------
# register_dev_key tests
# ---------------------------------------------------------------------------

class TestRegisterDevKey:
    def test_register_dev_key_adds_to_set(self):
        """register_dev_key adds a key to the in-memory dev keys set."""
        import preflight.api.auth as auth_module
        initial_count = len(auth_module._dev_keys)
        key = "pfk_testdevkey_unique_123"
        register_dev_key(key)
        assert key in auth_module._dev_keys
        # Cleanup
        auth_module._dev_keys.discard(key)

    def test_register_same_key_twice_is_idempotent(self):
        """Registering the same key twice doesn't duplicate it (it's a set)."""
        import preflight.api.auth as auth_module
        key = "pfk_dup_test_key_xyz"
        register_dev_key(key)
        register_dev_key(key)
        # Set should only contain it once
        assert list(auth_module._dev_keys).count(key) == 1
        auth_module._dev_keys.discard(key)

    def test_register_dev_key_accepts_string(self):
        """register_dev_key accepts any string."""
        import preflight.api.auth as auth_module
        key = "any_arbitrary_string"
        register_dev_key(key)
        assert key in auth_module._dev_keys
        auth_module._dev_keys.discard(key)


# ---------------------------------------------------------------------------
# verify_api_key async function tests (called directly, not via HTTP)
# ---------------------------------------------------------------------------

class TestVerifyApiKey:
    """Test verify_api_key as an async callable, patching _DEV_MODE."""

    @pytest.mark.asyncio
    async def test_dev_mode_returns_key_from_header(self):
        """In dev mode with a header key, that key is returned."""
        import preflight.api.auth as auth_module
        with patch.object(auth_module, "_DEV_MODE", True):
            result = await verify_api_key(
                api_key_header_value="my-test-key",
                bearer_credentials=None,
            )
        assert result == "my-test-key"

    @pytest.mark.asyncio
    async def test_dev_mode_returns_dev_key_when_no_header(self):
        """In dev mode with no key provided, returns 'dev-key' fallback."""
        import preflight.api.auth as auth_module
        with patch.object(auth_module, "_DEV_MODE", True):
            result = await verify_api_key(
                api_key_header_value=None,
                bearer_credentials=None,
            )
        assert result == "dev-key"

    @pytest.mark.asyncio
    async def test_dev_mode_returns_bearer_token(self):
        """In dev mode with bearer credentials, uses the bearer token."""
        import preflight.api.auth as auth_module
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bearer-test-key")
        with patch.object(auth_module, "_DEV_MODE", True):
            result = await verify_api_key(
                api_key_header_value=None,
                bearer_credentials=creds,
            )
        assert result == "bearer-test-key"

    @pytest.mark.asyncio
    async def test_non_dev_mode_raises_401_when_no_key(self):
        """In non-dev mode with no key, raises HTTP 401."""
        import preflight.api.auth as auth_module
        with patch.object(auth_module, "_DEV_MODE", False):
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(
                    api_key_header_value=None,
                    bearer_credentials=None,
                )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_non_dev_mode_with_registered_dev_key_succeeds(self):
        """A key in _dev_keys bypasses DB lookup even in non-dev mode."""
        import preflight.api.auth as auth_module
        test_key = "pfk_registered_dev_key_xyz"
        auth_module._dev_keys.add(test_key)
        try:
            with patch.object(auth_module, "_DEV_MODE", False):
                result = await verify_api_key(
                    api_key_header_value=test_key,
                    bearer_credentials=None,
                )
            assert result == test_key
        finally:
            auth_module._dev_keys.discard(test_key)

    @pytest.mark.asyncio
    async def test_non_dev_mode_db_unavailable_returns_key(self):
        """When DB is unavailable in non-dev mode, permissive fallback returns the key."""
        import preflight.api.auth as auth_module
        test_key = "pfk_fallback_test_key"
        with patch.object(auth_module, "_DEV_MODE", False):
            with patch(
                "preflight.core.infrastructure.database.session.get_db_session",
                side_effect=Exception("DB connection refused"),
            ):
                result = await verify_api_key(
                    api_key_header_value=test_key,
                    bearer_credentials=None,
                )
        assert result == test_key

    @pytest.mark.asyncio
    async def test_non_dev_mode_uses_bearer_when_no_header(self):
        """In non-dev mode with only a bearer token, the bearer token is used."""
        import preflight.api.auth as auth_module
        test_key = "pfk_bearer_key"
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=test_key)
        with patch.object(auth_module, "_DEV_MODE", False):
            with patch(
                "preflight.core.infrastructure.database.session.get_db_session",
                side_effect=Exception("No DB"),
            ):
                result = await verify_api_key(
                    api_key_header_value=None,
                    bearer_credentials=creds,
                )
        assert result == test_key


# ---------------------------------------------------------------------------
# optional_auth tests
# ---------------------------------------------------------------------------

class TestOptionalAuth:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_credentials(self):
        """With no key or bearer token, optional_auth returns None."""
        result = await optional_auth(
            api_key_header_value=None,
            bearer_credentials=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_key_when_header_provided_in_dev_mode(self):
        """In dev mode with header key, optional_auth returns the key."""
        import preflight.api.auth as auth_module
        with patch.object(auth_module, "_DEV_MODE", True):
            result = await optional_auth(
                api_key_header_value="test-opt-key",
                bearer_credentials=None,
            )
        assert result == "test-opt-key"

    @pytest.mark.asyncio
    async def test_returns_none_on_auth_failure(self):
        """When verify_api_key raises, optional_auth returns None instead of propagating."""
        import preflight.api.auth as auth_module
        with patch.object(auth_module, "_DEV_MODE", False):
            with patch(
                "preflight.api.auth.verify_api_key",
                side_effect=HTTPException(status_code=401, detail="Unauthorized"),
            ):
                result = await optional_auth(
                    api_key_header_value="bad-key",
                    bearer_credentials=None,
                )
        assert result is None


# ---------------------------------------------------------------------------
# Auth route endpoint tests (via TestClient with dev mode on)
# ---------------------------------------------------------------------------

class TestAuthRouteEndpoints:
    """Test /auth/keys endpoints — requires dev mode so auth is bypassed."""

    @pytest.fixture
    def client(self):
        """Create isolated test client with dev-mode auth."""
        from fastapi.testclient import TestClient
        from preflight.api.app import create_app
        from preflight.api import dependencies

        app = create_app()
        fresh_connections: dict = {}
        fresh_runs: dict = {}
        fresh_reports: dict = {}
        app.dependency_overrides[dependencies.get_connections_store] = lambda: fresh_connections
        app.dependency_overrides[dependencies.get_runs_store] = lambda: fresh_runs
        app.dependency_overrides[dependencies.get_reports_store] = lambda: fresh_reports

        return TestClient(app, raise_server_exceptions=True)

    def test_create_api_key_returns_201(self, client):
        """POST /auth/keys should return 201 with a key."""
        response = client.post(
            "/auth/keys",
            json={"name": "test-key"},
        )
        assert response.status_code == 201

    def test_create_api_key_returns_key_value(self, client):
        """Created key should include the actual key string (only shown once)."""
        response = client.post(
            "/auth/keys",
            json={"name": "my-integration-key"},
        )
        data = response.json()
        assert "key" in data
        assert data["key"].startswith("pfk_")

    def test_create_api_key_with_expiry(self, client):
        """POST /auth/keys with expires_in_days should set expiry."""
        response = client.post(
            "/auth/keys",
            json={"name": "expiring-key", "expires_in_days": 30},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["expires_at"] is not None

    def test_create_api_key_without_expiry(self, client):
        """POST /auth/keys without expires_in_days should have null expires_at."""
        response = client.post(
            "/auth/keys",
            json={"name": "permanent-key"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["expires_at"] is None

    def test_create_api_key_returns_id(self, client):
        """Created key should include an id field (UUID)."""
        response = client.post(
            "/auth/keys",
            json={"name": "key-with-id"},
        )
        data = response.json()
        assert "id" in data
        assert len(data["id"]) > 0

    def test_create_api_key_active_is_true(self, client):
        """Newly created key should have active=True."""
        response = client.post(
            "/auth/keys",
            json={"name": "active-key"},
        )
        data = response.json()
        assert data["active"] is True

    def test_create_api_key_returns_name(self, client):
        """Response should echo back the requested name."""
        response = client.post(
            "/auth/keys",
            json={"name": "my-special-key"},
        )
        data = response.json()
        assert data["name"] == "my-special-key"

    def test_list_api_keys_returns_200(self, client):
        """GET /auth/keys should return 200."""
        response = client.get("/auth/keys")
        assert response.status_code == 200

    def test_list_api_keys_returns_list_format(self, client):
        """GET /auth/keys response has 'keys' and 'total' fields."""
        response = client.get("/auth/keys")
        data = response.json()
        assert "keys" in data
        assert "total" in data
        assert isinstance(data["keys"], list)
        assert isinstance(data["total"], int)

    def test_revoke_api_key_returns_204(self, client):
        """DELETE /auth/keys/{id} should return 204."""
        # First create a key to get a valid ID
        create_resp = client.post(
            "/auth/keys",
            json={"name": "to-revoke"},
        )
        key_id = create_resp.json()["id"]

        delete_resp = client.delete(f"/auth/keys/{key_id}")
        assert delete_resp.status_code == 204

    def test_revoke_nonexistent_key_returns_204(self, client):
        """DELETE /auth/keys/{nonexistent-id} returns 204 (idempotent)."""
        response = client.delete("/auth/keys/00000000-0000-0000-0000-000000000000")
        # Implementation ignores missing keys gracefully
        assert response.status_code == 204

    def test_create_key_requires_name_field(self, client):
        """POST /auth/keys without name should return 422."""
        response = client.post(
            "/auth/keys",
            json={},
        )
        assert response.status_code == 422
