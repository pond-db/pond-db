"""SessionManager — manages DuckDB connections per session."""

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import duckdb


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DESTROYED = "DESTROYED"


class QueryError(Exception):
    """Raised when DuckDB rejects a query."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    rowcount: int
    elapsed_ms: float


@dataclass
class _Session:
    session_id: str
    status: SessionStatus
    created_at: datetime
    last_active: datetime
    namespace: str
    conn: Optional[duckdb.DuckDBPyConnection]


def _drive(coro) -> None:
    """Drive a coroutine that contains no real awaits to completion.

    MetadataStore methods use synchronous sqlite3 internally, so they
    complete on the first send() without needing an event loop.
    """
    try:
        coro.send(None)
    except StopIteration:
        pass


class SessionManager:
    """In-process session manager for DuckDB connections."""

    def __init__(
        self,
        idle_timeout: Optional[float] = None,
        store=None,  # Optional[MetadataStore]
    ) -> None:
        if idle_timeout is not None:
            self.idle_timeout: float = idle_timeout
        else:
            self.idle_timeout = int(os.environ.get("POND_IDLE_TIMEOUT", 300))
        self._sessions: dict[str, _Session] = {}
        self.store = store

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def create_session(self, namespace: str = "default") -> str:
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        conn = duckdb.connect(":memory:")
        self._sessions[sid] = _Session(
            session_id=sid,
            status=SessionStatus.ACTIVE,
            created_at=now,
            last_active=now,
            namespace=namespace,
            conn=conn,
        )
        if self.store is not None:
            _drive(self.store.save_session(sid, namespace, "ACTIVE", now, now))
        return sid

    def destroy_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        session = self._sessions.pop(session_id)
        if session.conn is not None:
            try:
                session.conn.close()
            except Exception:
                pass
        if self.store is not None:
            _drive(self.store.delete_session(session_id))

    def get_session(self, session_id: str) -> dict:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        s = self._sessions[session_id]
        return {
            "session_id": s.session_id,
            "status": s.status,
            "created_at": s.created_at.isoformat(),
            "last_active": s.last_active.isoformat(),
            "namespace": s.namespace,
        }

    def list_sessions(self, namespace: Optional[str] = None) -> list[dict]:
        sessions = [self.get_session(sid) for sid in self._sessions]
        if namespace is not None:
            sessions = [s for s in sessions if s["namespace"] == namespace]
        return sessions

    def suspend_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        session = self._sessions[session_id]
        if session.status == SessionStatus.SUSPENDED:
            raise ValueError(f"Session already suspended: {session_id}")
        if session.conn is not None:
            try:
                session.conn.close()
            except Exception:
                pass
            session.conn = None
        session.status = SessionStatus.SUSPENDED
        if self.store is not None:
            _drive(self.store.save_session(
                session_id, session.namespace, "SUSPENDED",
                session.created_at, session.last_active,
            ))

    def resume_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        session = self._sessions[session_id]
        if session.status == SessionStatus.ACTIVE:
            raise ValueError(f"Session already active: {session_id}")
        session.conn = duckdb.connect(":memory:")
        session.status = SessionStatus.ACTIVE
        now = datetime.now(timezone.utc)
        session.last_active = now
        if self.store is not None:
            _drive(self.store.save_session(
                session_id, session.namespace, "ACTIVE",
                session.created_at, now,
            ))

    async def load_from_store(self) -> None:
        """Repopulate in-memory state from the MetadataStore."""
        if self.store is None:
            return
        rows = await self.store.load_sessions()
        for row in rows:
            sid = row["session_id"]
            state = row["state"]
            status = SessionStatus(state)
            created_at = datetime.fromisoformat(row["created_at"])
            last_active = datetime.fromisoformat(row["last_active"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            conn = duckdb.connect(":memory:") if status == SessionStatus.ACTIVE else None
            self._sessions[sid] = _Session(
                session_id=sid,
                status=status,
                created_at=created_at,
                last_active=last_active,
                namespace=row["namespace"],
                conn=conn,
            )

    def execute_query(self, session_id: str, sql: str) -> QueryResult:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        if not sql or not sql.strip():
            raise ValueError("SQL must not be empty")

        session = self._sessions[session_id]

        # Transparent resume for suspended sessions
        if session.status == SessionStatus.SUSPENDED:
            self.resume_session(session_id)

        start = time.perf_counter()
        try:
            rel = session.conn.execute(sql)
            session.last_active = datetime.now(timezone.utc)
            if rel is None:
                return QueryResult(columns=[], rows=[], rowcount=0,
                                   elapsed_ms=(time.perf_counter() - start) * 1000)
            columns = [desc[0] for desc in rel.description] if rel.description else []
            raw_rows = rel.fetchall()
            rows = [list(row) for row in raw_rows]
            elapsed_ms = (time.perf_counter() - start) * 1000
            return QueryResult(columns=columns, rows=rows,
                               rowcount=len(rows), elapsed_ms=elapsed_ms)
        except duckdb.Error as exc:
            raise QueryError(str(exc)) from exc

    async def run_watchdog_once(self) -> None:
        """Suspend all ACTIVE sessions that have been idle longer than idle_timeout."""
        now = datetime.now(timezone.utc)
        to_suspend = [
            sid for sid, s in self._sessions.items()
            if s.status == SessionStatus.ACTIVE
            and (now - s.last_active).total_seconds() > self.idle_timeout
        ]
        for sid in to_suspend:
            if sid in self._sessions:
                try:
                    self.suspend_session(sid)
                except (KeyError, ValueError):
                    pass

    async def start_watchdog(self, poll_interval: float = 60.0) -> None:
        """Async loop: repeatedly run watchdog until cancelled."""
        while True:
            await self.run_watchdog_once()
            await asyncio.sleep(poll_interval)
