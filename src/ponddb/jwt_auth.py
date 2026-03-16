"""JWT token utilities for PondDB authentication."""

import base64
import hashlib
import hmac as _hmac
import json
import os
import time
from typing import Any

from fastapi import HTTPException, Request
from jose import JWTError
from jose import jwt as jose_jwt

DEFAULT_EXPIRY_SECONDS = 3600          # 1 hour
DEFAULT_REFRESH_EXPIRY_SECONDS = 30 * 24 * 3600  # 30 days


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_secret() -> str:
    """Read JWT secret from env — raises 500 if not configured."""
    secret = os.environ.get("POND_JWT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="POND_JWT_SECRET is not configured")
    return secret


def _get_expiry_seconds() -> int:
    """Read POND_JWT_EXPIRY_SECONDS; falls back to 3600."""
    val = os.environ.get("POND_JWT_EXPIRY_SECONDS", "")
    try:
        return int(val) if val else DEFAULT_EXPIRY_SECONDS
    except ValueError:
        return DEFAULT_EXPIRY_SECONDS


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


def create_access_token(
    tenant_id: str,
    scopes: list[str] | None = None,
    role: str | None = None,
) -> str:
    """Return a signed HS256 access JWT for *tenant_id*."""
    secret = _get_secret()
    expiry = _get_expiry_seconds()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "scopes": scopes or ["query", "read", "write"],
        "type": "access",
        "iat": now,
        "exp": now + expiry,
    }
    if role is not None:
        payload["role"] = role
    return jose_jwt.encode(payload, secret, algorithm="HS256")


def create_refresh_token(tenant_id: str) -> str:
    """Return a signed HS256 refresh JWT for *tenant_id*."""
    secret = _get_secret()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "type": "refresh",
        "iat": now,
        "exp": now + DEFAULT_REFRESH_EXPIRY_SECONDS,
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token. Raises HTTPException(401) on failure."""
    secret = _get_secret()
    try:
        claims = jose_jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc
    if claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="Token is not an access token")
    return claims


def verify_refresh_token(token: str) -> dict[str, Any]:
    """Decode and validate a refresh token. Raises HTTPException(401) on failure."""
    secret = _get_secret()
    try:
        claims = jose_jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {exc}") from exc
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token is not a refresh token")
    return claims


# ---------------------------------------------------------------------------
# Session cookie verification (shared with website_routes)
# ---------------------------------------------------------------------------

_COOKIE_NAME = "pond_session"


def _verify_session_cookie(cookie: str) -> dict | None:
    """Verify HMAC-signed session cookie. Returns payload dict or None."""
    try:
        payload_b64, sig = cookie.rsplit(".", 1)
        secret = os.environ.get("POND_WEBSITE_SESSION_SECRET", "change-me-default-secret")
        expected = _hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# FastAPI dependency — accepts Bearer JWT, X-API-Key, or session cookie
# ---------------------------------------------------------------------------


async def require_auth(request: Request) -> dict[str, Any]:
    """Dependency that accepts a Bearer JWT, X-API-Key header, or session cookie."""
    authorization = request.headers.get("Authorization", "")
    api_key = request.headers.get("X-API-Key", "")

    if authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        return verify_access_token(token)

    if api_key:
        expected = os.environ.get("POND_API_KEY", "")
        if expected and api_key == expected:
            # Return a minimal claims dict for API-key callers
            return {"tenant_id": "default", "scopes": ["query", "read", "write"], "type": "access"}
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Fall back to website session cookie
    cookie = request.cookies.get(_COOKIE_NAME)
    if cookie:
        session = _verify_session_cookie(cookie)
        if session:
            return {
                "tenant_id": session.get("tenant_id", "default"),
                "scopes": ["query", "read", "write"],
                "type": "access",
            }

    raise HTTPException(status_code=401, detail="Authentication required")


async def require_admin(request: Request) -> dict[str, Any]:
    """Dependency that requires a Bearer JWT with role=admin.

    - No auth → 401
    - API key only → 403
    - Valid JWT without role=admin → 403
    - Valid JWT with role=admin → returns claims
    """
    authorization = request.headers.get("Authorization", "")
    api_key = request.headers.get("X-API-Key", "")

    if not authorization.startswith("Bearer "):
        if api_key:
            # API key is authenticated but not admin
            raise HTTPException(status_code=403, detail="Admin role required")
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization[len("Bearer "):]
    claims = verify_access_token(token)
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return claims
