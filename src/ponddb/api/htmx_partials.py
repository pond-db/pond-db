# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""HTMX partial routes — return HTML fragments for dynamic dashboard updates.

All endpoints return HTML fragments (no <html>/<body>), suitable for
hx-swap="innerHTML" or hx-swap="outerHTML" targets.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ponddb.auth.jwt_auth import require_auth
from ponddb.engine.session_manager import SessionManager

_templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _fetch_execution(conn: sqlite3.Connection, execution_id: str) -> Optional[dict]:
    """Fetch a PondAPI execution row and format it for templates."""
    cur = conn.execute(
        "SELECT * FROM pondapi_executions WHERE execution_id=?", (execution_id,)
    )
    row = cur.fetchone()
    if row is None:
        return None
    result: dict[str, Any] = {
        "execution_id": row["execution_id"],
        "tenant_id": row["tenant_id"],
        "session_id": row["session_id"],
        "sql": row["sql"],
        "status": row["status"],
        "created_at": row["created_at"],
        "elapsed_ms": row["elapsed_ms"],
        "error": row["error"],
        "rowcount": row["rowcount"],
        "columns": json.loads(row["columns_json"]) if row["columns_json"] else [],
        "rows": json.loads(row["rows_json"]) if row["rows_json"] else [],
    }
    return result


def make_htmx_router(
    manager: SessionManager,
    workgroups: dict,
    pondapi_db: Optional[sqlite3.Connection] = None,
    store: Any = None,
) -> APIRouter:
    """Return HTMX partials router. All routes require auth."""
    router = APIRouter(prefix="/htmx", tags=["htmx"])

    @router.get("/sessions-table", response_class=HTMLResponse)
    async def sessions_table(request: Request) -> Any:
        """Return sessions table fragment for auto-refresh."""
        await require_auth(request)
        sessions = manager.list_sessions()
        return _templates.TemplateResponse(
            request,
            "_partials/sessions_table.html",
            {"sessions": sessions},
        )

    @router.delete("/session/{session_id}", response_class=HTMLResponse)
    async def terminate_session(request: Request, session_id: str) -> Any:
        """Terminate a session and return empty row (removed from DOM)."""
        await require_auth(request)
        try:
            manager.destroy_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Session not found")
        # Return empty string so hx-swap="outerHTML" removes the row
        return HTMLResponse(content="", status_code=200)

    @router.get("/workgroup/{wg_name}/sessions", response_class=HTMLResponse)
    async def workgroup_sessions(request: Request, wg_name: str) -> Any:
        """Return sessions for a specific workgroup."""
        await require_auth(request)
        if not any(w.get("name") == wg_name for w in workgroups.values()):
            raise HTTPException(status_code=404, detail="Workgroup not found")
        sessions = [s for s in manager.list_sessions() if s.get("workgroup_id") == wg_name]
        return _templates.TemplateResponse(
            request, "_partials/workgroup_sessions.html", {"sessions": sessions},
        )

    @router.get("/pondapi/{execution_id}/detail", response_class=HTMLResponse)
    async def pondapi_detail(request: Request, execution_id: str) -> Any:
        """Return PondAPI execution detail panel HTML fragment."""
        claims = await require_auth(request)
        if pondapi_db is None:
            raise HTTPException(status_code=503, detail="PondAPI not available")
        execution = _fetch_execution(pondapi_db, execution_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="Execution not found")
        # Tenant isolation
        tenant_id = claims.get("tenant_id", "default")
        if execution["tenant_id"] != tenant_id:
            raise HTTPException(status_code=404, detail="Execution not found")
        return _templates.TemplateResponse(
            request,
            "_partials/pondapi_detail.html",
            {"execution": execution},
        )

    # ── Workgroup tab partials ────────────────────────────────────────────

    def _find_workgroup(wg_name: str) -> Optional[dict]:
        """Look up a workgroup by name."""
        for w in workgroups.values():
            if w.get("name") == wg_name:
                return w
        return None

    @router.get("/workgroup/{wg_name}/overview", response_class=HTMLResponse)
    async def workgroup_overview(request: Request, wg_name: str) -> Any:
        """Return workgroup overview tab fragment."""
        await require_auth(request)
        wg = _find_workgroup(wg_name)
        if wg is None:
            raise HTTPException(status_code=404, detail="Workgroup not found")
        all_sessions = manager.list_sessions()
        wg["active_sessions"] = sum(
            1 for s in all_sessions if s.get("workgroup_id") == wg_name
        )
        return _templates.TemplateResponse(
            request,
            "_partials/workgroup_overview.html",
            {"workgroup": wg},
        )

    @router.get("/workgroup/{wg_name}/history", response_class=HTMLResponse)
    async def workgroup_history(request: Request, wg_name: str) -> Any:
        """Return workgroup query history tab fragment."""
        claims = await require_auth(request)
        wg = _find_workgroup(wg_name)
        if wg is None:
            raise HTTPException(status_code=404, detail="Workgroup not found")
        history: list[dict] = []
        if store is not None:
            try:
                tenant_id = claims.get("tenant_id", "default")
                history = await store.get_query_history(
                    tenant_id=tenant_id, limit=25,
                )
            except Exception:
                pass
        return _templates.TemplateResponse(
            request,
            "_partials/workgroup_history.html",
            {"history": history, "workgroup": wg},
        )

    @router.get("/workgroup/{wg_name}/apikeys", response_class=HTMLResponse)
    async def workgroup_apikeys(request: Request, wg_name: str) -> Any:
        """Return workgroup API keys tab fragment."""
        await require_auth(request)
        wg = _find_workgroup(wg_name)
        if wg is None:
            raise HTTPException(status_code=404, detail="Workgroup not found")
        return _templates.TemplateResponse(
            request,
            "_partials/workgroup_apikeys.html",
            {"workgroup": wg},
        )

    # ── Session suspend/resume ────────────────────────────────────────────

    @router.post("/session/{session_id}/suspend", response_class=HTMLResponse)
    async def suspend_session(request: Request, session_id: str) -> Any:
        """Suspend a session and return updated row fragment."""
        await require_auth(request)
        try:
            manager.suspend_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Session not found")
        except ValueError:
            raise HTTPException(status_code=409, detail="Session already suspended")
        session = manager.get_session(session_id)
        return _templates.TemplateResponse(
            request,
            "_partials/session_row.html",
            {"session": session},
        )

    @router.post("/session/{session_id}/resume", response_class=HTMLResponse)
    async def resume_session(request: Request, session_id: str) -> Any:
        """Resume a suspended session and return updated row fragment."""
        await require_auth(request)
        try:
            manager.resume_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Session not found")
        except ValueError:
            raise HTTPException(status_code=409, detail="Session already active")
        session = manager.get_session(session_id)
        return _templates.TemplateResponse(
            request,
            "_partials/session_row.html",
            {"session": session},
        )

    return router
