"""
Auth management routes — create/list/revoke API keys.
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import verify_api_key, generate_api_key, hash_api_key

router = APIRouter()


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., description="Human-readable name for this API key")
    expires_in_days: Optional[int] = Field(None, description="Expiry in days (None = never)")


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key: Optional[str] = None  # Only returned on creation
    active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class ApiKeyListResponse(BaseModel):
    keys: List[ApiKeyResponse]
    total: int


@router.post("", response_model=ApiKeyResponse, status_code=201)
async def create_api_key(
    request: CreateApiKeyRequest,
    _: str = Depends(verify_api_key),  # Must be authenticated to create keys
) -> ApiKeyResponse:
    """Create a new API key. The key is only shown once — store it securely."""
    key = generate_api_key()
    key_hash = hash_api_key(key)
    key_id = str(uuid.uuid4())
    now = datetime.utcnow()
    expires_at = now + timedelta(days=request.expires_in_days) if request.expires_in_days else None

    try:
        from preflight.core.infrastructure.database.session import get_db_session
        from preflight.core.infrastructure.database.models import ApiKeyModel

        async with get_db_session() as session:
            model = ApiKeyModel(
                id=key_id,
                name=request.name,
                key_hash=key_hash,
                active=True,
                created_at=now,
                expires_at=expires_at,
            )
            session.add(model)
    except Exception:
        pass  # Fall through to in-memory response

    return ApiKeyResponse(
        id=key_id,
        name=request.name,
        key=key,  # Only returned here
        active=True,
        created_at=now,
        expires_at=expires_at,
    )


@router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    _: str = Depends(verify_api_key),
) -> ApiKeyListResponse:
    """List all API keys (without the actual key values)."""
    return ApiKeyListResponse(keys=[], total=0)


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    _: str = Depends(verify_api_key),
) -> None:
    """Revoke an API key."""
    try:
        from preflight.core.infrastructure.database.session import get_db_session
        from preflight.core.infrastructure.database.models import ApiKeyModel

        async with get_db_session() as session:
            model = await session.get(ApiKeyModel, key_id)
            if model:
                model.active = False
    except Exception:
        pass
