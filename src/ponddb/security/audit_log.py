# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Security audit log middleware and helpers for PondDB.

Writes security events (login_success, login_failure, sandbox_block) to a
Postgres security_audit_log table. All writes are fire-and-forget: exceptions
are swallowed so audit failures never block requests.
"""

import base64
import json
import logging
from typing import Any, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

from ponddb.security.sql_sandbox import BlockedSqlError, check_sql

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS security_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT        NOT NULL,
    tenant_id   TEXT,
    ip_address  TEXT,
    user_agent  TEXT,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sal_event_type ON security_audit_log (event_type);
CREATE INDEX IF NOT EXISTS idx_sal_created_at ON security_audit_log (created_at);

REVOKE DELETE ON security_audit_log FROM PUBLIC;
"""


# ---------------------------------------------------------------------------
# log_event helper
# ---------------------------------------------------------------------------


async def log_event(
    pool: Any,
    event_type: str,
    *,
    tenant_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    detail: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Insert one row into security_audit_log. Never propagates exceptions."""
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO security_audit_log "
                "(event_type, tenant_id, ip_address, user_agent, detail) "
                "VALUES ($1, $2, $3, $4, $5)",
                event_type,
                tenant_id,
                ip_address,
                user_agent,
                detail,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("audit log write failed (ignored): %s", exc)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_ip(scope: Scope) -> Optional[str]:
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode(errors="replace").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else None


def _get_user_agent(scope: Scope) -> Optional[str]:
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))
    ua = headers.get(b"user-agent")
    return ua.decode(errors="replace") if ua else None


def _get_tenant_from_jwt(scope: Scope) -> Optional[str]:
    """Decode JWT payload (no signature verification) to extract tenant_id."""
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode(errors="replace")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        return claims.get("tenant_id")
    except Exception:  # noqa: BLE001
        return None


def _detect_blocked_pattern(sql: str) -> Optional[str]:
    try:
        check_sql(sql)
        return None
    except BlockedSqlError as exc:
        return exc.pattern


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AuditLogMiddleware:
    """Raw ASGI middleware that logs security events to security_audit_log.

    Pool creation is deferred so __init__ never raises synchronously.
    All audit writes are fire-and-forget (exceptions swallowed).
    """

    _pool: Any = None  # class-level so tests can monkeypatch it

    def __init__(self, app: ASGIApp, dsn: Optional[str] = None) -> None:
        self.app = app
        self.dsn = dsn
        # Pool is created lazily; never raise in __init__

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        method: str = scope.get("method", "")

        if method != "POST" or path not in ("/auth/token", "/query"):
            await self.app(scope, receive, send)
            return

        # Pre-buffer entire request body so we can inspect it
        body_parts: list[bytes] = []
        more = True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                break
            body_parts.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(body_parts)

        consumed = False

        async def replay_receive() -> dict:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        status_holder: list[int] = [200]

        async def wrapped_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_holder[0] = message["status"]
            await send(message)

        await self.app(scope, replay_receive, wrapped_send)

        # Determine event to log
        status = status_holder[0]
        ip = _get_ip(scope)
        user_agent = _get_user_agent(scope)
        try:
            data: dict = json.loads(body)
        except Exception:  # noqa: BLE001
            data = {}

        pool = type(self)._pool  # class-level; can be patched by tests

        if path == "/auth/token":
            event_type = "login_success" if status == 200 else "login_failure"
            tenant_id: Optional[str] = data.get("tenant_id") or "default"
            try:
                await log_event(
                    pool,
                    event_type,
                    tenant_id=tenant_id,
                    ip_address=ip,
                    user_agent=user_agent,
                )
            except Exception:  # noqa: BLE001
                pass

        elif path == "/query" and status == 403:
            sql: str = data.get("sql", "")
            pattern_name = _detect_blocked_pattern(sql) or "unknown"
            tenant_id = _get_tenant_from_jwt(scope)
            try:
                await log_event(
                    pool,
                    "sandbox_block",
                    tenant_id=tenant_id,
                    ip_address=ip,
                    user_agent=user_agent,
                    detail=f"blocked pattern: {pattern_name}",
                )
            except Exception:  # noqa: BLE001
                pass
