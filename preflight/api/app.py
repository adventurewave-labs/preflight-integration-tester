from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

def create_app() -> FastAPI:
    app = FastAPI(
        title="Preflight Integration Tester API",
        description="Pre-purchase AI readiness diagnostic for enterprise systems",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # Include routers
    from .routes import health, connections, diagnostics, reports
    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(connections.router, prefix="/connections", tags=["connections"])
    app.include_router(diagnostics.router, prefix="/diagnostics", tags=["diagnostics"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])

    return app

app = create_app()
