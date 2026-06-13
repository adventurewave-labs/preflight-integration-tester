"""
FastAPI middleware for Preflight API.

Includes:
- Request logging with timing
- Rate limiting (in-memory, token bucket)
- Security headers
- Request ID injection
"""
import time
import uuid
import logging
import os
from typing import Dict, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests with timing."""

    async def dispatch(self, request: Request, call_next):
        # Generate request ID
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        start = time.perf_counter()

        # Process request
        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            f"[{request_id}] {request.method} {request.url.path} "
            f"→ {response.status_code} ({elapsed_ms:.1f}ms)"
        )

        # Add headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"

        # CSP for API responses
        if "text/html" not in response.headers.get("content-type", ""):
            response.headers["Content-Security-Policy"] = "default-src 'none'"

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Token bucket rate limiter.

    Default: 60 requests/minute per IP, burst of 20.
    Configurable via env: RATE_LIMIT_RPM, RATE_LIMIT_BURST.
    Disabled in dev mode.
    """

    def __init__(self, app, rpm: int = 60, burst: int = 20):
        super().__init__(app)
        self.rpm = int(os.environ.get("RATE_LIMIT_RPM", rpm))
        self.burst = int(os.environ.get("RATE_LIMIT_BURST", burst))
        self.dev_mode = os.environ.get("PREFLIGHT_DEV_MODE", "true").lower() == "true"
        self._buckets: Dict[str, Tuple[float, float]] = {}  # ip → (tokens, last_refill)
        self._refill_rate = self.rpm / 60.0  # tokens per second

    def _get_tokens(self, ip: str) -> float:
        now = time.time()
        if ip not in self._buckets:
            self._buckets[ip] = (float(self.burst), now)
            return self.burst

        tokens, last_refill = self._buckets[ip]
        elapsed = now - last_refill
        tokens = min(self.burst, tokens + elapsed * self._refill_rate)
        self._buckets[ip] = (tokens, now)
        return tokens

    def _consume_token(self, ip: str) -> bool:
        tokens = self._get_tokens(ip)
        if tokens >= 1:
            t, ts = self._buckets[ip]
            self._buckets[ip] = (t - 1, ts)
            return True
        return False

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting in dev mode or for health checks
        if self.dev_mode or request.url.path.startswith("/health"):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"

        if not self._consume_token(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please wait before retrying."},
                headers={"Retry-After": "60"},
            )

        return await call_next(request)
