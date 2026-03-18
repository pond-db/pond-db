# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Async, non-blocking access log writer for memory operations."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_access_log(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    workgroup_id: str,
    action: str,
    memory_ids: Optional[list[str]] = None,
    query_text: Optional[str] = None,
    result_count: Optional[int] = None,
    grant_id: Optional[str] = None,
    source_workgroup_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    latency_ms: Optional[float] = None,
    status: str = "ok",
    error_type: Optional[str] = None,
) -> str:
    """Write an entry to memory_access_log. Returns the log entry id."""
    log_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO memory_access_log
           (id, agent_id, workgroup_id, action, memory_ids, query_text,
            result_count, grant_id, source_workgroup_id, execution_id,
            trace_id, span_id, latency_ms, status, error_type, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            log_id,
            agent_id,
            workgroup_id,
            action,
            json.dumps(memory_ids) if memory_ids else None,
            query_text,
            result_count,
            grant_id,
            source_workgroup_id,
            execution_id,
            trace_id,
            span_id,
            latency_ms,
            status,
            error_type,
            _now_iso(),
        ),
    )
    conn.commit()
    return log_id


def get_access_logs(
    conn: sqlite3.Connection,
    *,
    agent_id: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where, params = [], []
    if agent_id:
        where.append("agent_id = ?")
        params.append(agent_id)
    if action:
        where.append("action = ?")
        params.append(action)
    if since:
        where.append("created_at >= ?")
        params.append(since)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM memory_access_log {clause} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [{k: row[k] for k in row.keys()} for row in rows]


def count_recent_actions(
    conn: sqlite3.Connection,
    *,
    agent_id: Optional[str] = None,
    action: str,
    memory_id: Optional[str] = None,
    window_seconds: int = 60,
) -> int:
    """Count recent actions for rate limiting."""
    since = datetime.now(timezone.utc)
    from datetime import timedelta

    since = (since - timedelta(seconds=window_seconds)).isoformat()
    where = ["action = ?", "created_at >= ?"]
    params: list[Any] = [action, since]
    if agent_id:
        where.append("agent_id = ?")
        params.append(agent_id)
    if memory_id:
        where.append("memory_ids LIKE ?")
        params.append(f"%{memory_id}%")
    row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM memory_access_log WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    return row["cnt"] if row else 0
