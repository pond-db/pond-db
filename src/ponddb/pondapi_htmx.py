"""HTMX endpoints for async SQL execution via HTML fragments.

POST /pondapi/execute/htmx          — submit SQL via form, returns HTML fragment
GET  /pondapi/execute/{id}/htmx     — poll status, returns updated HTML fragment

Fragment contract:
  pending/running  → contains hx-trigger="every 1s" + hx-get for auto-polling
  complete         → contains result table, no polling trigger
  error            → contains error message, no polling trigger
"""

import sqlite3
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ponddb.jwt_auth import require_auth
from ponddb.pondapi_execute import (
    _executor,
    _fetch_row,
    _insert_execution,
    _row_to_result,
    _run_query_sync,
)
from ponddb.session_manager import SessionManager


def _pending_fragment(execution_id: str) -> str:
    poll_url = f"/pondapi/execute/{execution_id}/htmx"
    return (
        f'<div id="pondapi-result"'
        f' hx-get="{poll_url}"'
        f' hx-trigger="every 1s"'
        f' hx-swap="outerHTML">'
        f"<p>Status: pending</p>"
        f'<span class="htmx-indicator">Running...</span>'
        f"</div>"
    )


def _complete_fragment(result: dict) -> str:
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    thead = "".join(f"<th>{c}</th>" for c in columns)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    rowcount = result.get("rowcount", 0)
    elapsed = result.get("elapsed_ms", 0.0)
    return (
        f'<div id="pondapi-result">'
        f"<p>Status: complete ({rowcount} rows, {elapsed:.1f}ms)</p>"
        f"<table><thead><tr>{thead}</tr></thead>"
        f"<tbody>{tbody}</tbody></table>"
        f"</div>"
    )


def _error_fragment(message: str) -> str:
    return (
        f'<div id="pondapi-result">'
        f"<p>Status: error</p>"
        f'<p class="error">Error: {message}</p>'
        f"</div>"
    )


def make_pondapi_htmx_router(
    manager: SessionManager,
    db_conn: sqlite3.Connection,
) -> APIRouter:
    router = APIRouter(prefix="/pondapi")
    _in_flight: dict[str, tuple[str, "Future[Any]"]] = {}

    @router.post("/execute/htmx", response_class=HTMLResponse)
    async def submit_htmx(
        request: Request,
        session_id: str = Form(...),
        sql: str = Form(...),
        auth: dict = Depends(require_auth),
    ) -> HTMLResponse:
        if not sql or not sql.strip():
            raise HTTPException(status_code=400, detail="SQL must not be empty")

        try:
            manager.get_session(session_id)
        except KeyError:
            body = _error_fragment(f"Session not found: {session_id}")
            return HTMLResponse(content=body, status_code=200)

        tenant_id: str = auth.get("tenant_id", "default")
        execution_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        _insert_execution(db_conn, execution_id, tenant_id, session_id, sql, created_at)

        fut = _executor.submit(
            _run_query_sync, manager, None, db_conn, execution_id, session_id, sql
        )
        _in_flight[execution_id] = (tenant_id, fut)

        return HTMLResponse(content=_pending_fragment(execution_id), status_code=200)

    @router.get("/execute/{execution_id}/htmx", response_class=HTMLResponse)
    async def poll_htmx(
        execution_id: str,
        auth: dict = Depends(require_auth),
    ) -> HTMLResponse:
        row = _fetch_row(db_conn, execution_id)
        if row is None:
            body = _error_fragment(f"Execution not found: {execution_id}")
            return HTMLResponse(content=body, status_code=200)

        tenant_id: str = auth.get("tenant_id", "default")
        row_tenant = row["tenant_id"]
        if (
            row_tenant != tenant_id
            and row_tenant != "default"
            and tenant_id != "default"
        ):
            body = _error_fragment("Forbidden: access denied")
            return HTMLResponse(content=body, status_code=403)

        status = row["status"]

        # Refresh from DB if future is done
        if status in ("pending", "running") and execution_id in _in_flight:
            _, fut = _in_flight[execution_id]
            if fut.done():
                row = _fetch_row(db_conn, execution_id)
                if row is None:
                    return HTMLResponse(
                        content=_error_fragment(f"Execution lost: {execution_id}"),
                        status_code=200,
                    )
                status = row["status"]

        if status == "complete":
            result = _row_to_result(row)
            return HTMLResponse(content=_complete_fragment(result), status_code=200)
        elif status == "error":
            error = row["error"] or "Unknown error"
            return HTMLResponse(content=_error_fragment(error), status_code=200)
        else:
            return HTMLResponse(content=_pending_fragment(execution_id), status_code=200)

    return router
