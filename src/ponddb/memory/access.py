# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Access scope enforcement for agent memory operations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_accessible_workgroups(
    conn: sqlite3.Connection,
    caller_workgroup_id: str,
    caller_agent_id: Optional[str] = None,
    include_grants: bool = True,
    permission_filter: str = "read",
) -> list[dict[str, Any]]:
    """Return list of workgroups the caller can access.

    Always includes the caller's own workgroup.  When *include_grants* is True,
    also includes workgroups where an active grant exists.

    Each entry has keys:
        workgroup_id, grant_id (None for own), type_filter (None = all),
        min_importance (0.0 for own).
    """
    now = _now_iso()
    result: list[dict[str, Any]] = [
        {
            "workgroup_id": caller_workgroup_id,
            "grant_id": None,
            "type_filter": None,
            "min_importance": 0.0,
        }
    ]

    if not include_grants:
        return result

    # Build WHERE clause for grant matching
    where = [
        "(valid_from IS NULL OR valid_from <= ?)",
        "(valid_until IS NULL OR valid_until > ?)",
    ]
    params: list[Any] = [now, now]

    # Permission filter
    if permission_filter == "read":
        where.append("permission IN ('read', 'read_write')")
    elif permission_filter == "write":
        where.append("permission IN ('write', 'read_write')")
    else:
        where.append("permission = ?")
        params.append(permission_filter)

    # Match grantee: workgroup-level OR agent-specific
    grantee_clauses = ["grantee_workgroup_id = ?"]
    params.append(caller_workgroup_id)

    if caller_agent_id:
        grantee_clauses.append("grantee_agent_id = ?")
        params.append(caller_agent_id)

    where.append(f"({' OR '.join(grantee_clauses)})")

    sql = (
        "SELECT id, grantor_workgroup_id, memory_type_filter, min_importance "
        f"FROM memory_grants WHERE {' AND '.join(where)}"
    )
    rows = conn.execute(sql, params).fetchall()

    for row in rows:
        result.append(
            {
                "workgroup_id": row["grantor_workgroup_id"],
                "grant_id": row["id"],
                "type_filter": row["memory_type_filter"],
                "min_importance": row["min_importance"] or 0.0,
            }
        )

    return result


def can_access_memory(
    conn: sqlite3.Connection,
    memory: dict[str, Any],
    caller_workgroup_id: str,
    caller_agent_id: Optional[str] = None,
    permission: str = "read",
) -> bool:
    """Check if caller can access a specific memory."""
    # Own workgroup: always accessible (private check done separately)
    if memory["workgroup_id"] == caller_workgroup_id:
        if memory["access_scope"] == "private":
            return caller_agent_id is not None and memory["agent_id"] == caller_agent_id
        return True

    # Cross-workgroup: need a grant
    accessible = get_accessible_workgroups(
        conn,
        caller_workgroup_id,
        caller_agent_id,
        include_grants=True,
        permission_filter=permission,
    )
    for entry in accessible:
        if entry["workgroup_id"] == memory["workgroup_id"]:
            # Private memories never visible cross-workgroup
            if memory["access_scope"] == "private":
                return False
            # Check type filter
            if entry["type_filter"] and entry["type_filter"] != memory["memory_type"]:
                return False
            # Check importance filter
            if memory["importance"] < entry["min_importance"]:
                return False
            return True
    return False


def can_modify_memory(
    memory: dict[str, Any],
    caller_workgroup_id: str,
    caller_agent_id: Optional[str] = None,
    is_admin: bool = False,
) -> bool:
    """Check if caller can modify (update/delete) a memory."""
    if memory["workgroup_id"] != caller_workgroup_id:
        return False
    if is_admin:
        return True
    return caller_agent_id is not None and memory["agent_id"] == caller_agent_id
