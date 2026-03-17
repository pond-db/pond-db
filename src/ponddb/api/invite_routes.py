# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Invite token API routes and SMTP email delivery."""

import logging
import os
import re
import smtplib
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ponddb.security import audit_log
from ponddb.store.invite_store import InviteStore
from ponddb.auth.jwt_auth import create_access_token, require_admin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simple email format validator (no external dependency)
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email_format(email: str) -> str:
    if not _EMAIL_RE.match(email):
        raise ValueError(f"Invalid email format: {email!r}")
    return email


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateInviteRequest(BaseModel):
    email: str
    role: str = "member"
    expires_in_hours: int = 168

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, v: str) -> str:
        return _validate_email_format(v)


class AcceptInviteRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def email_must_be_valid(cls, v: str) -> str:
        return _validate_email_format(v)


# ---------------------------------------------------------------------------
# SMTP delivery
# ---------------------------------------------------------------------------


def send_invite_email(email: str, token: str, **kwargs) -> None:
    """Send invite email via SMTP. No-op if SMTP_HOST is not configured."""
    smtp_host = os.environ.get("POND_SMTP_HOST", "")
    if not smtp_host:
        return

    smtp_port = int(os.environ.get("POND_SMTP_PORT", "587"))
    smtp_user = os.environ.get("POND_SMTP_USER", "")
    smtp_password = os.environ.get("POND_SMTP_PASSWORD", "")
    smtp_from = os.environ.get("POND_SMTP_FROM", smtp_user)
    base_url = os.environ.get("POND_BASE_URL", "http://localhost:8432")

    accept_link = f"{base_url}/invites/{token}/accept"
    body = (
        f"You have been invited to PondDB.\n\nAccept your invite:\n{accept_link}\n\nToken: {token}"
    )

    msg = MIMEText(body)
    msg["Subject"] = f"Your PondDB Invite ({token})"
    msg["From"] = smtp_from
    msg["To"] = email

    with smtplib.SMTP(smtp_host, smtp_port) as conn:
        if smtp_user and smtp_password:
            conn.starttls()
            conn.login(smtp_user, smtp_password)
        conn.sendmail(smtp_from, [email], msg.as_string())


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def make_invite_router(invite_store: InviteStore) -> APIRouter:
    """Return router with all /invites endpoints."""
    router = APIRouter(prefix="/invites", tags=["invites"])

    @router.post("", status_code=201)
    async def create_invite(
        req: CreateInviteRequest,
        claims: dict = Depends(require_admin),
    ) -> dict:
        tenant_id: str = claims.get("tenant_id", "default")
        created_by: str = claims.get("sub", tenant_id)
        invite = await invite_store.create_invite(
            email=req.email,
            tenant_id=tenant_id,
            created_by=created_by,
            role=req.role,
            expires_in_hours=req.expires_in_hours,
        )
        # Fire-and-forget email — never blocks invite creation
        try:
            send_invite_email(req.email, invite["token"])
        except Exception as exc:
            logger.warning("Failed to send invite email: %s", exc)
        await audit_log.log_event(
            None,
            "invite_created",
            tenant_id=tenant_id,
            detail=f"invited {req.email} as {req.role}",
        )
        return invite

    @router.get("")
    async def list_invites(claims: dict = Depends(require_admin)) -> list[dict]:
        tenant_id: str = claims.get("tenant_id", "default")
        return await invite_store.list_invites(tenant_id)

    @router.get("/{token}")
    async def get_invite(token: str, claims: dict = Depends(require_admin)) -> dict:
        invite = await invite_store.get_invite(token)
        if invite is None:
            raise HTTPException(status_code=404, detail=f"Invite not found: {token}")
        return invite

    @router.delete("/{token}")
    async def revoke_invite(token: str, claims: dict = Depends(require_admin)) -> dict:
        try:
            await invite_store.revoke_invite(token)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"Invite not found: {token}")
        return {"detail": "revoked"}

    @router.post("/{token}/accept")
    async def accept_invite(token: str, req: AcceptInviteRequest) -> dict:
        """Public endpoint — no auth required."""
        invite = await invite_store.get_invite(token)
        if invite is None:
            raise HTTPException(status_code=404, detail=f"Invite not found: {token}")

        try:
            result = await invite_store.accept_invite(token, req.email)
        except ValueError as exc:
            msg = str(exc).lower()
            if "already" in msg or "conflict" in msg:
                raise HTTPException(status_code=409, detail=str(exc))
            if "email" in msg or "forbidden" in msg:
                raise HTTPException(status_code=403, detail=str(exc))
            if "revoked" in msg:
                raise HTTPException(status_code=410, detail=str(exc))
            raise HTTPException(status_code=400, detail=str(exc))

        if isinstance(result, dict) and result.get("error"):
            err = result["error"]
            if err == "expired":
                raise HTTPException(status_code=410, detail="Invite has expired")
            if err == "revoked":
                raise HTTPException(status_code=410, detail="Invite has been revoked")
            raise HTTPException(status_code=400, detail=f"Cannot accept invite: {err}")

        # Issue a JWT for the newly accepted user
        tenant_id: str = result.get("tenant_id", "default")
        role: str = result.get("role", "member")
        access_token = create_access_token(tenant_id, role=role)

        await audit_log.log_event(
            None,
            "user_provisioned",
            tenant_id=tenant_id,
            detail=f"provisioned {result.get('email')} as {role}",
        )

        return {
            "tenant_id": tenant_id,
            "access_token": access_token,
            "status": result.get("status"),
            "email": result.get("email"),
        }

    return router
