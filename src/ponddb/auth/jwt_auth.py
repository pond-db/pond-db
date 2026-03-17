# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""JWT token utilities for PondDB authentication."""

import base64
import hashlib
import hmac as _hmac
import json
import os
import time
import uuid
from typing import Any

from fastapi import HTTPException, Request
from jose import JWTError
from jose import jwt as jose_jwt

DEFAULT_EXPIRY_SECONDS = 3600          # 1 hour
DEFAULT_REFRESH_EXPIRY_SECONDS = 30 * 24 * 3600  # 30 days


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COMMON_WEAK_SECRETS = {"password", "secret", "changeme", "letmein", "qwerty"}
_MIN_SECRET_LENGTH = 16


def validate_secret_strength(secret: str) -> None:
    """Raise ValueError if *secret* is too short or obviously weak."""
    if len(secret) < _MIN_SECRET_LENGTH:
        raise ValueError(
            f"JWT secret must be at least {_MIN_SECRET_LENGTH} characters long "
            f"(got {len(secret)})"
        )
    if secret.lower() in _COMMON_WEAK_SECRETS:
        raise ValueError("JWT secret is too common/predictable")


def _get_secret() -> str:
    """Read JWT secret with priority: file > V2 > V1 > base env var."""
    # 1. File-based secret
    secret_file = os.environ.get("POND_JWT_SECRET_FILE", "")
    if secret_file:
        try:
            contents = open(secret_file).read().strip()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Secret file not found or unreadable: {secret_file}",
            ) from exc
        if not contents:
            raise HTTPException(
                status_code=500,
                detail=f"Secret file is empty: {secret_file}",
            )
        return contents

    # 2. Versioned secrets (V2 primary, V1 fallback)
    v2 = os.environ.get("POND_JWT_SECRET_V2", "")
    if v2:
        return v2

    v1 = os.environ.get("POND_JWT_SECRET_V1", "")
    if v1:
        return v1

    # 3. Base env var
    secret = os.environ.get("POND_JWT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="POND_JWT_SECRET is not configured")
    return secret


def _get_api_key() -> str:
    """Read API key with priority: POND_API_KEY_FILE > POND_API_KEY."""
    key_file = os.environ.get("POND_API_KEY_FILE", "")
    if key_file:
        try:
            contents = open(key_file).read().strip()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"API key file not found or unreadable: {key_file}",
            ) from exc
        return contents
    return os.environ.get("POND_API_KEY", "")


def _get_session_secret() -> str:
    """Read session secret: POND_WEBSITE_SESSION_SECRET_FILE > POND_WEBSITE_SESSION_SECRET."""
    secret_file = os.environ.get("POND_WEBSITE_SESSION_SECRET_FILE", "")
    if secret_file:
        try:
            contents = open(secret_file).read().strip()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Session secret file not found or unreadable: {secret_file}",
            ) from exc
        return contents
    return os.environ.get("POND_WEBSITE_SESSION_SECRET", "change-me-default-secret")


def _get_all_secrets() -> list[str]:
    """Return all configured secrets in priority order (primary first) for fallback verification."""
    secrets: list[str] = []

    # File-based wins over everything
    secret_file = os.environ.get("POND_JWT_SECRET_FILE", "")
    if secret_file:
        try:
            contents = open(secret_file).read().strip()
            if contents:
                return [contents]
        except OSError:
            pass

    v2 = os.environ.get("POND_JWT_SECRET_V2", "")
    if v2:
        secrets.append(v2)

    v1 = os.environ.get("POND_JWT_SECRET_V1", "")
    if v1:
        secrets.append(v1)

    base = os.environ.get("POND_JWT_SECRET", "")
    if base and base not in secrets:
        secrets.append(base)

    return secrets


def validate_startup_secret() -> None:
    """Validate that the configured JWT secret is strong enough. Raises on failure."""
    secret_file = os.environ.get("POND_JWT_SECRET_FILE", "")
    if secret_file:
        # Delegate to _get_secret which raises 500 on file errors
        secret = _get_secret()
        validate_secret_strength(secret)
        return

    # Check versioned first
    v2 = os.environ.get("POND_JWT_SECRET_V2", "")
    v1 = os.environ.get("POND_JWT_SECRET_V1", "")
    base = os.environ.get("POND_JWT_SECRET", "")

    primary = v2 or v1 or base
    if not primary:
        raise RuntimeError("No JWT secret configured (POND_JWT_SECRET or POND_JWT_SECRET_V2)")
    validate_secret_strength(primary)


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
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + expiry,
    }
    if role is not None:
        payload["role"] = role
    return jose_jwt.encode(payload, secret, algorithm="HS256")


