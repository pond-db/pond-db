# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Memory search with grant-aware query builder."""

import json
from datetime import datetime, timezone
from typing import Any, Optional

import sqlite3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def search_memories(
    conn: sqlite3.Connection,
    workgroup_id: str,
    *,
    agent_id: Optional[str] = None,
    caller_agent_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    access_scope: Optional[str] = None,
    min_importance: Optional[float] = None,
    min_utility: Optional[float] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    content_contains: Optional[str] = None,
    limit: int = 20,
    granted_workgroups: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Search memories in own workgroup + granted workgroups.

    granted_workgroups: list of dicts with keys:
        workgroup_id, grant_id, type_filter, min_importance
    """
    now = _now_iso()
    results = []

    # 1. Own workgroup
    own = _query_workgroup(
        conn,
        workgroup_id,
        caller_agent_id=caller_agent_id,
        agent_id=agent_id,
        memory_type=memory_type,
        access_scope=access_scope,
        min_importance=min_importance,
        min_utility=min_utility,
        since=since,
        until=until,
        content_contains=content_contains,
        limit=limit,
        now=now,
    )
    results.extend(own)

    # 2. Granted workgroups
    for grant in granted_workgroups or []:
        gtype = grant.get("type_filter") or memory_type
        gimp = max(grant.get("min_importance", 0.0), min_importance or 0.0)
        granted = _query_workgroup(
            conn,
            grant["workgroup_id"],
            caller_agent_id=caller_agent_id,
            agent_id=agent_id,
            memory_type=gtype,
            access_scope=access_scope,
            min_importance=gimp,
            min_utility=min_utility,
            since=since,
            until=until,
            content_contains=content_contains,
            limit=limit,
            now=now,
            exclude_private=True,
        )
        for row in granted:
            row["_grant_id"] = grant.get("grant_id")
            row["_source_workgroup_id"] = grant["workgroup_id"]
        results.extend(granted)

    # Sort by utility DESC, created_at DESC and cap at limit
    results.sort(key=lambda r: (-r.get("utility", 0), r.get("created_at", "")), reverse=False)
    results.sort(key=lambda r: (-r.get("utility", 0),))
    results = results[:limit]

    # Update access_count and last_accessed_at for returned memories
    if results:
        ids = [r["id"] for r in results]
        placeholders = ",".join("?" for _ in ids)
        try:
            conn.execute(
                f"UPDATE agent_memories SET access_count = access_count + 1, "
                f"last_accessed_at = ? WHERE id IN ({placeholders})",
                [now] + ids,
            )
            conn.commit()
        except Exception:
            pass  # Non-fatal — access count is best-effort

    return results


def _query_workgroup(
    conn: sqlite3.Connection,
    workgroup_id: str,
    *,
    caller_agent_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    access_scope: Optional[str] = None,
    min_importance: Optional[float] = None,
    min_utility: Optional[float] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    content_contains: Optional[str] = None,
    limit: int = 20,
    now: Optional[str] = None,
    exclude_private: bool = False,
) -> list[dict[str, Any]]:
    """Query a single workgroup with filters."""
    now = now or _now_iso()
    where = ["workgroup_id = ?", "deleted_at IS NULL"]
    params: list[Any] = [workgroup_id]

    # Exclude expired working memory
    where.append("(expires_at IS NULL OR expires_at > ? OR memory_type != 'working')")
    params.append(now)

    if exclude_private:
        where.append("access_scope != 'private'")
    elif caller_agent_id:
        # Private memories only visible to creating agent
        where.append("(access_scope != 'private' OR agent_id = ?)")
        params.append(caller_agent_id)

    if agent_id:
        where.append("agent_id = ?")
        params.append(agent_id)
    if memory_type:
        where.append("memory_type = ?")
        params.append(memory_type)
    if access_scope:
        where.append("access_scope = ?")
        params.append(access_scope)
    if min_importance is not None:
        where.append("importance >= ?")
        params.append(min_importance)
    if min_utility is not None:
        where.append("utility >= ?")
        params.append(min_utility)
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if until:
        where.append("created_at <= ?")
        params.append(until)
    if content_contains:
        where.append("content LIKE ?")
        params.append(f"%{content_contains}%")

    params.append(limit)
    sql = (
        f"SELECT * FROM agent_memories WHERE {' AND '.join(where)} "
        f"ORDER BY utility DESC, created_at DESC LIMIT ?"
    )
    rows = conn.execute(sql, params).fetchall()
    results = []
    for row in rows:
        d = {k: row[k] for k in row.keys()}
        d["content"] = json.loads(d["content"]) if isinstance(d["content"], str) else d["content"]
        d["linked_memory_ids"] = (
            json.loads(d["linked_memory_ids"])
            if isinstance(d["linked_memory_ids"], str)
            else d["linked_memory_ids"]
        )
        results.append(d)
    return results
