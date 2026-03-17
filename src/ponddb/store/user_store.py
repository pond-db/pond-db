# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""UserStore — SQLite-backed storage for users, org_members, workgroup_members, api_keys."""

import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


class UserStore:
    """Synchronous SQLite store with async interface for user management."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize_blocking(self) -> None:
        """Create the SQLite connection and all required tables (idempotent)."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT,
                provider TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                tenant_id TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                avatar_url TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS org_members (
                org_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                added_at TEXT NOT NULL,
                PRIMARY KEY (org_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS workgroup_members (
                workgroup_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                added_at TEXT NOT NULL,
                PRIMARY KEY (workgroup_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );
        """)
        self._conn.commit()

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {k: row[k] for k in row.keys()}

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    async def create_user(
        self,
        email: str,
        display_name: str,
        provider: str,
        provider_id: str,
        tenant_id: str,
        role: str = "member",
        avatar_url: Optional[str] = None,
    ) -> dict[str, Any]:
        assert self._conn is not None
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        email_lower = email.lower()
        self._conn.execute(
            "INSERT INTO users (id, email, display_name, provider, provider_id, tenant_id, "
            "role, avatar_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                email_lower,
                display_name,
                provider,
                provider_id,
                tenant_id,
                role,
                avatar_url,
                now,
            ),
        )
        self._conn.commit()
        return {
            "id": user_id,
            "email": email_lower,
            "display_name": display_name,
            "provider": provider,
            "provider_id": provider_id,
            "tenant_id": tenant_id,
            "role": role,
            "avatar_url": avatar_url,
            "created_at": now,
            "last_login_at": None,
        }

    async def get_user_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        assert self._conn is not None
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row(row) if row else None

    async def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email,)
        ).fetchone()
        return self._row(row) if row else None

    async def get_user_by_provider_id(
        self, provider: str, provider_id: str
    ) -> Optional[dict[str, Any]]:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM users WHERE provider = ? AND provider_id = ?", (provider, provider_id)
        ).fetchone()
        return self._row(row) if row else None

    async def get_user_by_tenant_id(self, tenant_id: str) -> Optional[dict[str, Any]]:
        assert self._conn is not None
        row = self._conn.execute("SELECT * FROM users WHERE tenant_id = ?", (tenant_id,)).fetchone()
        return self._row(row) if row else None

    async def update_user(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        assert self._conn is not None
        allowed = {"display_name", "avatar_url", "last_login_at", "role"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        user = await self.get_user_by_id(user_id)
        if user is None:
            raise ValueError(f"Not found: {user_id}")
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            self._conn.execute(
                f"UPDATE users SET {set_clause} WHERE id = ?",
                [*updates.values(), user_id],
            )
            self._conn.commit()
        return await self.get_user_by_id(user_id)  # type: ignore[return-value]

    async def upsert_user(
        self,
        provider: str,
        provider_id: str,
        email: str,
        display_name: str,
        tenant_id: str,
        avatar_url: Optional[str] = None,
    ) -> dict[str, Any]:
        existing = await self.get_user_by_provider_id(provider, provider_id)
        if existing:
            now = datetime.now(timezone.utc).isoformat()
            upd: dict[str, Any] = {"last_login_at": now, "display_name": display_name}
            if avatar_url is not None:
                upd["avatar_url"] = avatar_url
            return await self.update_user(existing["id"], **upd)
        return await self.create_user(
            email=email,
            display_name=display_name,
            provider=provider,
            provider_id=provider_id,
            tenant_id=tenant_id,
            avatar_url=avatar_url,
        )

    # ------------------------------------------------------------------
    # Org members
    # ------------------------------------------------------------------

    async def add_org_member(
        self, org_id: str, user_id: str, role: str = "member"
    ) -> dict[str, Any]:
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO org_members (org_id, user_id, role, added_at) VALUES (?, ?, ?, ?)",
            (org_id, user_id, role, now),
        )
        self._conn.commit()
        return {"org_id": org_id, "user_id": user_id, "role": role, "added_at": now}

    async def list_org_members(self, org_id: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM org_members WHERE org_id = ?", (org_id,)
        ).fetchall()
        return [self._row(r) for r in rows]

    async def remove_org_member(self, org_id: str, user_id: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "DELETE FROM org_members WHERE org_id = ? AND user_id = ?", (org_id, user_id)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Workgroup members
    # ------------------------------------------------------------------

    async def add_workgroup_member(
        self, workgroup_id: str, user_id: str, role: str = "member"
    ) -> dict[str, Any]:
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO workgroup_members (workgroup_id, user_id, role, added_at) VALUES (?, ?, ?, ?)",
            (workgroup_id, user_id, role, now),
        )
        self._conn.commit()
        return {"workgroup_id": workgroup_id, "user_id": user_id, "role": role, "added_at": now}

    async def list_workgroup_members(self, workgroup_id: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM workgroup_members WHERE workgroup_id = ?", (workgroup_id,)
        ).fetchall()
        return [self._row(r) for r in rows]

    async def remove_workgroup_member(self, workgroup_id: str, user_id: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "DELETE FROM workgroup_members WHERE workgroup_id = ? AND user_id = ?",
            (workgroup_id, user_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # API keys
    # ------------------------------------------------------------------

    async def create_api_key(
        self,
        user_id: str,
        tenant_id: str,
        name: str,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        assert self._conn is not None
        key_id = str(uuid.uuid4())
        plaintext = "pk_" + secrets.token_hex(32)
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        key_prefix = plaintext[:8]
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO api_keys (id, user_id, tenant_id, name, key_hash, key_prefix, "
            "revoked, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (key_id, user_id, tenant_id, name, key_hash, key_prefix, now, expires_at),
        )
        self._conn.commit()
        return {
            "id": key_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "name": name,
            "key_prefix": key_prefix,
            "revoked": False,
            "created_at": now,
            "expires_at": expires_at,
            "plaintext_key": plaintext,
        }

    async def list_api_keys(self, user_id: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, user_id, tenant_id, name, key_prefix, revoked, created_at, expires_at "
            "FROM api_keys WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    async def revoke_api_key(self, key_id: str, user_id: str) -> None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT id, user_id FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()
        if row is None or row["user_id"] != user_id:
            raise ValueError(f"Not found: {key_id}")
        self._conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        self._conn.commit()

    async def verify_api_key(self, plaintext: str) -> Optional[dict[str, Any]]:
        assert self._conn is not None
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        row = self._conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        if row is None:
            return None
        r = self._row(row)
        if r["revoked"]:
            return None
        if r["expires_at"] is not None:
            exp = datetime.fromisoformat(r["expires_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp:
                return None
        return {"user_id": r["user_id"], "tenant_id": r["tenant_id"]}
