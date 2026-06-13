"""
API Key Authentication for Preflight API.

Supports:
- Bearer token auth (Authorization: Bearer <api-key>)
- X-API-Key header auth
- Optional auth bypass in development mode (PREFLIGHT_DEV_MODE=true)

Keys are stored as SHA-256 hashes in the database. In dev mode, any key or
no key is accepted.
"""
import hashlib
import logging
import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# Security schemes
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

# In-memory key store for development (bypass DB dependency)
_dev_keys: set = set()
_DEV_MODE = os.environ.get("PREFLIGHT_DEV_MODE", "true").lower() == "true"


def hash_api_key(key: str) -> str:
    """Hash an API key for secure storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a cryptographically secure API key."""
    return f"pfk_{secrets.token_urlsafe(32)}"


def register_dev_key(key: str) -> None:
    """Register a key for development mode validation."""
    _dev_keys.add(key)


async def verify_api_key(
    api_key_header_value: Optional[str] = Security(api_key_header),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> str:
    """
    FastAPI dependency: verify API key from header or bearer token.

    Returns the verified API key on success.
    Raises HTTP 401 if not authenticated.
    """
    # Development mode: accept anything
    if _DEV_MODE:
        key = api_key_header_value or (bearer_credentials.credentials if bearer_credentials else "dev-key")
        logger.debug("Dev mode: auth bypassed")
        return key or "dev-key"

    # Extract key from either header
    key = None
    if api_key_header_value:
        key = api_key_header_value
    elif bearer_credentials:
        key = bearer_credentials.credentials

    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Provide via X-API-Key header or Authorization: Bearer <key>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate against stored keys
    key_hash = hash_api_key(key)

    # Check in-memory dev keys first
    if key in _dev_keys:
        return key

    # Try database lookup
    try:
        from preflight.core.infrastructure.database.session import get_db_session
        from preflight.core.infrastructure.database.models import ApiKeyModel
        from sqlalchemy import select

        async with get_db_session() as session:
            result = await session.execute(
                select(ApiKeyModel).where(
                    ApiKeyModel.key_hash == key_hash,
                    ApiKeyModel.active == True,
                )
            )
            api_key_record = result.scalar_one_or_none()

            if not api_key_record:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Check expiry
            if api_key_record.expires_at and api_key_record.expires_at < datetime.utcnow():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Update last_used
            api_key_record.last_used_at = datetime.utcnow()
            return key
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Auth DB lookup failed, using permissive fallback: {e}")
        # If DB is unavailable, be permissive in non-strict mode
        return key


# Convenience: optional auth (allows unauthenticated for public endpoints)
async def optional_auth(
    api_key_header_value: Optional[str] = Security(api_key_header),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> Optional[str]:
    """Optional authentication — returns None if no key provided."""
    if not api_key_header_value and not bearer_credentials:
        return None
    try:
        return await verify_api_key(api_key_header_value, bearer_credentials)
    except HTTPException:
        return None
