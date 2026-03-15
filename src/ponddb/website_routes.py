"""Website routes: landing, login, dashboard, and workgroup pages.

Cookie-based auth model:
  POST /login  → validates POND_API_KEY, sets signed session cookie
  /dashboard, /workgroup/* → require valid session cookie
"""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ponddb.session_manager import SessionManager

_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

COOKIE_NAME = "pond_session"


def _get_session_secret() -> str:
    return os.environ.get("POND_WEBSITE_SESSION_SECRET", "change-me-default-secret")


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


def make_website_router(manager: SessionManager, workgroups: dict) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def landing(request: Request) -> Response:
        return _templates.TemplateResponse(request, "landing.html")

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        return _templates.TemplateResponse(request, "login.html", {"error": None})

    @router.post("/login")
    async def login_submit(
        request: Request,
        api_key: str = Form(default=""),
    ) -> Response:
        if not api_key or not api_key.strip():
            return _templates.TemplateResponse(
                request, "login.html", {"error": "API key is required"}, status_code=400
            )
        expected = os.environ.get("POND_API_KEY", "")
        if not expected or api_key != expected:
            return _templates.TemplateResponse(
                request, "login.html", {"error": "Invalid API key"}, status_code=200
            )
        session_data = {"tenant_id": "default"}
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
        active_sessions = manager.session_count
        return _templates.TemplateResponse(
            request,
            "dashboard.html",
            {"active_sessions": active_sessions, "workgroups": list(workgroups.values())},
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
        return _templates.TemplateResponse(request, "workgroup.html", {"workgroup": wg})

    return router
