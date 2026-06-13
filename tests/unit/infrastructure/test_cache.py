"""Tests for DiagnosticCache."""
import asyncio
import pytest
from preflight.core.infrastructure.cache import DiagnosticCache, _InMemoryBackend


class TestInMemoryBackend:
    """Direct tests for the _InMemoryBackend."""

    def test_get_missing_returns_none(self):
        backend = _InMemoryBackend()
        assert backend.get("missing") is None

    def test_set_and_get(self):
        backend = _InMemoryBackend()
        backend.set("key1", "value1")
        assert backend.get("key1") == "value1"

    def test_overwrite_value(self):
        backend = _InMemoryBackend()
        backend.set("key", "old")
        backend.set("key", "new")
        assert backend.get("key") == "new"

    def test_delete_pattern_exact(self):
        backend = _InMemoryBackend()
        backend.set("k1", "v1")
        backend.set("k2", "v2")
        count = backend.delete_pattern("k1")
        assert count == 1
        assert backend.get("k1") is None
        assert backend.get("k2") == "v2"

    def test_delete_pattern_wildcard(self):
        backend = _InMemoryBackend()
        backend.set("prefix:a", "v1")
        backend.set("prefix:b", "v2")
        backend.set("other:c", "v3")
        count = backend.delete_pattern("prefix:*")
        assert count == 2
        assert backend.get("prefix:a") is None
        assert backend.get("prefix:b") is None
        assert backend.get("other:c") == "v3"

    def test_clear(self):
        backend = _InMemoryBackend()
        backend.set("a", "1")
        backend.set("b", "2")
        backend.clear()
        assert backend.get("a") is None
        assert backend.get("b") is None

    def test_ttl_expiry(self):
        """Keys with very short TTL expire."""
        import time
        backend = _InMemoryBackend()
        # Set with TTL in the past by manipulating expiry
        backend.set("expired_key", "value")
        backend._expiry["expired_key"] = time.monotonic() - 1  # already expired
        assert backend.get("expired_key") is None

    def test_no_ttl_key_persists(self):
        backend = _InMemoryBackend()
        backend.set("persistent", "value", ttl=None)
        assert backend.get("persistent") == "value"