def compute_fingerprint(
    ip: str,
    user_agent: str,
    salt: str,
    include_ip: bool = True,
) -> str:
    """Return HMAC-SHA256 fingerprint of IP+UA (or UA only) keyed with salt."""
    if include_ip:
        message = (ip + "|" + user_agent).encode()
    else:
        message = user_agent.encode()
    return _hmac.new(salt.encode(), message, hashlib.sha256).hexdigest()


def _get_fingerprint_salt() -> str:
    return os.environ.get("POND_FINGERPRINT_SALT", "")


def _fingerprint_include_ip() -> bool:
    return os.environ.get("POND_FINGERPRINT_IP", "true").lower() not in ("false", "0", "no")


def create_refresh_token(
    tenant_id: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Return a signed HS256 refresh JWT for *tenant_id*.

    If both *ip* and *user_agent* are provided, an ``fp`` claim is added
    containing HMAC(ip + "|" + ua, POND_FINGERPRINT_SALT).
    """
    secret = _get_secret()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + DEFAULT_REFRESH_EXPIRY_SECONDS,
    }
    if ip is not None and user_agent is not None:
        salt = _get_fingerprint_salt()
        include_ip = _fingerprint_include_ip()
        payload["fp"] = compute_fingerprint(ip, user_agent, salt, include_ip=include_ip)
    return jose_jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token. Tries all configured secrets for fallback."""
    from ponddb.auth import token_blocklist

    secrets = _get_all_secrets()
    if not secrets:
        raise HTTPException(status_code=500, detail="POND_JWT_SECRET is not configured")

    last_exc: Exception | None = None
    for secret in secrets:
        try:
            claims = jose_jwt.decode(token, secret, algorithms=["HS256"])
            if claims.get("type") != "access":
                raise HTTPException(status_code=401, detail="Token is not an access token")

            # Blocklist check — fail open if storage is unavailable
            jti = claims.get("jti")
            if jti:
                try:
                    if token_blocklist.is_revoked(jti):
                        raise HTTPException(status_code=401, detail="Token has been revoked")
                except HTTPException:
                    raise
                except Exception as bl_exc:
                    token_blocklist.logger.warning(
                        "Blocklist check failed, failing open: %s", bl_exc
                    )

            return claims
        except HTTPException:
            raise
        except JWTError as exc:
            last_exc = exc
            continue

    raise HTTPException(status_code=401, detail=f"Invalid token: {last_exc}") from last_exc


def verify_refresh_token(
    token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Decode and validate a refresh token. Raises HTTPException(401) on failure.

    If the token has an ``fp`` claim, verifies it matches the fingerprint
    computed from *ip* and *user_agent*.  Tokens without an ``fp`` claim
    always pass (backward compatibility).
    """
    from ponddb.auth import token_blocklist

    secret = _get_secret()
    try:
        claims = jose_jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {exc}") from exc
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token is not a refresh token")

    # Blocklist check for refresh tokens — fail open on storage errors
    jti = claims.get("jti")
    if jti:
        try:
            if token_blocklist.is_revoked(jti):
                raise HTTPException(status_code=401, detail="Refresh token has been revoked")
        except HTTPException:
            raise
        except Exception:
            pass

    # Fingerprint verification — only when token carries fp claim
    stored_fp = claims.get("fp")
    if stored_fp is not None and ip is not None and user_agent is not None:
        salt = _get_fingerprint_salt()
        include_ip = _fingerprint_include_ip()
        expected_fp = compute_fingerprint(ip, user_agent, salt, include_ip=include_ip)
        if not _hmac.compare_digest(stored_fp, expected_fp):
            raise HTTPException(status_code=401, detail="Fingerprint mismatch")

    return claims


# ---------------------------------------------------------------------------
# Session cookie verification (shared with website_routes)
# ---------------------------------------------------------------------------

_COOKIE_NAME = "pond_session"


def _verify_session_cookie(cookie: str) -> dict | None:
    """Verify HMAC-signed session cookie. Returns payload dict or None."""
    try:
        payload_b64, sig = cookie.rsplit(".", 1)
        secret = _get_session_secret()
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

    try:
        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):]
            return verify_access_token(token)

        if api_key:
            expected = _get_api_key()
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
    except HTTPException as exc:
        if exc.status_code == 401:
            from ponddb.security import audit_log
            from ponddb.security.audit_log import AuditLogMiddleware

            fwd = request.headers.get("X-Forwarded-For", "")
            ip = fwd.split(",")[0].strip() if fwd else (
                request.client.host if request.client else None
            )
            ua = request.headers.get("User-Agent")
            try:
                await audit_log.log_event(
                    AuditLogMiddleware._pool,
                    "failed_auth",
                    ip_address=ip,
                    user_agent=ua,
                    detail=str(exc.detail),
                )
            except Exception:
                pass
        raise


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
