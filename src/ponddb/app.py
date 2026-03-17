"""FastAPI application — PondDB server entry point."""

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ponddb import __version__
from ponddb.cors_middleware import AllowlistCORSMiddleware
from ponddb.dataset_manager import DatasetManager
from ponddb.dataset_routes import make_dataset_router
from ponddb.jwt_auth import (
    _get_api_key,
    create_access_token,
    create_refresh_token,
    require_auth,
    verify_refresh_token,
)
from ponddb import token_blocklist
from ponddb.metadata_store import MetadataStore
from ponddb.namespace_routes import check_and_reserve_session_slot, make_namespace_workgroup_router
from ponddb.query_routes import make_query_router
from ponddb.result_cache import ResultCache
from ponddb.session_manager import QueryError, SessionManager, WorkgroupAccessError
from ponddb.sql_sandbox import BlockedSqlError
from ponddb.invite_routes import make_invite_router
from ponddb.invite_store import InviteStore
from ponddb.oauth_routes import make_oauth_router
from ponddb.share_routes import make_share_router
from ponddb.user_routes import make_user_router
from ponddb.user_store import UserStore
from ponddb.pondapi_execute import make_pondapi_execute_router
from ponddb.pondapi_htmx import make_pondapi_htmx_router
from ponddb.website_routes import make_website_router
from ponddb.admin_routes import make_admin_router
from ponddb.htmx_partials import make_htmx_router
from ponddb.security_headers import SecurityHeadersMiddleware
from ponddb.health_security import make_health_security_router

# ---------------------------------------------------------------------------
# Write-operation detection (invalidates cache)
# ---------------------------------------------------------------------------
_WRITE_RE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|TRUNCATE|MERGE)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Prometheus metrics state (reset on module reload)
# ---------------------------------------------------------------------------

_BUCKETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")]
_histogram_bucket_counts: list[int] = [0] * len(_BUCKETS)
_histogram_sum: float = 0.0
_histogram_count: int = 0
_compute_units_total: float = 0.0


def _record_query_duration(duration_s: float) -> None:
    global _histogram_sum, _histogram_count, _compute_units_total
    for i, bound in enumerate(_BUCKETS):
        if duration_s <= bound:
            _histogram_bucket_counts[i] += 1
    _histogram_sum += duration_s
    _histogram_count += 1
    _compute_units_total += duration_s * 1000.0  # ms as compute units


def _bucket_le(b: float) -> str:
    return "+Inf" if b == float("inf") else str(b)


def _render_metrics(session_count: int) -> str:
    lines: list[str] = []

    # sessions_active gauge
    lines.append("# HELP ponddb_sessions_active Number of active PondDB sessions")
    lines.append("# TYPE ponddb_sessions_active gauge")
    lines.append(f"ponddb_sessions_active {float(session_count)}")

    # query_duration_seconds histogram
    lines.append("# HELP ponddb_query_duration_seconds Query execution wall time in seconds")
    lines.append("# TYPE ponddb_query_duration_seconds histogram")
    cumulative = 0
    for i, bound in enumerate(_BUCKETS):
        cumulative += _histogram_bucket_counts[i]
        lines.append(
            f'ponddb_query_duration_seconds_bucket{{le="{_bucket_le(bound)}"}} {float(cumulative)}'
        )
    lines.append(f"ponddb_query_duration_seconds_sum {_histogram_sum}")
    lines.append(f"ponddb_query_duration_seconds_count {float(_histogram_count)}")

    # compute_units_total counter
    lines.append("# HELP ponddb_compute_units_total Total compute units consumed (query ms)")
    lines.append("# TYPE ponddb_compute_units_total counter")
    lines.append(f"ponddb_compute_units_total {_compute_units_total}")

    return "\n".join(lines) + "\n"

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: str | None = Security(_api_key_header)) -> None:
    """Dependency: validate X-API-Key against POND_API_KEY env var."""
    expected = _get_api_key()
    if not key or not key.strip() or key != expected or not expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


_logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start background tasks on startup, clean up on shutdown."""
    poll = float(os.environ.get("POND_WATCHDOG_INTERVAL", "30"))
    task = asyncio.create_task(_manager.start_watchdog(poll_interval=poll))
    _logger.info("Session watchdog started (poll=%.0fs)", poll)
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _logger.info("Session watchdog stopped")


app = FastAPI(
    title="PondDB",
    version=__version__,
    description="Lightweight self-hosted DuckDB compute platform",
    lifespan=_lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)

# Serve static assets (pond.css)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


# ---------------------------------------------------------------------------
# Auth endpoints: POST /auth/token and POST /auth/refresh
# ---------------------------------------------------------------------------


class TokenRequest(BaseModel):
    api_key: str
    tenant_id: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class RevokeRequest(BaseModel):
    token: str


@app.post("/auth/token", response_model=TokenResponse)
async def issue_token(req: TokenRequest, request: Request) -> dict[str, Any]:
    """Exchange an API key for JWT access + refresh tokens."""
    expected = _get_api_key()
    if not req.api_key or req.api_key != expected or not expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    tenant_id = req.tenant_id or "default"
    expiry = int(os.environ.get("POND_JWT_EXPIRY_SECONDS", "3600") or "3600")
    client_ip: Optional[str] = request.headers.get("X-Forwarded-For") or None
    user_agent: Optional[str] = request.headers.get("User-Agent") or None
    access = create_access_token(tenant_id)
    refresh = create_refresh_token(tenant_id, ip=client_ip, user_agent=user_agent)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": expiry,
    }


@app.post("/auth/refresh")
async def refresh_token(req: RefreshRequest, request: Request) -> dict[str, Any]:
    """Issue a new access token using a valid refresh token."""
    from ponddb import audit_log
    from ponddb.audit_log import AuditLogMiddleware

    client_ip: Optional[str] = request.headers.get("X-Forwarded-For") or None
    user_agent: Optional[str] = request.headers.get("User-Agent") or None

    try:
        claims = verify_refresh_token(req.refresh_token, ip=client_ip, user_agent=user_agent)
        tenant_id = claims.get("tenant_id", "default")
        expiry = int(os.environ.get("POND_JWT_EXPIRY_SECONDS", "3600") or "3600")
        new_access = create_access_token(tenant_id)
        try:
            await audit_log.log_event(
                AuditLogMiddleware._pool,
                "token_refresh",
                tenant_id=tenant_id,
                ip_address=client_ip,
                user_agent=user_agent,
                detail="success",
            )
        except Exception:
            pass
        return {
            "access_token": new_access,
            "token_type": "bearer",
            "expires_in": expiry,
        }
    except HTTPException as exc:
        try:
            await audit_log.log_event(
                AuditLogMiddleware._pool,
                "token_refresh",
                ip_address=client_ip,
                user_agent=user_agent,
                detail=f"failed: {exc.detail}",
            )
        except Exception:
            pass
        raise


@app.post("/auth/revoke")
async def revoke_token(req: RevokeRequest, request: Request) -> dict[str, Any]:
    """Revoke a token by adding its jti to the blocklist."""
    from jose import JWTError
    from jose import jwt as jose_jwt
    from ponddb import audit_log
    from ponddb.audit_log import AuditLogMiddleware

    secret = os.environ.get("POND_JWT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="POND_JWT_SECRET is not configured")

    # Decode without expiry verification so expired tokens can still be revoked
    try:
        claims = jose_jwt.decode(
            req.token,
            secret,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except JWTError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid token: {exc}") from exc

    jti = claims.get("jti")
    if not jti:
        raise HTTPException(status_code=400, detail="Token has no jti claim")

    token_blocklist.add_to_blocklist(jti)

    fwd = request.headers.get("X-Forwarded-For", "")
    client_ip: Optional[str] = fwd.split(",")[0].strip() if fwd else None
    user_agent: Optional[str] = request.headers.get("User-Agent") or None
    tenant_id: Optional[str] = claims.get("tenant_id") or claims.get("sub")
    try:
        await audit_log.log_event(
            AuditLogMiddleware._pool,
            "token_revoke",
            tenant_id=tenant_id,
            ip_address=client_ip,
            user_agent=user_agent,
            detail=f"jti:{jti}",
        )
    except Exception:
        pass

    return {"detail": "revoked", "jti": jti}

_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

_manager = SessionManager()

_sqlite_path = os.environ.get("POND_SQLITE_PATH", ":memory:")
_store = MetadataStore(_sqlite_path)
_store.initialize_blocking()
app.include_router(make_query_router(_store))
app.include_router(make_share_router(_store))

_user_store = UserStore(_sqlite_path)
_user_store.initialize_blocking()
app.include_router(make_oauth_router(_user_store))
app.include_router(make_user_router(_user_store))

_invite_store = InviteStore(_store)
app.include_router(make_invite_router(_invite_store))

_data_root = os.environ.get("POND_DATA_ROOT", "./data")
_dataset_manager = DatasetManager(_data_root)
_manager.dataset_manager = _dataset_manager  # auto-register datasets in new/resumed sessions
app.include_router(make_dataset_router(_dataset_manager))

# Shared state for workgroup quota tracking (passed into the namespace router)
_workgroups: dict = {}
_namespaces: dict = {}
_session_workgroups: dict[str, str] = {}  # session_id -> workgroup_id
app.include_router(make_namespace_workgroup_router(_workgroups, _session_workgroups, _namespaces))

_cache = ResultCache(ttl_seconds=300)
# Per-session dataset version for cache invalidation
_dataset_versions: dict[str, int] = {}

# PondAPI async execution router (uses the same SQLite connection as MetadataStore)
import sqlite3 as _sqlite3
_pondapi_db_conn = _sqlite3.connect(":memory:", check_same_thread=False)
_pondapi_db_conn.row_factory = _sqlite3.Row
app.include_router(make_pondapi_execute_router(_manager, _pondapi_db_conn))
app.include_router(make_pondapi_htmx_router(_manager, _pondapi_db_conn))
app.include_router(make_website_router(_manager, _workgroups, store=_store, dataset_manager=_dataset_manager))


def _get_usage_stats() -> dict:
    return {
        "active_sessions": _manager.session_count,
        "total_queries": _histogram_count,
        "compute_ms": _compute_units_total,
    }


app.include_router(make_admin_router(_invite_store, _workgroups, _namespaces, _get_usage_stats))
app.include_router(make_htmx_router(_manager, _workgroups, _pondapi_db_conn, store=_store))


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    text = _render_metrics(_manager.session_count)
    return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")


app.include_router(make_health_security_router())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "sessions": _manager.session_count}


@app.head("/health", include_in_schema=False)
async def health_head() -> None:
    pass


@app.get("/editor", response_class=HTMLResponse)
async def editor(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request, "editor.html")


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    namespace: str = "default"
    workgroup_id: Optional[str] = None


@app.post("/session", status_code=201)
async def create_session(
    req: Optional[CreateSessionRequest] = None,
) -> dict:
    namespace = req.namespace if req is not None else "default"
    workgroup_id = req.workgroup_id if req is not None else None

    # Workgroup quota enforcement
    if workgroup_id is not None:
        if workgroup_id not in _workgroups:
            raise HTTPException(status_code=404, detail=f"Workgroup not found: {workgroup_id}")
        wg = _workgroups[workgroup_id]
        try:
            check_and_reserve_session_slot(wg)
        except ValueError as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        wg["active_sessions"] = wg.get("active_sessions", 0) + 1

    effective_workgroup_id = workgroup_id if workgroup_id is not None else "default"
    sid = _manager.create_session(namespace=namespace, workgroup_id=effective_workgroup_id)

    if workgroup_id is not None:
        _session_workgroups[sid] = workgroup_id

    # Auto-register uploaded datasets as views in the new session
    session = _manager._sessions[sid]
    if session.conn is not None:
        _dataset_manager.register_in_session(session.conn)
    info = _manager.get_session(sid)
    status = info["status"]
    result: dict = {
        "session_id": sid,
        "status": status.value if hasattr(status, "value") else status,
        "workgroup_id": effective_workgroup_id,
    }
    return result


@app.delete("/session/{session_id}")
async def destroy_session(session_id: str) -> dict:
    try:
        _manager.destroy_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    # Decrement workgroup active_sessions if this session was in a workgroup
    if session_id in _session_workgroups:
        wg_id = _session_workgroups.pop(session_id)
        if wg_id in _workgroups:
            wg = _workgroups[wg_id]
            wg["active_sessions"] = max(0, wg.get("active_sessions", 0) - 1)
    return {"detail": "destroyed"}


@app.get("/sessions")
async def list_sessions(
    namespace: Optional[str] = None,
    workgroup_id: Optional[str] = None,
) -> list[dict]:
    sessions = _manager.list_sessions(namespace=namespace, workgroup_id=workgroup_id)
    return [
        {
            "session_id": s["session_id"],
            "status": s["status"].value if hasattr(s["status"], "value") else s["status"],
            "namespace": s["namespace"],
            "created_at": s["created_at"],
            "last_active": s["last_active"],
            "workgroup_id": s["workgroup_id"],
        }
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Query endpoint
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    session_id: str
    sql: str = Field(..., max_length=50_000)
    format: str = "json"


@app.post("/query")
async def execute_query(
    req: QueryRequest, response: Response, _auth: dict = Depends(require_auth)
) -> dict:
    if req.format not in ("json",):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {req.format}")
    if not req.sql or not req.sql.strip():
        raise HTTPException(status_code=400, detail="SQL must not be empty")

    # Resolve namespace for history logging and extract tenant_id for isolation
    tenant_id: str = _auth.get("tenant_id", "default")
    try:
        session_info = _manager.get_session(req.session_id)
        namespace = session_info["namespace"]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")

    # Workgroup isolation: enforce only when JWT carries a workgroup_id claim
    caller_wg: Optional[str] = _auth.get("workgroup_id")
    if caller_wg is not None:
        try:
            _manager.check_workgroup_access(req.session_id, caller_wg)
        except WorkgroupAccessError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    is_write = bool(_WRITE_RE.match(req.sql))

    # Bump dataset version on write operations, clearing old cache entries
    if is_write:
        _dataset_versions[req.session_id] = _dataset_versions.get(req.session_id, 0) + 1

    version = str(_dataset_versions.get(req.session_id, 0))
    # Include tenant_id in cache key to prevent cross-tenant cache leakage
    cache_key = _cache.make_key(req.sql, f"{tenant_id}:{version}")

    # Check cache (reads only)
    if not is_write:
        t0 = __import__("time").perf_counter()
        cached = _cache.get(cache_key)
        lookup_ms = (__import__("time").perf_counter() - t0) * 1000
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            return {**cached, "elapsed_ms": lookup_ms}

    # Execute query
    executed_at = datetime.now(timezone.utc)
    try:
        result = _manager.execute_query(req.session_id, req.sql)
    except BlockedSqlError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"Blocked SQL pattern '{exc.pattern}': not allowed",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")
    except QueryError as exc:
        # Log error to history
        try:
            await _store.log_query_history(
                namespace=namespace,
                tenant_id=tenant_id,
                sql=req.sql,
                duration_ms=0.0,
                rows_returned=0,
                status="error",
                error_message=str(exc),
                executed_at=executed_at,
            )
        except Exception:
            pass
        response.headers["X-Cache"] = "MISS"
        raise HTTPException(status_code=400, detail=str(exc))

    _record_query_duration(result.elapsed_ms / 1000.0)

    # Log success to history
    try:
        await _store.log_query_history(
            namespace=namespace,
            tenant_id=tenant_id,
            sql=req.sql,
            duration_ms=result.elapsed_ms,
            rows_returned=result.rowcount,
            status="success",
            executed_at=executed_at,
        )
    except Exception:
        pass

    payload = {
        "columns": result.columns,
        "rows": result.rows,
        "rowcount": result.rowcount,
        "elapsed_ms": result.elapsed_ms,
    }

    # Cache successful read queries
    if not is_write:
        _cache.set(cache_key, payload)

    response.headers["X-Cache"] = "MISS"
    return payload


# ---------------------------------------------------------------------------
# History endpoint
# ---------------------------------------------------------------------------

_VALID_STATUSES = {"success", "error"}


@app.get("/schema")
async def get_schema(
    _auth: dict = Depends(require_auth),
    session_id: str = Query(...),
) -> list[dict]:
    """Introspect DuckDB session schema — returns user tables/views with column metadata."""
    if session_id not in _manager._sessions:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    session = _manager._sessions[session_id]
    if session.conn is None:
        raise HTTPException(status_code=404, detail=f"Session not active: {session_id}")
    try:
        tables_result = session.conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        schema: list[dict] = []
        for (table_name,) in tables_result:
            cols_result = session.conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
                [table_name],
            ).fetchall()
            columns = [{"name": col_name, "type": data_type} for col_name, data_type in cols_result]
            schema.append({"table_name": table_name, "columns": columns})
        return schema
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history")
async def get_history(
    request: Request,
    _auth: dict = Depends(require_auth),
    status: Optional[str] = Query(default=None),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> Any:
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status!r}. Must be 'success' or 'error'")

    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None

    def _parse_ts(ts: str) -> datetime:
        # A bare "+" in a query string is decoded as a space; restore it
        ts = ts.replace(" ", "+").replace("Z", "+00:00")
        return datetime.fromisoformat(ts)

    try:
        if start is not None:
            start_dt = _parse_ts(start)
        if end is not None:
            end_dt = _parse_ts(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}")

    calling_tenant: str = _auth.get("tenant_id", "default")
    rows = await _store.get_query_history(
        tenant_id=calling_tenant,
        status_filter=status,
        start=start_dt,
        end=end_dt,
        limit=limit,
        offset=offset,
    )

    # Content negotiation: HTML page for browsers, JSON for API clients
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        from ponddb.website_routes import _get_session, _build_current_user
        session = _get_session(request)
        if not session:
            return RedirectResponse(url="/login", status_code=302)
        wg_list = list(_workgroups.values())
        return _templates.TemplateResponse(
            request, "history.html",
            {
                "history": rows,
                "status_filter": status or "",
                "limit": limit,
                "offset": offset,
                "current_user": _build_current_user(session),
                "active_page": "history",
                "workgroups_nav": wg_list,
            },
        )

    return rows


# ---------------------------------------------------------------------------
# build_app() — factory for fresh apps (used by tests, notably CORS tests)
# ---------------------------------------------------------------------------


def build_app() -> FastAPI:
    """Create a fresh FastAPI app with CORSMiddleware from POND_CORS_ORIGINS.

    Reads POND_CORS_ORIGINS at call time (comma-separated, whitespace stripped).
    Allowed origins are echoed in Access-Control-Allow-Origin; no wildcard is used.
    """
    cors_raw = os.environ.get("POND_CORS_ORIGINS", "")
    allow_origins: list[str] = (
        [o.strip() for o in cors_raw.split(",") if o.strip()]
        if cors_raw.strip()
        else []
    )

    new_app = FastAPI(
        title="PondDB",
        version=__version__,
        description="Lightweight self-hosted DuckDB compute platform",
    )

    if allow_origins:
        new_app.add_middleware(AllowlistCORSMiddleware, allow_origins=allow_origins)

    @new_app.get("/health")
    async def _health() -> dict:
        return {"status": "ok", "version": __version__, "sessions": 0}

    @new_app.post("/query")
    async def _query() -> dict:
        return {}

    return new_app
