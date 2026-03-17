# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Share link routes — GET /q/{slug} for re-executing saved queries.

Public queries are accessible without auth.
Private queries require X-API-Key.
Rate limiting: 10 req/min per IP on public endpoints (token bucket).
"""

import time
from typing import Any, Optional

import duckdb
from fastapi import APIRouter, HTTPException, Request, Response, Security
from fastapi.security.api_key import APIKeyHeader

from ponddb.auth.jwt_auth import _get_api_key
from ponddb.store.metadata_store import MetadataStore
from ponddb.store.query_store import QueryNotFoundError

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ---------------------------------------------------------------------------
# Token bucket rate limiter (in-memory, per-IP)
# ---------------------------------------------------------------------------

_RATE_LIMIT = 10        # max requests per window
_RATE_WINDOW = 60.0     # window size in seconds

# {ip: [request_timestamps]}
_buckets: dict[str, list[float]] = {}


def reset_rate_limiter() -> None:
    """Clear all rate-limit buckets. Useful for test isolation."""
    _buckets.clear()


def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate limited."""
    now = time.monotonic()
    window_start = now - _RATE_WINDOW
    timestamps = _buckets.get(ip, [])
    # Evict old timestamps
    timestamps = [t for t in timestamps if t > window_start]
    if len(timestamps) >= _RATE_LIMIT:
        _buckets[ip] = timestamps
        return False
    timestamps.append(now)
    _buckets[ip] = timestamps
    return True


def _get_client_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For or fallback to client host."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _is_valid_key(key: Optional[str]) -> bool:
    """Check if the provided API key matches the configured key."""
    expected = _get_api_key()
    return bool(key and key.strip() and expected and key == expected)


def _execute_sql(sql: str) -> dict[str, Any]:
    """Execute SQL in a fresh in-memory DuckDB connection and return results."""
    start = time.perf_counter()
    conn = duckdb.connect(":memory:")
    try:
        result = conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
    finally:
        conn.close()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return {
        "columns": columns,
        "rows": [list(row) for row in rows],
        "rowcount": len(rows),
        "elapsed_ms": elapsed_ms,
    }


def make_share_router(store: MetadataStore) -> APIRouter:
    """Return a router with GET /q/{slug} endpoint."""
    reset_rate_limiter()  # Clean state for fresh router (important for test isolation)
    router = APIRouter()

    @router.get("/q/{slug}")
    async def get_share_link(
        slug: str,
        request: Request,
        key: Optional[str] = Security(_api_key_header),
    ) -> Response:
        # Look up the query (skip tenant check — share links handle auth separately)
        try:
            query = await store.get_query_by_slug(slug, enforce_tenant=False)
        except QueryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        visibility = query.get("visibility", "private")
        authed = _is_valid_key(key)

        if visibility == "private":
            # Private queries always require valid auth — no rate limiting
            if not authed:
                raise HTTPException(status_code=403, detail="API key required for private queries")
            result = _execute_sql(query["sql"])
            result["slug"] = slug
            return result

        # Public query — apply rate limiting
        ip = _get_client_ip(request)
        if not _check_rate_limit(ip):
            retry_after = int(_RATE_WINDOW)
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )

        result = _execute_sql(query["sql"])
        result["slug"] = slug
        return result

    return router
