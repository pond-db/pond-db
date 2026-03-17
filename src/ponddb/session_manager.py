"""SessionManager — manages DuckDB connections per session."""

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

import duckdb

import ponddb.subprocess_runner as _subprocess_runner
from ponddb.sql_sandbox import BlockedSqlError, check_sql


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DESTROYED = "DESTROYED"


class QueryError(Exception):
    """Raised when DuckDB rejects a query."""


class WorkgroupAccessError(Exception):
    """Raised when a caller's workgroup_id does not match the session's workgroup_id."""


class WorkgroupQuotaExceeded(Exception):
    """Raised when a workgroup has reached its max_concurrent_sessions limit."""


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
    workgroup_id: str = "default"
    suspended_at: Optional[datetime] = None


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
        dataset_manager=None,  # Optional[DatasetManager]
    ) -> None:
        if idle_timeout is not None:
            self.idle_timeout: float = idle_timeout
        else:
            self.idle_timeout = int(os.environ.get("POND_IDLE_TIMEOUT", 300))
        self._sessions: dict[str, _Session] = {}
        self.store = store
        self.dataset_manager = dataset_manager

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def _create_hardened_connection(self) -> duckdb.DuckDBPyConnection:
        """Create a DuckDB connection with sandbox hardening applied.

        Order matters:
        1. Create connection (external access ON for dataset registration)
        2. Register datasets from DatasetManager (needs file I/O)
        3. Disable external access and lock configuration
        """
        conn = duckdb.connect(":memory:")
        memory_limit = os.environ.get("POND_SESSION_MEMORY_LIMIT", "2GB")
        threads = int(os.environ.get("POND_SESSION_THREADS", "4"))
        conn.execute(f"SET memory_limit = '{memory_limit}'")
        conn.execute(f"SET threads = {threads}")
        # Register datasets before disabling external access
        if self.dataset_manager is not None:
            self.dataset_manager.register_in_session(conn)
        conn.execute("SET enable_external_access = false")
        conn.execute("SET lock_configuration = true")
        return conn

    def _active_count_for_workgroup(self, workgroup_id: str) -> int:
        """Count ACTIVE sessions in a workgroup."""
        return sum(
            1 for s in self._sessions.values()
            if s.workgroup_id == workgroup_id and s.status == SessionStatus.ACTIVE
        )

    def _find_suspended_in_workgroup(self, workgroup_id: str) -> Optional[str]:
        """Find a SUSPENDED session in the workgroup, if any."""
        for sid, s in self._sessions.items():
            if s.workgroup_id == workgroup_id and s.status == SessionStatus.SUSPENDED:
                return sid
        return None

    def create_session(
        self,
        namespace: str = "default",
        workgroup_id: str = "default",
        max_concurrent_sessions: Optional[int] = None,
    ) -> str:
        """Create a new session, enforcing workgroup quota if set.

        If max_concurrent_sessions is set and the workgroup is at capacity:
          1. Try to resume a suspended session instead of rejecting.
          2. If no suspended sessions, raise WorkgroupQuotaExceeded.
        """
        # Quota enforcement for non-default workgroups
        if max_concurrent_sessions is not None and workgroup_id != "default":
            active = self._active_count_for_workgroup(workgroup_id)
            if active >= max_concurrent_sessions:
                # Try to resume a suspended session instead of rejecting
                suspended_sid = self._find_suspended_in_workgroup(workgroup_id)
                if suspended_sid is not None:
                    self.resume_session(suspended_sid)
                    logger.info(
                        "Quota hit for %s — resumed suspended session %s",
                        workgroup_id, suspended_sid[:8],
                    )
                    return suspended_sid
                raise WorkgroupQuotaExceeded(
                    f"Workgroup '{workgroup_id}' at max concurrent sessions "
                    f"({max_concurrent_sessions}). Terminate idle sessions or try later."
                )

        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        conn = self._create_hardened_connection()
        self._sessions[sid] = _Session(
            session_id=sid,
            status=SessionStatus.ACTIVE,
            created_at=now,
            last_active=now,
            namespace=namespace,
            conn=conn,
            workgroup_id=workgroup_id,
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
            "status": s.status.value,
            "created_at": s.created_at.isoformat(),
            "last_active": s.last_active.isoformat(),
            "namespace": s.namespace,
            "workgroup_id": s.workgroup_id,
        }

    def list_sessions(
        self,
        namespace: Optional[str] = None,
        workgroup_id: Optional[str] = None,
    ) -> list[dict]:
        sessions = [self.get_session(sid) for sid in self._sessions]
        if namespace is not None:
            sessions = [s for s in sessions if s["namespace"] == namespace]
        if workgroup_id is not None:
            sessions = [s for s in sessions if s["workgroup_id"] == workgroup_id]
        return sessions

    def check_workgroup_access(
        self, session_id: str, caller_workgroup_id: Optional[str]
    ) -> None:
        """Raise WorkgroupAccessError if caller's workgroup doesn't match session's.

        Passing None as caller_workgroup_id skips the check entirely (backwards compat).
        """
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        if caller_workgroup_id is None:
            return
        session_wg = self._sessions[session_id].workgroup_id
        if session_wg != caller_workgroup_id:
            raise WorkgroupAccessError(
                f"Access denied: session belongs to workgroup '{session_wg}', "
                f"caller asserts workgroup '{caller_workgroup_id}'"
            )

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
        session.suspended_at = datetime.now(timezone.utc)
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
        session.conn = self._create_hardened_connection()
        session.status = SessionStatus.ACTIVE
        session.suspended_at = None
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
            conn = self._create_hardened_connection() if status == SessionStatus.ACTIVE else None
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

        # Sandbox check — raises BlockedSqlError before touching DuckDB
        check_sql(sql)

        # Transparent resume for suspended sessions
        if session.status == SessionStatus.SUSPENDED:
            self.resume_session(session_id)

        # Subprocess isolation path
        if os.environ.get("POND_SUBPROCESS_ISOLATION", "").lower() == "true":
            return _subprocess_runner.run_query_isolated(sql)

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

    async def run_watchdog_once(self) -> list[str]:
        """Suspend ACTIVE sessions idle longer than idle_timeout. Returns suspended IDs."""
        now = datetime.now(timezone.utc)
        to_suspend = [
            sid for sid, s in self._sessions.items()
            if s.status == SessionStatus.ACTIVE
            and (now - s.last_active).total_seconds() > self.idle_timeout
        ]
        suspended: list[str] = []
        for sid in to_suspend:
            if sid in self._sessions:
                try:
                    self.suspend_session(sid)
                    suspended.append(sid)
                    logger.info("Watchdog suspended idle session %s", sid[:8])
                except (KeyError, ValueError):
                    pass
        return suspended

    async def run_reaper_once(self, max_suspend_age: float = 3600.0) -> list[str]:
        """Destroy sessions suspended longer than max_suspend_age seconds.

        Frees resources for sessions that have been sitting suspended too long.
        Returns list of destroyed session IDs.
        """
        now = datetime.now(timezone.utc)
        to_destroy = [
            sid for sid, s in self._sessions.items()
            if s.status == SessionStatus.SUSPENDED
            and s.suspended_at is not None
            and (now - s.suspended_at).total_seconds() > max_suspend_age
        ]
        destroyed: list[str] = []
        for sid in to_destroy:
            if sid in self._sessions:
                try:
                    self.destroy_session(sid)
                    destroyed.append(sid)
                    logger.info("Reaper destroyed stale session %s", sid[:8])
                except KeyError:
                    pass
        return destroyed

    async def start_watchdog(self, poll_interval: float = 60.0) -> None:
        """Async loop: suspend idle sessions and destroy stale suspended ones."""
        max_suspend_age = float(os.environ.get("POND_MAX_SUSPEND_AGE", "3600"))
        while True:
            try:
                await self.run_watchdog_once()
                await self.run_reaper_once(max_suspend_age)
            except Exception:
                logger.exception("Watchdog/reaper error")
            await asyncio.sleep(poll_interval)
