# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""HMAC state token utilities for OAuth2 CSRF protection.

Tokens are URL-safe strings of the form:
    base64url(json_payload) . hmac_sha256_hex

The payload contains: provider, ts (Unix timestamp), nonce (random hex).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

DEFAULT_MAX_AGE_SECONDS = 600  # 10 minutes


def _get_secret() -> bytes:
    """Read POND_OAUTH_SECRET from env — raises if not configured."""
    secret = os.environ.get("POND_OAUTH_SECRET", "")
    if not secret:
        raise ValueError("POND_OAUTH_SECRET is not configured")
    return secret.encode()


def _b64_encode(data: bytes) -> str:
    """URL-safe base64 encode without padding."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64_decode(s: str) -> bytes:
    """URL-safe base64 decode with padding restored."""
    padding = "=" * (4 - len(s) % 4) if len(s) % 4 else ""
    return base64.urlsafe_b64decode(s + padding)


def _compute_sig(payload_b64: str, secret: bytes) -> str:
    """Return HMAC-SHA256 hex digest of the base64-encoded payload."""
    return hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()


def generate_state(
    provider: str,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> str:
    """Generate a signed, URL-safe HMAC state token for *provider*.

    The token embeds a timestamp and nonce so it is both expirable and
    replay-resistant.  ``max_age_seconds`` is stored in the payload so
    the same value must be passed to :func:`verify_state`.
    """
    secret = _get_secret()
    payload: dict[str, Any] = {
        "provider": provider,
        "ts": int(time.time()),
        "nonce": secrets.token_hex(16),
        "max_age": max_age_seconds,
    }
    payload_b64 = _b64_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = _compute_sig(payload_b64, secret)
    return f"{payload_b64}.{sig}"


def verify_state(
    token: str,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Verify *token* and return its payload dict.

    Raises:
        ValueError: if the token is missing, malformed, tampered, or expired.
    """
    if not token:
        raise ValueError("Empty state token")

    secret = _get_secret()

    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("Invalid state token format — expected payload.signature")

    payload_b64, sig = parts[0], parts[1]

    # Verify signature using constant-time comparison to prevent timing attacks
    expected_sig = _compute_sig(payload_b64, secret)
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("Invalid signature — token tampered or wrong secret")

    # Decode and parse payload
    try:
        payload: dict[str, Any] = json.loads(_b64_decode(payload_b64).decode())
    except Exception as exc:
        raise ValueError(f"Malformed state token payload: {exc}") from exc

    # Check expiry using the caller-supplied max_age_seconds
    ts: int = payload.get("ts", 0)
    age = int(time.time()) - ts
    if age > max_age_seconds:
        raise ValueError(f"Expired state token (age={age}s, max={max_age_seconds}s)")

    return payload
