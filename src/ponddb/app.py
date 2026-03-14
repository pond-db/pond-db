"""FastAPI application — PondDB server entry point."""

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Response, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from ponddb import __version__
from ponddb.session_manager import QueryError, SessionManager

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
    expected = os.environ.get("POND_API_KEY", "")
    if not key or not key.strip() or key != expected or not expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


app = FastAPI(
    title="PondDB",
    version=__version__,
    description="Lightweight self-hosted DuckDB compute platform",
)

_manager = SessionManager()


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    text = _render_metrics(_manager.session_count)
    return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "sessions": _manager.session_count}


@app.head("/health", include_in_schema=False)
async def health_head() -> None:
    pass


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    namespace: str = "default"


@app.post("/session", status_code=201)
async def create_session(req: Optional[CreateSessionRequest] = None) -> dict:
    namespace = req.namespace if req is not None else "default"
    sid = _manager.create_session(namespace=namespace)
    info = _manager.get_session(sid)
    status = info["status"]
    return {"session_id": sid, "status": status.value if hasattr(status, "value") else status}


@app.delete("/session/{session_id}")
async def destroy_session(session_id: str) -> dict:
    try:
        _manager.destroy_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return {"detail": "destroyed"}


@app.get("/sessions")
async def list_sessions(namespace: Optional[str] = None) -> list[dict]:
    sessions = _manager.list_sessions(namespace=namespace)
    return [
        {
            "session_id": s["session_id"],
            "status": s["status"].value if hasattr(s["status"], "value") else s["status"],
            "namespace": s["namespace"],
            "created_at": s["created_at"],
            "last_active": s["last_active"],
        }
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Query endpoint
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    session_id: str
    sql: str
    format: str = "json"


@app.post("/query", dependencies=[Security(_require_api_key)])
async def execute_query(req: QueryRequest) -> dict:
    if req.format not in ("json",):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {req.format}")
    if not req.sql or not req.sql.strip():
        raise HTTPException(status_code=400, detail="SQL must not be empty")
    try:
        result = _manager.execute_query(req.session_id, req.sql)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _record_query_duration(result.elapsed_ms / 1000.0)
    return {
        "columns": result.columns,
        "rows": result.rows,
        "rowcount": result.rowcount,
        "elapsed_ms": result.elapsed_ms,
    }
