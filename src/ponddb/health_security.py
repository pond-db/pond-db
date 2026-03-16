"""Security health check endpoint — GET /health/security.

Checks 8 security controls and returns JSON.
P0 controls: any False → 503.
P1 controls: False shown in response but does NOT trigger 503.
"""

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ponddb import sql_sandbox

# P0: any False → 503
P0_CONTROLS: set[str] = {
    "jwt_secret_configured",
    "sql_sandbox_enabled",
    "security_headers_enabled",
    "brute_force_protection_enabled",
    "rate_limiting_enabled",
    "audit_logging_enabled",
    "cors_configured",
}

# P1: False shown in response, but NOT a 503
P1_CONTROLS: set[str] = {
    "jwt_revocation_enabled",
}


def _check_jwt_secret() -> bool:
    """True when any JWT secret env var is configured."""
    return bool(
        os.environ.get("POND_JWT_SECRET")
        or os.environ.get("POND_JWT_SECRET_V1")
        or os.environ.get("POND_JWT_SECRET_V2")
        or os.environ.get("POND_JWT_SECRET_FILE")
    )


def _check_sql_sandbox() -> bool:
    """True when BLOCKED_PATTERNS list is non-empty."""
    return bool(sql_sandbox.BLOCKED_PATTERNS)


def _check_security_headers() -> bool:
    """True when SecurityHeadersMiddleware module is present."""
    try:
        from ponddb.security_headers import SecurityHeadersMiddleware  # noqa: F401
        return True
    except ImportError:
        return False


def _check_brute_force() -> bool:
    """True when BruteForceGuard module is present."""
    try:
        from ponddb.brute_force import BruteForceGuard  # noqa: F401
        return True
    except ImportError:
        return False


def _check_rate_limiting() -> bool:
    """True when RateLimiter module is present."""
    try:
        from ponddb.rate_limit import RateLimiter  # noqa: F401
        return True
    except ImportError:
        return False


def _check_audit_logging() -> bool:
    """True when AuditLogMiddleware module is present."""
    try:
        from ponddb.audit_log import AuditLogMiddleware  # noqa: F401
        return True
    except ImportError:
        return False


def _check_cors() -> bool:
    """True when AllowlistCORSMiddleware module is present."""
    try:
        from ponddb.cors_middleware import AllowlistCORSMiddleware  # noqa: F401
        return True
    except ImportError:
        return False


def _check_jwt_revocation() -> bool:
    """P1: True only when POND_REDIS_URL is configured (Redis available)."""
    return bool(os.environ.get("POND_REDIS_URL", ""))


def _evaluate_controls() -> dict[str, bool]:
    """Evaluate all 8 security controls and return their boolean status."""
    return {
        "jwt_secret_configured": _check_jwt_secret(),
        "sql_sandbox_enabled": _check_sql_sandbox(),
        "security_headers_enabled": _check_security_headers(),
        "brute_force_protection_enabled": _check_brute_force(),
        "rate_limiting_enabled": _check_rate_limiting(),
        "audit_logging_enabled": _check_audit_logging(),
        "cors_configured": _check_cors(),
        "jwt_revocation_enabled": _check_jwt_revocation(),
    }


def make_health_security_router() -> APIRouter:
    """Return a router with the GET /health/security endpoint."""
    router = APIRouter()

    @router.get("/health/security")
    async def health_security() -> JSONResponse:
        controls = _evaluate_controls()

        p0_failed = any(not controls[k] for k in P0_CONTROLS)
        status_str = "degraded" if p0_failed else "healthy"
        http_status = 503 if p0_failed else 200

        return JSONResponse(
            status_code=http_status,
            content={
                "status": status_str,
                "controls": controls,
                "p0_controls": sorted(P0_CONTROLS),
            },
        )

    return router
