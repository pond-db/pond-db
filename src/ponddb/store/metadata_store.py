# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""MetadataStore — SQLite persistence for session state, catalog mounts, and queries.

Uses sqlite3 (synchronous) wrapped in async def so callers can await it,
while SessionManager can also drive the coroutines synchronously via send().
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ponddb.store.query_store import QueryStoreMixin


def _to_iso(dt: datetime) -> str:
    """Convert datetime to ISO string for storage."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class MetadataStore(QueryStoreMixin):
    """Async interface backed by synchronous SQLite via sqlite3."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _create_tables(self) -> None:
        """Create all tables (idempotent). Shared by initialize() and initialize_blocking()."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                namespace   TEXT NOT NULL,
                state       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                last_active TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS catalog_mounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                path        TEXT NOT NULL,
                alias       TEXT NOT NULL,
                mount_type  TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS compute_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                query_hash   TEXT NOT NULL,
                wall_ms      REAL NOT NULL,
                mem_delta_kb REAL NOT NULL,
                timestamp    TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS queries (
                slug        TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                sql         TEXT NOT NULL,
                created_by  TEXT NOT NULL,
                tenant_id   TEXT NOT NULL DEFAULT 'default',
                created_at  TEXT NOT NULL,
                visibility  TEXT NOT NULL DEFAULT 'private'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS query_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace     TEXT NOT NULL,
                tenant_id     TEXT NOT NULL DEFAULT 'default',
                sql           TEXT NOT NULL,
                duration_ms   REAL NOT NULL,
                rows_returned INTEGER NOT NULL,
                status        TEXT NOT NULL,
                error_message TEXT,
                executed_at   TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS invite_tokens (
                token       TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                tenant_id   TEXT NOT NULL,
                role        TEXT NOT NULL DEFAULT 'member',
                status      TEXT NOT NULL DEFAULT 'pending',
                created_by  TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                accepted_at TEXT
            )
        """)
        # Migrations: add columns to existing tables
        for stmt in (
            "ALTER TABLE queries ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'",
            "ALTER TABLE queries ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE query_history ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'",
        ):
            try:
                self._conn.execute(stmt)
                self._conn.commit()
            except Exception:
                pass  # Column already exists
        self._conn.commit()

    async def initialize(self) -> None:
        """Create tables if they don't exist (idempotent)."""
        self._create_tables()

    def initialize_blocking(self) -> None:
        """Synchronous version of initialize() for use at module import time."""
        self._create_tables()

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    async def save_session(
        self,
        session_id: str,
        namespace: str,
        state: str,
        created_at: datetime,
        last_active: datetime,
    ) -> None:
        """Upsert a session row."""
        self._conn.execute(
            """
            INSERT INTO sessions (session_id, namespace, state, created_at, last_active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                namespace   = excluded.namespace,
                state       = excluded.state,
                created_at  = excluded.created_at,
                last_active = excluded.last_active
            """,
            (session_id, namespace, state, _to_iso(created_at), _to_iso(last_active)),
        )
        self._conn.commit()

    async def load_sessions(self) -> list[dict]:
        """Return all non-DESTROYED sessions as dicts."""
        cursor = self._conn.execute(
            "SELECT session_id, namespace, state, created_at, last_active "
            "FROM sessions WHERE state != 'DESTROYED'"
        )
        return [dict(row) for row in cursor.fetchall()]

    async def delete_session(self, session_id: str) -> None:
        """Remove a session row (no-op if not found)."""
        self._conn.execute(
            "DELETE FROM sessions WHERE session_id = ?", (session_id,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Catalog mount operations
    # ------------------------------------------------------------------

    async def save_mount(
        self,
        session_id: str,
        path: str,
        alias: str,
        mount_type: str,
    ) -> None:
        """Insert a catalog mount entry."""
        self._conn.execute(
            """
            INSERT INTO catalog_mounts (session_id, path, alias, mount_type)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, path, alias, mount_type),
        )
        self._conn.commit()

    async def list_mounts(self, session_id: str) -> list[dict]:
        """Return all mounts for a session."""
        cursor = self._conn.execute(
            "SELECT session_id, path, alias, mount_type "
            "FROM catalog_mounts WHERE session_id = ?",
            (session_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    async def delete_mounts(self, session_id: str) -> None:
        """Remove all mounts for a session (no-op if none found)."""
        self._conn.execute(
            "DELETE FROM catalog_mounts WHERE session_id = ?", (session_id,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Compute log operations
    # ------------------------------------------------------------------

    async def log_compute_sample(self, sample) -> None:
        """Insert a ComputeSample into compute_log."""
        self._conn.execute(
            """
            INSERT INTO compute_log (session_id, query_hash, wall_ms, mem_delta_kb, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                sample.session_id,
                sample.query_hash,
                sample.wall_ms,
                sample.mem_delta_kb,
                _to_iso(sample.timestamp),
            ),
        )
        self._conn.commit()

    async def get_compute_samples(
        self, session_id: Optional[str] = None
    ) -> list[dict]:
        """Return compute_log rows, optionally filtered by session_id."""
        if session_id is not None:
            cursor = self._conn.execute(
                "SELECT session_id, query_hash, wall_ms, mem_delta_kb, timestamp "
                "FROM compute_log WHERE session_id = ?",
                (session_id,),
            )
        else:
            cursor = self._conn.execute(
                "SELECT session_id, query_hash, wall_ms, mem_delta_kb, timestamp "
                "FROM compute_log"
            )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Query history operations
    # ------------------------------------------------------------------

    async def log_query_history(
        self,
        namespace: str,
        sql: str,
        duration_ms: float,
        rows_returned: int,
        status: str,
        executed_at: datetime,
        error_message: Optional[str] = None,
        tenant_id: str = "default",
    ) -> None:
        """Append a query execution record to query_history."""
        self._conn.execute(
            """
            INSERT INTO query_history
                (namespace, tenant_id, sql, duration_ms, rows_returned, status, error_message, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (namespace, tenant_id, sql, duration_ms, rows_returned, status, error_message, _to_iso(executed_at)),
        )
        self._conn.commit()

    async def get_query_history(
        self,
        namespace: Optional[str] = None,
        tenant_id: Optional[str] = None,
        status_filter: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return query history rows ordered by executed_at DESC."""
        conditions: list[str] = []
        params: list = []

        if tenant_id is not None:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)
        elif namespace is not None:
            conditions.append("namespace = ?")
            params.append(namespace)
        if status_filter is not None:
            conditions.append("status = ?")
            params.append(status_filter)
        if start is not None:
            conditions.append("executed_at >= ?")
            params.append(_to_iso(start))
        if end is not None:
            conditions.append("executed_at <= ?")
            params.append(_to_iso(end))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])

        cursor = self._conn.execute(
            f"SELECT namespace, tenant_id, sql, duration_ms, rows_returned, status, error_message, executed_at "
            f"FROM query_history {where} "
            f"ORDER BY executed_at DESC "
            f"LIMIT ? OFFSET ?",
            params,
        )
        return [dict(row) for row in cursor.fetchall()]
