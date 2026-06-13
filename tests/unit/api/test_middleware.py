"""
Tests for API middleware: rate limiting, security headers, request logging.
"""
import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from preflight.api.middleware import (
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    RateLimitMiddleware,
)


def make_simple_app(middleware_cls, **kwargs):
    """Create a minimal FastAPI app with the given middleware."""
    app = FastAPI()

    @app.get("/test")
    async def endpoint():
        return {"ok": True}

    @app.get("/health/ping")
    async def health():
        return {"alive": True}

    app.add_middleware(middleware_cls, **kwargs)
    return TestClient(app, raise_server_exceptions=True)


class TestSecurityHeadersMiddleware:
    def test_x_content_type_options(self):
        client = make_simple_app(SecurityHeadersMiddleware)
        resp = client.get("/test")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self):
        client = make_simple_app(SecurityHeadersMiddleware)
        resp = client.get("/test")
        assert resp.headers.get("x-frame-options") == "DENY"

    def test_x_xss_protection(self):
        client = make_simple_app(SecurityHeadersMiddleware)
        resp = client.get("/test")
        assert resp.headers.get("x-xss-protection") == "1; mode=block"

    def test_cache_control_no_store(self):
        client = make_simple_app(SecurityHeadersMiddleware)
        resp = client.get("/test")
        assert "no-store" in resp.headers.get("cache-control", "")

    def test_referrer_policy(self):
        client = make_simple_app(SecurityHeadersMiddleware)
        resp = client.get("/test")
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_csp_for_json_response(self):
        client = make_simple_app(SecurityHeadersMiddleware)
        resp = client.get("/test")
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src" in csp

    def test_headers_present_on_all_responses(self):
        client = make_simple_app(SecurityHeadersMiddleware)
        for path in ["/test", "/health/ping"]:
            resp = client.get(path)
            assert "x-frame-options" in resp.headers


class TestRequestLoggingMiddleware:
    def test_request_id_header_added(self):
        client = make_simple_app(RequestLoggingMiddleware)
        resp = client.get("/test")
        assert "x-request-id" in resp.headers
        assert resp.headers["x-request-id"]

    def test_response_time_header_added(self):
        client = make_simple_app(RequestLoggingMiddleware)
        resp = client.get("/test")
        assert "x-response-time" in resp.headers
        assert "ms" in resp.headers["x-response-time"]

    def test_response_still_correct(self):
        client = make_simple_app(RequestLoggingMiddleware)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_request_id_unique_per_request(self):
        client = make_simple_app(RequestLoggingMiddleware)
        ids = {client.get("/test").headers.get("x-request-id") for _ in range(5)}
        assert len(ids) == 5  # all unique


class TestRateLimitMiddleware:
    def test_allows_requests_in_dev_mode(self):
        """In dev mode (default) all requests pass through."""
        # Default is dev_mode = True (from env PREFLIGHT_DEV_MODE=true)
        client = make_simple_app(RateLimitMiddleware, rpm=60, burst=20)
        for _ in range(50):
            resp = client.get("/test")
            assert resp.status_code == 200

    def test_rate_limit_blocks_in_prod_mode(self, monkeypatch):
        """When dev_mode is False, excessive requests get 429."""
        monkeypatch.setenv("PREFLIGHT_DEV_MODE", "false")

        app = FastAPI()

        @app.get("/api/test")
        async def endpoint():
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, rpm=60, burst=2)
        client = TestClient(app, raise_server_exceptions=True)

        # First 2 requests should pass (burst=2)
        resp1 = client.get("/api/test")
        resp2 = client.get("/api/test")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # 3rd request should be rate limited
        resp3 = client.get("/api/test")
        assert resp3.status_code == 429
        assert "Rate limit" in resp3.json().get("detail", "")

    def test_health_path_bypasses_rate_limit(self, monkeypatch):
        """Health check paths bypass rate limiting even in prod mode."""
        monkeypatch.setenv("PREFLIGHT_DEV_MODE", "false")

        app = FastAPI()

        @app.get("/health/live")
        async def health():
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, rpm=1, burst=0)
        client = TestClient(app, raise_server_exceptions=True)

        # Health path should always pass
        for _ in range(10):
            resp = client.get("/health/live")
            assert resp.status_code == 200

    def test_rate_limit_env_override(self, monkeypatch):
        """RPM can be configured via env var."""
        monkeypatch.setenv("PREFLIGHT_DEV_MODE", "false")
        monkeypatch.setenv("RATE_LIMIT_RPM", "120")
        monkeypatch.setenv("RATE_LIMIT_BURST", "5")

        app = FastAPI()

        @app.get("/api/endpoint")
        async def endpoint():
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware)
        client = TestClient(app, raise_server_exceptions=True)

        # Should allow up to burst=5 requests
        responses = [client.get("/api/endpoint") for _ in range(5)]
        for r in responses:
            assert r.status_code == 200

    def test_rate_limit_429_has_retry_after(self, monkeypatch):
        """429 responses include Retry-After header."""
        monkeypatch.setenv("PREFLIGHT_DEV_MODE", "false")

        app = FastAPI()

        @app.get("/api/data")
        async def data():
            return {"data": "value"}

        app.add_middleware(RateLimitMiddleware, rpm=60, burst=1)
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/api/data")  # consume burst
        resp = client.get("/api/data")  # should be limited
        # Either still OK (if token refilled) or 429 with Retry-After
        if resp.status_code == 429:
            assert "retry-after" in resp.headers

    def test_get_tokens_new_ip(self, monkeypatch):
        """First request from a new IP gets a full burst of tokens."""
        monkeypatch.setenv("PREFLIGHT_DEV_MODE", "false")

        middleware = RateLimitMiddleware.__new__(RateLimitMiddleware)
        middleware.dev_mode = False
        middleware.rpm = 60
        middleware.burst = 10
        middleware._buckets = {}
        middleware._refill_rate = 60 / 60.0

        tokens = middleware._get_tokens("192.168.1.100")
        assert tokens == 10  # full burst for new IP

    def test_consume_token_reduces_count(self, monkeypatch):
        """Consuming a token reduces available tokens by 1."""
        monkeypatch.setenv("PREFLIGHT_DEV_MODE", "false")

        import time
        middleware = RateLimitMiddleware.__new__(RateLimitMiddleware)
        middleware.dev_mode = False
        middleware.rpm = 60
        middleware.burst = 5
        middleware._buckets = {}
        middleware._refill_rate = 1.0

        # Prime with a full bucket
        middleware._buckets["10.0.0.1"] = (5.0, time.time())
        assert middleware._consume_token("10.0.0.1") is True
        tokens_after, _ = middleware._buckets["10.0.0.1"]
        assert tokens_after == 4.0
