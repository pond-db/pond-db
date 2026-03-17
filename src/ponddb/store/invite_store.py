# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""InviteStore — CRUD operations for invite_tokens table."""

import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from ponddb.store.metadata_store import MetadataStore


class InviteStore:
    """Wraps MetadataStore to provide invite token CRUD."""

    def __init__(self, store: MetadataStore) -> None:
        self._store = store

    async def create_invite(
        self,
        email: str,
        tenant_id: str,
        created_by: str,
        role: str = "member",
        expires_in_hours: int = 168,
    ) -> dict:
        """Create a new invite token and return it as a dict."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=expires_in_hours)
        email_lc = email.lower()
        self._store._conn.execute(
            """
            INSERT INTO invite_tokens
                (token, email, tenant_id, role, status, created_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (token, email_lc, tenant_id, role, created_by, now.isoformat(), expires_at.isoformat()),
        )
        self._store._conn.commit()
        return {
            "token": token,
            "email": email_lc,
            "tenant_id": tenant_id,
            "role": role,
            "status": "pending",
            "created_by": created_by,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "accepted_at": None,
        }

    async def get_invite(self, token: str) -> Optional[dict]:
        """Return invite dict or None if not found."""
        cursor = self._store._conn.execute("SELECT * FROM invite_tokens WHERE token = ?", (token,))
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    async def list_invites(self, tenant_id: str) -> list[dict]:
        """Return all invites for a tenant ordered by created_at DESC."""
        cursor = self._store._conn.execute(
            "SELECT * FROM invite_tokens WHERE tenant_id = ? ORDER BY created_at DESC",
            (tenant_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    async def revoke_invite(self, token: str) -> None:
        """Mark invite as revoked. Raises ValueError if token not found."""
        cursor = self._store._conn.execute(
            "UPDATE invite_tokens SET status = 'revoked' WHERE token = ?", (token,)
        )
        self._store._conn.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"Invite token not found: {token}")

    async def accept_invite(self, token: str, email: str) -> dict:
        """Accept an invite. Returns dict with error key on expired/revoked, raises on other errors."""
        row = await self.get_invite(token)
        if row is None:
            raise ValueError(f"Invite token not found: {token}")

        # Check expiry
        expires_at_str = row["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > expires_at:
            return {"error": "expired"}

        # Check status
        if row["status"] == "accepted":
            raise ValueError("Already accepted: conflict")
        if row["status"] == "revoked":
            return {"error": "revoked"}

        # Check email (case-insensitive)
        if row["email"].lower() != email.lower():
            raise ValueError("Email forbidden: does not match invite")

        # Mark accepted
        accepted_at = now.isoformat()
        self._store._conn.execute(
            "UPDATE invite_tokens SET status = 'accepted', accepted_at = ? WHERE token = ?",
            (accepted_at, token),
        )
        self._store._conn.commit()
        return {**dict(row), "status": "accepted", "accepted_at": accepted_at}
