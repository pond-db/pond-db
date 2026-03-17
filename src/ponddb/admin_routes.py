"""Admin console HTML routes.

All /admin/* pages require a valid signed session cookie with role='admin'.
No cookie → 302 to /login. Non-admin cookie → 403.
"""

import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ponddb.invite_store import InviteStore

COOKIE_NAME = "pond_session"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _get_session_secret() -> str:
    return os.environ.get("POND_WEBSITE_SESSION_SECRET", "change-me-default-secret")


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


def _check_admin(request: Request) -> tuple[Optional[dict], Optional[Any]]:
    """Returns (session, error_response). error_response is set when auth fails."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None, RedirectResponse(url="/login", status_code=302)
    session = _verify_session(cookie)
    if not session:
        return None, RedirectResponse(url="/login", status_code=302)
    if session.get("role") != "admin":
        return None, HTMLResponse("Forbidden", status_code=403)
    return session, None


def _current_user_from_session(session: dict) -> dict:
    """Build current_user context dict from admin session cookie."""
    return {
        "display_name": session.get("display_name", "Admin"),
        "role": session.get("role", "admin"),
        "tenant_id": session.get("tenant_id", "default"),
    }


def make_admin_router(
    invite_store: InviteStore,
    workgroups: dict,
    namespaces: dict,
    get_stats: Callable[[], dict],
) -> APIRouter:
    """Return admin console router. All routes require admin session cookie."""
    router = APIRouter(prefix="/admin")

    @router.get("", response_class=HTMLResponse)
    async def admin_home(request: Request) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        wg_list = list(workgroups.values())
        return _templates.TemplateResponse(
            request, "admin_home.html",
            {"session": session, "current_user": _current_user_from_session(session), "active_page": "admin", "workgroups_nav": wg_list},
        )

    @router.get("/invites", response_class=HTMLResponse)
    async def admin_invites(request: Request) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        tenant_id: str = session.get("tenant_id", "default")
        invites = await invite_store.list_invites(tenant_id)
        wg_list = list(workgroups.values())
        return _templates.TemplateResponse(
            request, "admin_invites.html",
            {"session": session, "current_user": _current_user_from_session(session), "invites": invites, "error": None, "active_page": "admin", "workgroups_nav": wg_list},
        )

    @router.post("/invites")
    async def admin_create_invite(
        request: Request,
        email: str = Form(default=""),
        role: str = Form(default="member"),
        expires_in_hours: str = Form(default="168"),
    ) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        tenant_id: str = session.get("tenant_id", "default")

        if not email or not _EMAIL_RE.match(email.strip()):
            invites = await invite_store.list_invites(tenant_id)
            wg_list = list(workgroups.values())
            return _templates.TemplateResponse(
                request, "admin_invites.html",
                {"session": session, "current_user": _current_user_from_session(session), "invites": invites, "error": "Invalid email address", "active_page": "admin", "workgroups_nav": wg_list},
                status_code=400,
            )

        try:
            hours = int(expires_in_hours) if expires_in_hours.strip() else 168
        except ValueError:
            hours = 168

        try:
            await invite_store.create_invite(
                email=email.strip(),
                tenant_id=tenant_id,
                created_by=tenant_id,
                role=role or "member",
                expires_in_hours=hours,
            )
        except Exception:
            pass  # Duplicate or DB error — handled gracefully

        return RedirectResponse(url="/admin/invites", status_code=303)

    @router.post("/invites/{token}/revoke")
    async def admin_revoke_invite(request: Request, token: str) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        tenant_id: str = session.get("tenant_id", "default")
        try:
            await invite_store.revoke_invite(token)
        except ValueError:
            invites = await invite_store.list_invites(tenant_id)
            wg_list = list(workgroups.values())
            return _templates.TemplateResponse(
                request, "admin_invites.html",
                {"session": session, "current_user": _current_user_from_session(session), "invites": invites, "error": "Invite not found", "active_page": "admin", "workgroups_nav": wg_list},
                status_code=404,
            )
        return RedirectResponse(url="/admin/invites", status_code=303)

    @router.get("/namespaces", response_class=HTMLResponse)
    async def admin_namespaces(request: Request) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        wg_by_ns: dict[str, list] = {}
        for wg in workgroups.values():
            ns_id = wg.get("namespace_id", "")
            wg_by_ns.setdefault(ns_id, []).append(wg)
        wg_list = list(workgroups.values())
        return _templates.TemplateResponse(
            request, "admin_namespaces.html",
            {"session": session, "current_user": _current_user_from_session(session), "namespaces": namespaces, "wg_by_ns": wg_by_ns, "active_page": "admin", "workgroups_nav": wg_list},
        )

    @router.get("/usage", response_class=HTMLResponse)
    async def admin_usage(request: Request) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        stats = get_stats()
        wg_list = list(workgroups.values())
        return _templates.TemplateResponse(
            request, "admin_usage.html",
            {"session": session, "current_user": _current_user_from_session(session), "stats": stats, "workgroups": wg_list, "active_page": "admin", "workgroups_nav": wg_list},
        )

    @router.get("/workgroups/{wg_id}/quota", response_class=HTMLResponse)
    async def admin_quota_get(request: Request, wg_id: str) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        if wg_id not in workgroups:
            return HTMLResponse("Not Found", status_code=404)
        wg = workgroups[wg_id]
        wg_list = list(workgroups.values())
        return _templates.TemplateResponse(
            request, "admin_quota.html",
            {"session": session, "current_user": _current_user_from_session(session), "workgroup": wg, "wg_id": wg_id, "error": None, "active_page": "admin", "workgroups_nav": wg_list},
        )

    @router.post("/workgroups/{wg_id}/quota")
    async def admin_quota_post(
        request: Request,
        wg_id: str,
        max_sessions: str = Form(default=""),
        max_query_duration_ms: str = Form(default=""),
        max_result_mb: str = Form(default=""),
    ) -> Any:
        session, err = _check_admin(request)
        if err is not None:
            return err
        if wg_id not in workgroups:
            return HTMLResponse("Not Found", status_code=404)
        wg = workgroups[wg_id]

        def _parse(s: str) -> Optional[int]:
            s = s.strip()
            if not s:
                return None
            try:
                return int(s)
            except ValueError:
                raise ValueError(f"Invalid integer: {s!r}")

        try:
            ms = _parse(max_sessions)
            mqd = _parse(max_query_duration_ms)
            mrm = _parse(max_result_mb)
        except ValueError as exc:
            wg_list = list(workgroups.values())
            return _templates.TemplateResponse(
                request, "admin_quota.html",
                {"session": session, "current_user": _current_user_from_session(session), "workgroup": wg, "wg_id": wg_id, "error": str(exc), "active_page": "admin", "workgroups_nav": wg_list},
                status_code=400,
            )

        if ms is not None and ms <= 0:
            wg_list = list(workgroups.values())
            return _templates.TemplateResponse(
                request, "admin_quota.html",
                {
                    "session": session, "current_user": _current_user_from_session(session),
                    "workgroup": wg, "wg_id": wg_id,
                    "error": "max_sessions must be a positive integer",
                    "active_page": "admin", "workgroups_nav": wg_list,
                },
                status_code=400,
            )

        if ms is None and mqd is None and mrm is None:
            wg["quota"] = None
        else:
            wg["quota"] = {
                "max_sessions": ms,
                "max_query_duration_ms": mqd,
                "max_result_mb": mrm,
            }

        return RedirectResponse(url="/admin/namespaces", status_code=303)

    return router
