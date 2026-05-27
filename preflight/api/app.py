"""
FastAPI application factory for Preflight Integration Tester API.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .middleware import RequestLoggingMiddleware, SecurityHeadersMiddleware, RateLimitMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    # Startup
    logger.info("Preflight API starting up")
    try:
        from preflight.core.infrastructure.database.session import init_db
        await init_db()
        logger.info("Database initialised")
    except Exception as exc:
        logger.warning(f"Database init skipped (running without DB): {exc}")

    yield

    # Shutdown
    logger.info("Preflight API shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Preflight Integration Tester API",
        description="Pre-purchase AI readiness diagnostic for enterprise systems",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ---------------------------------------------------------------------------
    # Middleware (applied in reverse order — last added runs first on request)
    # ---------------------------------------------------------------------------

    # CORS must be added before other middleware so preflight OPTIONS requests
    # are handled correctly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting — outermost so rejected requests don't hit inner layers
    app.add_middleware(RateLimitMiddleware)

    # Security headers on every response
    app.add_middleware(SecurityHeadersMiddleware)

    # Request logging with timing and request-ID injection
    app.add_middleware(RequestLoggingMiddleware)

    # ---------------------------------------------------------------------------
    # Exception handlers
    # ---------------------------------------------------------------------------

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"Path '{request.url.path}' not found"},
        )

    @app.exception_handler(500)
    async def internal_error_handler(request: Request, exc):
        logger.exception("Unhandled internal error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred. Please try again later."},
        )

    # ---------------------------------------------------------------------------
    # Routers
    # ---------------------------------------------------------------------------

    from .routes import health, connections, diagnostics, reports
    from .routes.auth import router as auth_router

    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(connections.router, prefix="/connections", tags=["connections"])
    app.include_router(diagnostics.router, prefix="/diagnostics", tags=["diagnostics"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])
    app.include_router(auth_router, prefix="/auth/keys", tags=["auth"])

    return app


app = create_app()
