"""Caching layer for Preflight diagnostics.

Provides a unified async interface over three backends, selected in priority
order:

1. Redis (if ``redis`` is installed and a valid URL is provided)
2. diskcache (if ``diskcache`` is installed) – persists between process
   restarts but stays local to the machine
3. In-memory dict – always available, lost when the process exits

All public methods are ``async`` so callers don't need to worry about which
backend is in use.
"""

import asyncio
import fnmatch
import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

try:
    import redis.asyncio as aioredis

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REDIS_AVAILABLE = False

try:
    import diskcache

    _DISKCACHE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DISKCACHE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


class _InMemoryBackend:
    """Simple dict-based backend.  No persistence, no eviction beyond TTL."""

    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}

    def _is_expired(self, key: str) -> bool:
        exp = self._expiry.get(key)
        return exp is not None and time.monotonic() > exp

    def get(self, key: str) -> Optional[str]:
        if self._is_expired(key):
            self._store.pop(key, None)
            self._expiry.pop(key, None)
            return None
        return self._store.get(key)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        self._store[key] = value
        if ttl:
            self._expiry[key] = time.monotonic() + ttl

    def delete_pattern(self, pattern: str) -> int:
        matched = [k for k in list(self._store) if fnmatch.fnmatch(k, pattern)]
        for k in matched:
            self._store.pop(k, None)
            self._expiry.pop(k, None)
        return len(matched)

    def clear(self) -> None:
        self._store.clear()
        self._expiry.clear()


class _DiskCacheBackend:
    """diskcache-based backend with TTL support."""

    def __init__(self, directory: str = "/tmp/preflight_cache"):
        self._cache = diskcache.Cache(directory)

    def get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        self._cache.set(key, value, expire=ttl)

    def delete_pattern(self, pattern: str) -> int:
        matched = [k for k in self._cache if fnmatch.fnmatch(str(k), pattern)]
        for k in matched:
            del self._cache[k]
        return len(matched)

    def clear(self) -> None:
        self._cache.clear()


# ---------------------------------------------------------------------------
# Public cache class
# ---------------------------------------------------------------------------

# Namespace prefixes
_NS_SCHEMA = "preflight:schema:"
_NS_JOB = "preflight:job:"
_NS_QUERY = "preflight:query:"


