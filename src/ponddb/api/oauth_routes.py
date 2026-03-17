# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""OAuth2 routes for Google and GitHub providers.

Routes:
    GET /auth/{provider}           — redirect to provider authorization URL
    GET /auth/{provider}/callback  — exchange code, issue PondDB JWTs
"""

import os
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ponddb.auth import oauth_state
from ponddb.auth.jwt_auth import create_access_token, create_refresh_token

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS: dict[str, dict[str, str]] = {
    "google": {
        "client_id_env": "POND_GOOGLE_CLIENT_ID",
        "client_secret_env": "POND_GOOGLE_CLIENT_SECRET",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "client_id_env": "POND_GITHUB_CLIENT_ID",
        "client_secret_env": "POND_GITHUB_CLIENT_SECRET",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scope": "user:email",
    },
}


# ---------------------------------------------------------------------------
# Internal helpers (exposed at module level so tests can patch them)
# ---------------------------------------------------------------------------


async def _exchange_code_for_token(
    provider: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """POST the authorization code to the provider's token endpoint."""
    cfg = SUPPORTED_PROVIDERS[provider]
    client_id = os.environ.get(cfg["client_id_env"], "")
    client_secret = os.environ.get(cfg["client_secret_env"], "")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            cfg["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()


async def _fetch_user_info(
    provider: str,
    access_token: str,
) -> dict[str, Any]:
    """Fetch the authenticated user's profile from the provider."""
    cfg = SUPPORTED_PROVIDERS[provider]
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            cfg["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()


def _extract_user_id(provider: str, user_info: dict[str, Any]) -> str:
    """Return a stable user ID string from the provider's user info."""
    if provider == "google":
        return str(user_info.get("sub", "unknown"))
    if provider == "github":
        return str(user_info.get("id", "unknown"))
    return str(user_info.get("sub") or user_info.get("id", "unknown"))


def _build_redirect_uri(request: Request, provider: str) -> str:
    base = os.environ.get("POND_BASE_URL", str(request.base_url).rstrip("/"))
    return f"{base}/auth/{provider}/callback"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_oauth_router(user_store=None) -> APIRouter:
    router = APIRouter()

    @router.get("/auth/{provider}")
    async def initiate_oauth(provider: str, request: Request) -> RedirectResponse:
        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown provider: {provider!r}. Supported: {list(SUPPORTED_PROVIDERS)}",
            )
        cfg = SUPPORTED_PROVIDERS[provider]
        client_id = os.environ.get(cfg["client_id_env"], "")
        if not client_id:
            raise HTTPException(
                status_code=500,
                detail=f"OAuth client_id not configured (env: {cfg['client_id_env']})",
            )
        state_token = oauth_state.generate_state(provider)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "scope": cfg["scope"],
            "state": state_token,
            "redirect_uri": _build_redirect_uri(request, provider),
        }
        url = cfg["authorize_url"] + "?" + urlencode(params)
        return RedirectResponse(url=url, status_code=302)

    @router.get("/auth/{provider}/callback")
    async def oauth_callback(
        provider: str,
        request: Request,
        code: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
        error_description: Optional[str] = None,
    ) -> dict[str, Any]:
        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown provider: {provider!r}. Supported: {list(SUPPORTED_PROVIDERS)}",
            )

        # OAuth error from provider
        if error:
            detail = f"{error}: {error_description}" if error_description else error
            raise HTTPException(status_code=400, detail=detail)

        if not code:
            raise HTTPException(status_code=400, detail="Missing authorization code")
        if not state:
            raise HTTPException(status_code=400, detail="Missing state parameter")

        # Verify HMAC state token
        try:
            state_data = oauth_state.verify_state(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid state: {exc}") from exc

        # Provider mismatch check
        if state_data.get("provider") != provider:
            raise HTTPException(
                status_code=400,
                detail=f"State provider mismatch: expected {state_data.get('provider')!r}, got {provider!r}",
            )

        # Exchange code for provider access token
        try:
            token_response = await _exchange_code_for_token(
                provider, code, _build_redirect_uri(request, provider)
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}") from exc

        provider_access_token = token_response.get("access_token", "")

        # Fetch user info from provider
        try:
            user_info = await _fetch_user_info(provider, provider_access_token)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"User info fetch failed: {exc}") from exc

        # Derive a stable tenant_id
        user_id = _extract_user_id(provider, user_info)
        tenant_id = f"{provider}:{user_id}"

        # Provision user record if a UserStore is wired in
        if user_store is not None:
            email = (user_info.get("email") or "").lower()
            display_name = (
                user_info.get("name") or user_info.get("login") or email
            )
            avatar_url = user_info.get("picture") or user_info.get("avatar_url")
            try:
                await user_store.upsert_user(
                    provider=provider,
                    provider_id=user_id,
                    email=email,
                    display_name=display_name,
                    tenant_id=tenant_id,
                    avatar_url=avatar_url,
                )
            except Exception:
                pass  # never block token issuance on DB errors

        # Issue PondDB tokens
        access_token = create_access_token(tenant_id)
        refresh_token = create_refresh_token(tenant_id)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    return router
