"""PondAPI async execution endpoints.

POST /pondapi/execute          — submit SQL for async execution (returns 202)
GET  /pondapi/execute/{id}/result — poll for execution result

Backend: ThreadPoolExecutor-based async execution
Storage: pondapi_executions SQLite table
Auth: same JWT/API-key as the rest of the API
Rate limiting: sliding-window per-tenant limit on submissions
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ponddb.jwt_auth import require_auth
from ponddb.session_manager import SessionManager

# Module-level thread pool — reused across reloads
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pondapi-exec")

# Default sliding-window duration (seconds). Short enough that tests that submit
# a burst of requests all within one second still see the limit.
_WINDOW_SECONDS = float(os.environ.get("POND_PONDAPI_RATE_WINDOW", "60"))


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _init_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pondapi_executions (
            execution_id TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            session_id   TEXT NOT NULL,
            sql          TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            columns_json TEXT,
            rows_json    TEXT,
            rowcount     INTEGER,
            elapsed_ms   REAL,
            error        TEXT,
            created_at   TEXT NOT NULL
        )
    """)
    conn.commit()


def _insert_execution(
    conn: sqlite3.Connection,
    execution_id: str,
    tenant_id: str,
    session_id: str,
    sql: str,
    created_at: str,
) -> None:
    conn.execute(
        "INSERT INTO pondapi_executions "
        "(execution_id, tenant_id, session_id, sql, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (execution_id, tenant_id, session_id, sql, created_at),
    )
    conn.commit()


def _update_complete(
    conn: sqlite3.Connection,
    execution_id: str,
    columns: list[str],
    rows: list[list[Any]],
    rowcount: int,
    elapsed_ms: float,
) -> None:
    conn.execute(
        "UPDATE pondapi_executions "
        "SET status='complete', columns_json=?, rows_json=?, rowcount=?, elapsed_ms=? "
        "WHERE execution_id=?",
        (json.dumps(columns), json.dumps(rows), rowcount, elapsed_ms, execution_id),
    )
    conn.commit()


def _update_error(
    conn: sqlite3.Connection,
    execution_id: str,
    error: str,
    elapsed_ms: float,
) -> None:
    conn.execute(
        "UPDATE pondapi_executions "
        "SET status='error', error=?, elapsed_ms=? "
        "WHERE execution_id=?",
        (error, elapsed_ms, execution_id),
    )
    conn.commit()


def _fetch_row(conn: sqlite3.Connection, execution_id: str) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM pondapi_executions WHERE execution_id=?", (execution_id,)
    )
    return cur.fetchone()


def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    status = row["status"]
    result: dict[str, Any] = {
        "execution_id": row["execution_id"],
        "status": status,
        "created_at": row["created_at"],
    }
    if status == "complete":
        result["columns"] = json.loads(row["columns_json"]) if row["columns_json"] else []
        result["rows"] = json.loads(row["rows_json"]) if row["rows_json"] else []
        result["rowcount"] = row["rowcount"] if row["rowcount"] is not None else 0
        result["elapsed_ms"] = row["elapsed_ms"] if row["elapsed_ms"] is not None else 0.0
    elif status == "error":
        result["error"] = row["error"] or ""
        result["elapsed_ms"] = row["elapsed_ms"] if row["elapsed_ms"] is not None else 0.0
    return result


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


def _run_query_sync(
    manager: SessionManager,
    db_conn: sqlite3.Connection,
    execution_id: str,
    session_id: str,
    sql: str,
) -> None:
    """Execute SQL in a thread-pool worker and persist the result."""
    t0 = time.perf_counter()
    try:
        result = manager.execute_query(session_id, sql)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _update_complete(db_conn, execution_id, result.columns, result.rows, result.rowcount, elapsed_ms)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _update_error(db_conn, execution_id, str(exc), elapsed_ms)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    session_id: str
    sql: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_pondapi_execute_router(
    manager: SessionManager,
    db_conn: sqlite3.Connection,
) -> APIRouter:
    """Create and return the /pondapi router.

    Each call creates fresh rate-limit state (per-router-instance), so
    test reloads get isolated state.
    """
    _init_table(db_conn)

    # Per-router-instance sliding-window rate limit state.
    # Maps tenant_id -> deque of submission timestamps (monotonic).
    _submissions: dict[str, deque[float]] = {}
    _submissions_lock = threading.Lock()

    # Per-router-instance in-flight tracking for result polling
    _in_flight: dict[str, tuple[str, "Future[Any]"]] = {}

    def _count_window(tenant_id: str, rate_limit: int, window: float) -> int:
        """Return number of submissions in the current window for this tenant."""
        now = time.monotonic()
        cutoff = now - window
        with _submissions_lock:
            if tenant_id not in _submissions:
                _submissions[tenant_id] = deque()
            q = _submissions[tenant_id]
            # Prune expired entries
            while q and q[0] < cutoff:
                q.popleft()
            return len(q)

    def _record_submission(tenant_id: str) -> None:
        with _submissions_lock:
            if tenant_id not in _submissions:
                _submissions[tenant_id] = deque()
            _submissions[tenant_id].append(time.monotonic())

    router = APIRouter(prefix="/pondapi")

    @router.post("/execute", status_code=202)
    async def submit_execution(
        req: ExecuteRequest,
        response: Response,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        sql = req.sql
        if not sql or not sql.strip():
            raise HTTPException(status_code=400, detail="SQL must not be empty")

        try:
            manager.get_session(req.session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")

        tenant_id: str = auth.get("tenant_id", "default")
        rate_limit = int(os.environ.get("POND_PONDAPI_RATE_LIMIT", "10"))
        window = _WINDOW_SECONDS

        current = _count_window(tenant_id, rate_limit, window)
        if current >= rate_limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {rate_limit} executions per {window:.0f}s per tenant",
            )

        _record_submission(tenant_id)

        execution_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        _insert_execution(db_conn, execution_id, tenant_id, req.session_id, sql, created_at)

        fut = _executor.submit(
            _run_query_sync, manager, db_conn, execution_id, req.session_id, sql
        )
        _in_flight[execution_id] = (tenant_id, fut)

        response.headers["X-RateLimit-Limit"] = str(rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, rate_limit - current - 1))

        return {
            "execution_id": execution_id,
            "status": "pending",
            "created_at": created_at,
        }

    @router.get("/execute/{execution_id}/result")
    async def get_execution_result(
        execution_id: str,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        row = _fetch_row(db_conn, execution_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Execution not found: {execution_id}")

        tenant_id: str = auth.get("tenant_id", "default")
        row_tenant = row["tenant_id"]

        # Tenant isolation: deny if both sides have explicit (non-default) tenant IDs
        if row_tenant != tenant_id and row_tenant != "default" and tenant_id != "default":
            raise HTTPException(status_code=403, detail="Access denied to this execution")

        # If still pending and the future is done, refresh from DB to get latest status
        if row["status"] in ("pending", "running") and execution_id in _in_flight:
            _tid, fut = _in_flight[execution_id]
            if fut.done():
                row = _fetch_row(db_conn, execution_id)
                if row is None:
                    raise HTTPException(status_code=404, detail=f"Execution not found: {execution_id}")

        return _row_to_result(row)

    return router
