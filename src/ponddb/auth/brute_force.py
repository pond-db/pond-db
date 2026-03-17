# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Per-IP failed-auth counter with lockout and TTL expiry."""

import time
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


@dataclass
class _IpState:
    count: int = 0
    locked_at: Optional[float] = None


class BruteForceGuard:
    """Tracks failed auth attempts per IP and locks out after threshold failures."""

    def __init__(
        self,
        lockout_threshold: int = 5,
        lockout_ttl_seconds: Optional[float] = None,
    ) -> None:
        self.lockout_threshold = lockout_threshold
        self._ttl = lockout_ttl_seconds
        self._state: dict[str, _IpState] = {}

    def _get(self, ip: str) -> _IpState:
        if ip not in self._state:
            self._state[ip] = _IpState()
        return self._state[ip]

    def _is_ttl_expired(self, state: _IpState) -> bool:
        if self._ttl is None or state.locked_at is None:
            return False
        return time.monotonic() - state.locked_at >= self._ttl

    def is_locked(self, ip: str) -> bool:
        state = self._get(ip)
        if state.count < self.lockout_threshold:
            return False
        if self._is_ttl_expired(state):
            # Reset expired lockout
            self._state[ip] = _IpState()
            return False
        return True

    def record_failure(self, ip: str) -> None:
        state = self._get(ip)
        if self._is_ttl_expired(state):
            # Reset and start fresh
            self._state[ip] = _IpState()
            state = self._state[ip]
        state.count += 1
        if state.count >= self.lockout_threshold and state.locked_at is None:
            state.locked_at = time.monotonic()

    def record_success(self, ip: str) -> None:
        self._state[ip] = _IpState()

    def get_failure_count(self, ip: str) -> int:
        state = self._get(ip)
        if self._is_ttl_expired(state):
            self._state[ip] = _IpState()
            return 0
        return state.count

    def check_or_raise(self, ip: str) -> None:
        if self.is_locked(ip):
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed attempts from {ip}. Try again later.",
            )


class BruteForceMiddleware(BaseHTTPMiddleware):
    """Middleware that blocks locked IPs with 429 before reaching route handlers."""

    def __init__(self, app: ASGIApp, guard: BruteForceGuard) -> None:
        super().__init__(app)
        self._guard = guard

    async def dispatch(self, request: Request, call_next: object) -> Response:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ip = forwarded_for.split(",")[0].strip()
        else:
            ip = getattr(request.client, "host", "unknown") if request.client else "unknown"

        if self._guard.is_locked(ip):
            from ponddb.security import audit_log
            from ponddb.security.audit_log import AuditLogMiddleware

            try:
                await audit_log.log_event(
                    AuditLogMiddleware._pool,
                    "brute_force_lockout",
                    ip_address=ip,
                    user_agent=request.headers.get("User-Agent"),
                    detail=f"IP {ip} is locked out due to too many failed attempts",
                )
            except Exception:
                pass
            return JSONResponse(
                status_code=429,
                content={"detail": f"Too many failed attempts from {ip}. Try again later."},
            )

        return await call_next(request)  # type: ignore[operator]