class DiagnosticCache:
    """Unified cache for schema data, query results, and job state.

    Automatically selects the best available backend.

    Args:
        redis_url: Optional Redis connection URL, e.g.
            ``"redis://localhost:6379/0"``.  If not provided or unavailable,
            falls back to diskcache or in-memory.
        diskcache_dir: Directory for diskcache storage.  Defaults to
            ``/tmp/preflight_cache``.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        diskcache_dir: str = "/tmp/preflight_cache",
    ):
        self._redis_client: Optional[Any] = None
        self._sync_backend: Optional[Any] = None
        self._backend_name: str = "memory"

        if redis_url and _REDIS_AVAILABLE:
            try:
                self._redis_client = aioredis.from_url(
                    redis_url, encoding="utf-8", decode_responses=True
                )
                self._backend_name = "redis"
                logger.info("DiagnosticCache: using Redis backend at %s", redis_url)
            except Exception as exc:
                logger.warning(
                    "DiagnosticCache: Redis initialisation failed (%s), falling back", exc
                )

        if self._redis_client is None:
            if _DISKCACHE_AVAILABLE:
                self._sync_backend = _DiskCacheBackend(diskcache_dir)
                self._backend_name = "diskcache"
                logger.info(
                    "DiagnosticCache: using diskcache backend at %s", diskcache_dir
                )
            else:
                self._sync_backend = _InMemoryBackend()
                self._backend_name = "memory"
                logger.info("DiagnosticCache: using in-memory backend")

    @property
    def backend_name(self) -> str:
        """Name of the active backend: ``'redis'``, ``'diskcache'``, or ``'memory'``."""
        return self._backend_name

    # ------------------------------------------------------------------
    # Low-level get/set
    # ------------------------------------------------------------------

    async def _get(self, key: str) -> Optional[str]:
        if self._redis_client is not None:
            try:
                return await self._redis_client.get(key)
            except Exception as exc:
                logger.warning("Cache GET error (redis): %s", exc)
                return None
        return await asyncio.get_event_loop().run_in_executor(
            None, self._sync_backend.get, key
        )

    async def _set(self, key: str, value: str, ttl: Optional[int]) -> None:
        if self._redis_client is not None:
            try:
                if ttl:
                    await self._redis_client.setex(key, ttl, value)
                else:
                    await self._redis_client.set(key, value)
            except Exception as exc:
                logger.warning("Cache SET error (redis): %s", exc)
            return
        await asyncio.get_event_loop().run_in_executor(
            None, self._sync_backend.set, key, value, ttl
        )

    async def _delete_pattern(self, pattern: str) -> int:
        if self._redis_client is not None:
            try:
                keys = await self._redis_client.keys(pattern)
                if keys:
                    return await self._redis_client.delete(*keys)
                return 0
            except Exception as exc:
                logger.warning("Cache DELETE_PATTERN error (redis): %s", exc)
                return 0
        return await asyncio.get_event_loop().run_in_executor(
            None, self._sync_backend.delete_pattern, pattern
        )

    # ------------------------------------------------------------------
    # Schema cache
    # ------------------------------------------------------------------

    async def set_schema(
        self, system_id: str, schema: dict, ttl: int = 3600
    ) -> None:
        """Cache schema metadata for a connected system.

        Args:
            system_id: Unique identifier for the system (e.g. connector_id).
            schema: Schema dict (must be JSON-serialisable).
            ttl: Time-to-live in seconds.  Defaults to 1 hour.
        """
        key = _NS_SCHEMA + system_id
        await self._set(key, json.dumps(schema, default=str), ttl)
        logger.debug("Cached schema for system_id=%s (ttl=%ds)", system_id, ttl)

    async def get_schema(self, system_id: str) -> Optional[dict]:
        """Retrieve cached schema metadata.

        Returns None on cache miss or deserialisation error.
        """
        key = _NS_SCHEMA + system_id
        raw = await self._get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to deserialise cached schema for %s: %s", system_id, exc)
            return None

    # ------------------------------------------------------------------
    # Job progress cache
    # ------------------------------------------------------------------

    async def set_job_progress(
        self, job_id: str, progress: dict, ttl: int = 86400
    ) -> None:
        """Store job progress state.

        Args:
            job_id: Unique diagnostic job identifier.
            progress: Progress dict (must be JSON-serialisable).
            ttl: Time-to-live in seconds.  Defaults to 24 hours.
        """
        key = _NS_JOB + job_id
        await self._set(key, json.dumps(progress, default=str), ttl)

    async def get_job_progress(self, job_id: str) -> Optional[dict]:
        """Retrieve job progress state.

        Returns None on cache miss.
        """
        key = _NS_JOB + job_id
        raw = await self._get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to deserialise job progress for %s: %s", job_id, exc)
            return None

    # ------------------------------------------------------------------
    # Query result cache
    # ------------------------------------------------------------------

    async def set_query_result(
        self, cache_key: str, result: dict, ttl: int = 1800
    ) -> None:
        """Cache the result of a read-only query.

        Args:
            cache_key: Arbitrary string key (typically a hash of query + params).
            result: Query result dict.
            ttl: Time-to-live in seconds.  Defaults to 30 minutes.
        """
        key = _NS_QUERY + cache_key
        await self._set(key, json.dumps(result, default=str), ttl)

    async def get_query_result(self, cache_key: str) -> Optional[dict]:
        """Retrieve a cached query result."""
        key = _NS_QUERY + cache_key
        raw = await self._get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    async def invalidate(self, pattern: str) -> int:
        """Delete all cache keys matching a glob *pattern*.

        Args:
            pattern: Glob pattern, e.g. ``"preflight:schema:*"`` or
                ``"preflight:job:run-123"`` or simply ``"*"`` to clear all.

        Returns:
            Number of keys deleted.
        """
        deleted = await self._delete_pattern(pattern)
        logger.info("Cache invalidate('%s'): %d key(s) deleted", pattern, deleted)
        return deleted

    async def invalidate_schema(self, system_id: Optional[str] = None) -> int:
        """Invalidate schema cache for a specific system or all systems."""
        pattern = _NS_SCHEMA + (system_id if system_id else "*")
        return await self.invalidate(pattern)

    async def invalidate_job(self, job_id: Optional[str] = None) -> int:
        """Invalidate job progress for a specific job or all jobs."""
        pattern = _NS_JOB + (job_id if job_id else "*")
        return await self.invalidate(pattern)

    async def clear_all(self) -> int:
        """Remove every Preflight-namespaced key from the cache."""
        return await self.invalidate("preflight:*")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release backend resources."""
        if self._redis_client is not None:
            try:
                await self._redis_client.close()
            except Exception as exc:
                logger.warning("Error closing Redis client: %s", exc)
        logger.info("DiagnosticCache closed")

    async def __aenter__(self) -> "DiagnosticCache":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
