"""Website routes: landing, login, dashboard, settings, and workgroup pages.

Cookie-based auth model:
  POST /login  → validates POND_API_KEY, sets signed session cookie
  /dashboard, /workgroup/*, /settings → require valid session cookie
"""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ponddb.jwt_auth import _get_api_key, _get_session_secret as _jwt_get_session_secret

from ponddb import __version__
from ponddb.session_manager import SessionManager

_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

COOKIE_NAME = "pond_session"


def _get_session_secret() -> str:
    return _jwt_get_session_secret()


def _sign_session(data: dict) -> str:
    secret = _get_session_secret()
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_session(cookie: str) -> Optional[dict]:
    try:
        payload, sig = cookie.rsplit(".", 1)
        secret = _get_session_secret()
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(base64.urlsafe_b64decode(payload).decode())
    except Exception:
        return None


def _get_session(request: Request) -> Optional[dict]:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    return _verify_session(cookie)


def _build_current_user(session: dict) -> dict:
    """Build a current_user context dict from session cookie data."""
    return {
        "display_name": session.get("display_name", "User"),
        "role": session.get("role", "user"),
        "tenant_id": session.get("tenant_id", "default"),
    }


def make_website_router(
    manager: SessionManager,
    workgroups: dict,
    store: Any = None,
    dataset_manager: Any = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def landing(request: Request) -> Response:
        return _templates.TemplateResponse(request, "landing.html")

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        invite_state = request.query_params.get("invite_state", "")
        namespace_name = request.query_params.get("namespace_name", "")
        return _templates.TemplateResponse(
            request, "login.html",
            {"error": None, "invite_state": invite_state, "namespace_name": namespace_name},
        )

    @router.post("/login")
    async def login_submit(
        request: Request,
        api_key: str = Form(default=""),
    ) -> Response:
        ctx = {"error": None, "invite_state": "", "namespace_name": ""}
        if not api_key or not api_key.strip():
            ctx["error"] = "API key is required"
            return _templates.TemplateResponse(request, "login.html", ctx, status_code=400)
        expected = _get_api_key()
        if not expected or api_key != expected:
            ctx["error"] = "Invalid API key"
            return _templates.TemplateResponse(request, "login.html", ctx, status_code=200)
        # Master API key holder is always admin
        session_data = {"tenant_id": "default", "role": "admin"}
        cookie_val = _sign_session(session_data)
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            COOKIE_NAME, cookie_val, httponly=True, samesite="lax", max_age=86400
        )
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Response:
        session = _get_session(request)
        if not session:
            return RedirectResponse(url="/login", status_code=302)

        current_user = _build_current_user(session)
        wg_list = list(workgroups.values())

        # Enrich workgroups with active session counts
        all_sessions = manager.list_sessions()
        for wg in wg_list:
            wg_name = wg.get("name", "")
            wg["active_sessions"] = sum(
                1 for s in all_sessions if s.get("workgroup_id") == wg_name
            )

        # Queries today + recent executions from metadata store
        queries_today = 0
        recent_executions: list[dict] = []
        if store is not None:
            try:
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                today_rows = await store.get_query_history(
                    tenant_id=session.get("tenant_id", "default"),
                    start=today_start,
                    limit=1000,
                )
                queries_today = len(today_rows)
                recent_executions = await store.get_query_history(
                    tenant_id=session.get("tenant_id", "default"),
                    limit=10,
                )
            except Exception:
                pass  # graceful degradation — show 0

        # Dataset count
        datasets_count = 0
        if dataset_manager is not None:
            try:
                datasets_count = len(dataset_manager.list_datasets())
            except Exception:
                pass

        stats = {
            "active_sessions": manager.session_count,
            "queries_today": queries_today,
            "datasets": datasets_count,
        }

        return _templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "current_user": current_user,
                "stats": stats,
                "workgroups": wg_list,
                "recent_executions": recent_executions,
                "active_page": "dashboard",
                "workgroups_nav": wg_list,
            },
        )

    @router.get("/dashboard/sessions", response_class=HTMLResponse)
    async def sessions_page(request: Request) -> Response:
        session = _get_session(request)
        if not session:
            return RedirectResponse(url="/login", status_code=302)
        sessions = manager.list_sessions()
        wg_list = list(workgroups.values())
        return _templates.TemplateResponse(
            request,
            "sessions.html",
            {
                "sessions": sessions,
                "active_page": "sessions",
                "workgroups_nav": wg_list,
            },
        )

    @router.get("/workgroup/{workgroup_id}", response_class=HTMLResponse)
    async def workgroup_page(request: Request, workgroup_id: str) -> Response:
        session = _get_session(request)
        if not session:
            return RedirectResponse(url="/login", status_code=302)
        wg = None
        for w in workgroups.values():
            if w.get("name") == workgroup_id or w.get("id") == workgroup_id:
                wg = w
                break
        if wg is None:
            raise HTTPException(status_code=404, detail=f"Workgroup not found: {workgroup_id}")
        wg_list = list(workgroups.values())
        wg_name = wg.get("name", workgroup_id)
        all_sessions = manager.list_sessions()
        wg_sessions = [s for s in all_sessions if s.get("workgroup_id") == wg_name]
        wg["active_sessions"] = len(wg_sessions)
        return _templates.TemplateResponse(
            request,
            "workgroup.html",
            {
                "workgroup": wg,
                "wg_sessions": wg_sessions,
                "active_page": "workgroup",
                "workgroups_nav": wg_list,
                "breadcrumb": [
                    {"label": "Dashboard", "url": "/dashboard"},
                    {"label": f"Workgroup: {wg_name}"},
                ],
            },
        )

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Response:
        session = _get_session(request)
        if not session:
            return RedirectResponse(url="/login", status_code=302)
        current_user = _build_current_user(session)
        wg_list = list(workgroups.values())
        config = {
            "host": os.environ.get("POND_HOST", "0.0.0.0"),
            "port": os.environ.get("POND_PORT", "8432"),
            "data_root": os.environ.get("POND_DATA_ROOT", "./data"),
            "sqlite_path": os.environ.get("POND_SQLITE_PATH", ":memory:"),
            "log_level": os.environ.get("POND_LOG_LEVEL", "INFO"),
            "idle_timeout": os.environ.get("POND_IDLE_TIMEOUT", "300"),
            "max_session_age": os.environ.get("POND_MAX_SESSION_AGE", "86400"),
            "memory_limit": os.environ.get("POND_SESSION_MEMORY_LIMIT", "2GB"),
            "threads": os.environ.get("POND_SESSION_THREADS", "4"),
            "jwt_expiry": os.environ.get("POND_JWT_EXPIRY_SECONDS", "3600"),
            "rate_limit": os.environ.get("POND_PONDAPI_RATE_LIMIT", "10"),
            "rate_window": os.environ.get("POND_PONDAPI_RATE_WINDOW", "60"),
            "max_result_mb": os.environ.get("POND_MAX_RESULT_MB", "100"),
            "cors_origins": os.environ.get("POND_CORS_ORIGINS", ""),
            "google_oauth": bool(os.environ.get("POND_GOOGLE_CLIENT_ID")),
            "github_oauth": bool(os.environ.get("POND_GITHUB_CLIENT_ID")),
            "smtp_configured": bool(os.environ.get("POND_SMTP_HOST")),
        }
        return _templates.TemplateResponse(
            request, "settings.html",
            {
                "current_user": current_user,
                "config": config,
                "version": __version__,
                "active_page": "settings",
                "workgroups_nav": wg_list,
            },
        )

    return router
