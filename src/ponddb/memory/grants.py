# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Grant CRUD operations for memory_grants table."""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_grant(conn: sqlite3.Connection, **kwargs: Any) -> dict[str, Any]:
    """Insert a new memory grant. Returns the grant dict."""
    gid = str(uuid.uuid4())
    now = _now_iso()
    conn.execute(
        """INSERT INTO memory_grants
           (id, grantor_workgroup_id, grantee_agent_id, grantee_workgroup_id,
            memory_type_filter, min_importance, permission,
            valid_from, valid_until, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            gid,
            kwargs["grantor_workgroup_id"],
            kwargs.get("grantee_agent_id"),
            kwargs.get("grantee_workgroup_id"),
            kwargs.get("memory_type_filter"),
            kwargs.get("min_importance", 0.0),
            kwargs["permission"],
            kwargs.get("valid_from", now),
            kwargs.get("valid_until"),
            kwargs["created_by"],
            now,
        ),
    )
    conn.commit()
    return get_grant(conn, gid)


def get_grant(conn: sqlite3.Connection, grant_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM memory_grants WHERE id = ?", (grant_id,)).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def delete_grant(conn: sqlite3.Connection, grant_id: str) -> bool:
    """Hard delete a grant. Returns True if deleted."""
    cursor = conn.execute("DELETE FROM memory_grants WHERE id = ?", (grant_id,))
    conn.commit()
    return cursor.rowcount > 0


def list_grants(
    conn: sqlite3.Connection,
    *,
    grantor_workgroup_id: Optional[str] = None,
    grantee_workgroup_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    where, params = [], []
    if grantor_workgroup_id:
        where.append("grantor_workgroup_id = ?")
        params.append(grantor_workgroup_id)
    if grantee_workgroup_id:
        where.append("grantee_workgroup_id = ?")
        params.append(grantee_workgroup_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(f"SELECT * FROM memory_grants {clause}", params).fetchall()
    return [{k: row[k] for k in row.keys()} for row in rows]
