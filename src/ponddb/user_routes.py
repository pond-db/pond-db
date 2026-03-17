"""User management routes — /users/me and /users/me/api-keys."""

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ponddb.jwt_auth import _get_api_key, verify_access_token
from ponddb.user_store import UserStore


def make_user_router(user_store: UserStore) -> APIRouter:
    router = APIRouter()

    async def _require_user_auth(request: Request) -> dict[str, Any]:
        """Accept Bearer JWT or user-created API key (hashed lookup in UserStore)."""
        authorization = request.headers.get("Authorization", "")
        api_key_header = request.headers.get("X-API-Key", "")

        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):]
            return verify_access_token(token)  # raises 401 on invalid

        if api_key_header:
            # Try user-generated API key via hash lookup
            claims = await user_store.verify_api_key(api_key_header)
            if claims is not None:
                return {
                    "tenant_id": claims["tenant_id"],
                    "user_id": claims["user_id"],
                    "type": "access",
                    "scopes": ["query", "read"],
                }
            # Fall back to static POND_API_KEY
            expected = _get_api_key()
            if expected and api_key_header == expected:
                return {"tenant_id": "default", "scopes": ["query", "read", "write"], "type": "access"}
            raise HTTPException(status_code=401, detail="Invalid API key")

        raise HTTPException(status_code=401, detail="Authentication required")

    async def _get_user_from_auth(auth: dict[str, Any]) -> dict[str, Any]:
        tenant_id: str = auth.get("tenant_id", "")
        user = await user_store.get_user_by_tenant_id(tenant_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    # ------------------------------------------------------------------
    # GET /users/me
    # ------------------------------------------------------------------

    @router.get("/users/me")
    async def get_me(auth: dict = Depends(_require_user_auth)) -> dict[str, Any]:
        user = await _get_user_from_auth(auth)
        return {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "role": user["role"],
            "avatar_url": user.get("avatar_url"),
            "created_at": user["created_at"],
            "last_login_at": user.get("last_login_at"),
        }

    # ------------------------------------------------------------------
    # POST /users/me/api-keys
    # ------------------------------------------------------------------

    class CreateApiKeyRequest(BaseModel):
        name: str
        scopes: Optional[list[str]] = None
        expires_at: Optional[str] = None

    @router.post("/users/me/api-keys", status_code=201)
    async def create_api_key(
        req: CreateApiKeyRequest,
        auth: dict = Depends(_require_user_auth),
    ) -> dict[str, Any]:
        user = await _get_user_from_auth(auth)
        return await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name=req.name,
            expires_at=req.expires_at,
        )

    # ------------------------------------------------------------------
    # GET /users/me/api-keys
    # ------------------------------------------------------------------

    @router.get("/users/me/api-keys")
    async def list_api_keys(auth: dict = Depends(_require_user_auth)) -> list[dict[str, Any]]:
        user = await _get_user_from_auth(auth)
        return await user_store.list_api_keys(user_id=user["id"])

    # ------------------------------------------------------------------
    # DELETE /users/me/api-keys/{key_id}
    # ------------------------------------------------------------------

    @router.delete("/users/me/api-keys/{key_id}")
    async def revoke_api_key(
        key_id: str,
        auth: dict = Depends(_require_user_auth),
    ) -> dict[str, Any]:
        user = await _get_user_from_auth(auth)
        try:
            await user_store.revoke_api_key(key_id=key_id, user_id=user["id"])
        except ValueError:
            raise HTTPException(status_code=404, detail="API key not found")
        return {"detail": "revoked"}

    return router
