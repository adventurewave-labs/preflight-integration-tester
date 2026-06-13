FROM python:3.11-slim AS base

LABEL maintainer="support@preflight.ai"
LABEL description="Preflight Integration Tester - AI Deployment Readiness Diagnostic"
LABEL version="0.1.0"

# Security: run as non-root
RUN groupadd -r preflight && useradd -r -g preflight preflight

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build stage
FROM base AS builder
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM base AS production

# Copy dependencies from builder
COPY --from=builder /root/.local /home/preflight/.local
ENV PATH=/home/preflight/.local/bin:$PATH

# Copy application code
COPY --chown=preflight:preflight . .

# Install preflight package
RUN pip install --no-cache-dir -e . --quiet

# Create necessary directories
RUN mkdir -p /app/reports /app/logs /app/cache && \
    chown -R preflight:preflight /app

USER preflight

# Expose API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Default command: run API server
CMD ["uvicorn", "preflight.api.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