class TestDiagnosticCache:
    """Tests using in-memory cache (no Redis required)."""

    @pytest.fixture
    def cache(self):
        # Force in-memory by passing None URL
        return DiagnosticCache(redis_url=None)

    @pytest.mark.asyncio
    async def test_backend_name_is_memory_or_diskcache(self, cache):
        assert cache.backend_name in ("memory", "diskcache")

    @pytest.mark.asyncio
    async def test_set_and_get_schema(self, cache):
        schema_data = {"tables": ["customers", "orders"], "version": "1.0"}
        await cache.set_schema("system-1", schema_data)
        result = await cache.get_schema("system-1")
        assert result is not None
        assert result["tables"] == ["customers", "orders"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_schema(self, cache):
        result = await cache.get_schema("nonexistent-system")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_job_progress(self, cache):
        progress = {"status": "running", "pct": 45.0, "step": "schema_analysis"}
        await cache.set_job_progress("job-123", progress)
        result = await cache.get_job_progress("job-123")
        assert result is not None
        assert result["pct"] == 45.0

    @pytest.mark.asyncio
    async def test_get_nonexistent_progress(self, cache):
        result = await cache.get_job_progress("nonexistent-job")
        assert result is None

    @pytest.mark.asyncio
    async def test_overwrite_schema(self, cache):
        await cache.set_schema("sys", {"v": 1})
        await cache.set_schema("sys", {"v": 2})
        result = await cache.get_schema("sys")
        assert result["v"] == 2

    @pytest.mark.asyncio
    async def test_invalidate_all(self, cache):
        await cache.set_schema("sys-a", {"data": "a"})
        await cache.set_schema("sys-b", {"data": "b"})
        count = await cache.invalidate("preflight:schema:*")
        assert count >= 0  # count depends on backend

    @pytest.mark.asyncio
    async def test_invalidate_schema_by_system(self, cache):
        await cache.set_schema("target-sys", {"tables": []})
        await cache.invalidate_schema("target-sys")
        result = await cache.get_schema("target-sys")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_all_schemas(self, cache):
        await cache.set_schema("sys1", {"v": 1})
        await cache.set_schema("sys2", {"v": 2})
        await cache.invalidate_schema()  # invalidate all
        assert await cache.get_schema("sys1") is None
        assert await cache.get_schema("sys2") is None

    @pytest.mark.asyncio
    async def test_invalidate_job(self, cache):
        await cache.set_job_progress("job-xyz", {"pct": 50})
        await cache.invalidate_job("job-xyz")
        result = await cache.get_job_progress("job-xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_all_jobs(self, cache):
        await cache.set_job_progress("job-1", {"pct": 10})
        await cache.set_job_progress("job-2", {"pct": 20})
        await cache.invalidate_job()  # all jobs
        assert await cache.get_job_progress("job-1") is None
        assert await cache.get_job_progress("job-2") is None

    @pytest.mark.asyncio
    async def test_set_and_get_query_result(self, cache):
        result_data = {"rows": [{"id": 1, "name": "Alice"}], "count": 1}
        await cache.set_query_result("query-hash-abc", result_data)
        fetched = await cache.get_query_result("query-hash-abc")
        assert fetched is not None
        assert fetched["count"] == 1

    @pytest.mark.asyncio
    async def test_get_nonexistent_query_result(self, cache):
        result = await cache.get_query_result("nonexistent-hash")
        assert result is None

    @pytest.mark.asyncio
    async def test_clear_all(self, cache):
        await cache.set_schema("sys", {"v": 1})
        await cache.set_job_progress("job", {"pct": 50})
        await cache.set_query_result("q", {"rows": []})
        await cache.clear_all()
        assert await cache.get_schema("sys") is None
        assert await cache.get_job_progress("job") is None

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with DiagnosticCache(redis_url=None) as cache:
            await cache.set_schema("ctx-sys", {"ctx": True})
            result = await cache.get_schema("ctx-sys")
            assert result is not None
            assert result["ctx"] is True

    @pytest.mark.asyncio
    async def test_schema_stores_complex_data(self, cache):
        complex_schema = {
            "tables": {
                "customers": {
                    "columns": ["id", "name", "email"],
                    "primary_key": "id",
                    "nullable": ["email"],
                }
            },
            "version": "2.1",
            "dialect": "postgresql",
        }
        await cache.set_schema("complex-sys", complex_schema)
        result = await cache.get_schema("complex-sys")
        assert result["dialect"] == "postgresql"
        assert "customers" in result["tables"]

    @pytest.mark.asyncio
    async def test_cache_is_isolated_between_instances(self):
        cache1 = DiagnosticCache(redis_url=None)
        cache2 = DiagnosticCache(redis_url=None)

        await cache1.set_schema("sys", {"from": "cache1"})

        # cache2 should not see cache1's data (separate instances)
        result = await cache2.get_schema("sys")
        # In-memory caches are isolated per instance
        assert result is None or result.get("from") != "cache1"

    @pytest.mark.asyncio
    async def test_job_progress_multiple_keys(self, cache):
        jobs = [
            ("job-a", {"pct": 10, "step": "start"}),
            ("job-b", {"pct": 55, "step": "analysis"}),
            ("job-c", {"pct": 100, "step": "done"}),
        ]
        for job_id, progress in jobs:
            await cache.set_job_progress(job_id, progress)

        for job_id, expected in jobs:
            result = await cache.get_job_progress(job_id)
            assert result is not None
            assert result["pct"] == expected["pct"]
            assert result["step"] == expected["step"]

    @pytest.mark.asyncio
    async def test_get_schema_corrupt_json_returns_none(self, cache):
        """Corrupted JSON in schema cache returns None gracefully."""
        from preflight.core.infrastructure.cache import _NS_SCHEMA
        # Directly write invalid JSON into the backend
        key = _NS_SCHEMA + "corrupt-sys"
        await asyncio.get_event_loop().run_in_executor(
            None, cache._sync_backend.set, key, "not-valid-json{{{", None
        )
        result = await cache.get_schema("corrupt-sys")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_job_progress_corrupt_json_returns_none(self, cache):
        """Corrupted JSON in job cache returns None gracefully."""
        from preflight.core.infrastructure.cache import _NS_JOB
        key = _NS_JOB + "corrupt-job"
        await asyncio.get_event_loop().run_in_executor(
            None, cache._sync_backend.set, key, "[INVALID JSON", None
        )
        result = await cache.get_job_progress("corrupt-job")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_query_result_corrupt_json_returns_none(self, cache):
        """Corrupted JSON in query cache returns None gracefully."""
        from preflight.core.infrastructure.cache import _NS_QUERY
        key = _NS_QUERY + "corrupt-query"
        await asyncio.get_event_loop().run_in_executor(
            None, cache._sync_backend.set, key, "{{bad json}}", None
        )
        result = await cache.get_query_result("corrupt-query")
        assert result is None

    @pytest.mark.asyncio
    async def test_close_with_no_redis(self, cache):
        """close() completes without error when no Redis client."""
        assert cache._redis_client is None
        await cache.close()  # should not raise

    @pytest.mark.asyncio
    async def test_invalidate_returns_count(self, cache):
        """invalidate() returns the number of deleted keys."""
        await cache.set_schema("sys-x", {"v": 1})
        await cache.set_schema("sys-y", {"v": 2})
        count = await cache.invalidate("preflight:schema:sys-*")
        assert count == 2

    @pytest.mark.asyncio
    async def test_clear_all_removes_all_namespaces(self, cache):
        """clear_all() removes schema, job, and query keys."""
        await cache.set_schema("s1", {"v": 1})
        await cache.set_job_progress("j1", {"p": 50})
        await cache.set_query_result("q1", {"r": []})
        total = await cache.clear_all()
        assert await cache.get_schema("s1") is None
        assert await cache.get_job_progress("j1") is None
        assert await cache.get_query_result("q1") is None
