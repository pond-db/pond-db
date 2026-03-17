"""Tests for session lifecycle: namespace, last_active, suspend, resume, idle watchdog.

Expected behavior for SessionManager extensions:
  - create_session(namespace) → namespace stored, returned in get_session()
  - last_active field updated on every execute_query()
  - suspend_session(session_id) → status=SUSPENDED, DuckDB connection closed
  - resume_session(session_id) → status=ACTIVE, fresh DuckDB connection
  - execute_query on SUSPENDED session → transparent auto-resume
  - run_watchdog_once() → suspends sessions idle > idle_timeout
  - start_watchdog(poll_interval) → async loop calling run_watchdog_once()

All tests will FAIL until session_manager.py implements these methods.
"""

import asyncio
import time

import pytest

from ponddb.engine.session_manager import SessionManager, SessionStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    mgr = SessionManager()
    yield mgr
    for s in mgr.list_sessions():
        try:
            mgr.destroy_session(s["session_id"])
        except Exception:
            pass


@pytest.fixture
def session_id(manager) -> str:
    return manager.create_session()


# ---------------------------------------------------------------------------
# Namespace support
# ---------------------------------------------------------------------------


def test_create_session_with_namespace(manager) -> None:
    sid = manager.create_session(namespace="team-a")
    info = manager.get_session(sid)
    assert info["namespace"] == "team-a"


def test_create_session_default_namespace_is_not_none(manager) -> None:
    sid = manager.create_session()
    info = manager.get_session(sid)
    assert "namespace" in info
    assert info["namespace"] is not None


def test_namespace_preserved_across_sessions(manager) -> None:
    sid_a = manager.create_session(namespace="ns-a")
    sid_b = manager.create_session(namespace="ns-b")
    assert manager.get_session(sid_a)["namespace"] == "ns-a"
    assert manager.get_session(sid_b)["namespace"] == "ns-b"


def test_list_sessions_includes_namespace(manager) -> None:
    manager.create_session(namespace="alpha")
    sessions = manager.list_sessions()
    assert all("namespace" in s for s in sessions)


def test_session_namespace_in_list_matches_create(manager) -> None:
    sid = manager.create_session(namespace="beta")
    sessions = manager.list_sessions()
    matched = [s for s in sessions if s["session_id"] == sid]
    assert len(matched) == 1
    assert matched[0]["namespace"] == "beta"


# ---------------------------------------------------------------------------
# last_active tracking
# ---------------------------------------------------------------------------


def test_session_has_last_active_field(manager, session_id: str) -> None:
    info = manager.get_session(session_id)
    assert "last_active" in info


def test_last_active_set_on_creation(manager) -> None:
    sid = manager.create_session()
    info = manager.get_session(sid)
    assert info["last_active"] is not None


def test_last_active_updates_after_query(manager, session_id: str) -> None:
    before = manager.get_session(session_id)["last_active"]
    time.sleep(0.02)
    manager.execute_query(session_id, "SELECT 1")
    after = manager.get_session(session_id)["last_active"]
    assert after > before


def test_last_active_is_parseable_as_datetime(manager, session_id: str) -> None:
    from datetime import datetime

    last_active = manager.get_session(session_id)["last_active"]
    # accept datetime object or ISO-format string
    if isinstance(last_active, str):
        datetime.fromisoformat(last_active)  # must not raise
    else:
        assert isinstance(last_active, datetime)


def test_last_active_monotonically_increases_across_queries(manager, session_id: str) -> None:
    times = []
    for i in range(3):
        time.sleep(0.01)
        manager.execute_query(session_id, f"SELECT {i}")
        times.append(manager.get_session(session_id)["last_active"])
    assert times[0] <= times[1] <= times[2]


# ---------------------------------------------------------------------------
# suspend_session — state changes
# ---------------------------------------------------------------------------


def test_suspend_changes_status_to_suspended(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.SUSPENDED


def test_suspend_unknown_session_raises_key_error(manager) -> None:
    with pytest.raises(KeyError):
        manager.suspend_session("no-such-session")


def test_suspend_destroyed_session_raises_key_error(manager, session_id: str) -> None:
    manager.destroy_session(session_id)
    with pytest.raises(KeyError):
        manager.suspend_session(session_id)


def test_suspended_session_visible_in_list(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    sessions = manager.list_sessions()
    sid_map = {s["session_id"]: s["status"] for s in sessions}
    assert session_id in sid_map
    assert sid_map[session_id] == SessionStatus.SUSPENDED


def test_suspend_idempotent_or_raises_value_error(manager, session_id: str) -> None:
    """Double-suspend must not crash with KeyError or destroy the session."""
    manager.suspend_session(session_id)
    try:
        manager.suspend_session(session_id)
    except (ValueError, RuntimeError):
        pass  # acceptable — but must not be KeyError
    # session must still be reachable
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.SUSPENDED


def test_suspend_count_unchanged(manager) -> None:
    """Suspending does not remove the session from the manager."""
    sid = manager.create_session()
    assert manager.session_count == 1
    manager.suspend_session(sid)
    assert manager.session_count == 1


# ---------------------------------------------------------------------------
# suspend_session — in-memory data is lost
# ---------------------------------------------------------------------------


def test_in_memory_table_lost_after_suspend_resume(manager, session_id: str) -> None:
    """Per design: in-memory tables are LOST on suspend."""
    from ponddb.engine.session_manager import QueryError

    manager.execute_query(session_id, "CREATE TABLE mem_tbl (x INTEGER)")
    manager.execute_query(session_id, "INSERT INTO mem_tbl VALUES (42)")

    manager.suspend_session(session_id)
    manager.resume_session(session_id)  # explicit resume

    with pytest.raises(QueryError):
        manager.execute_query(session_id, "SELECT * FROM mem_tbl")


def test_new_tables_work_after_resume(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    manager.resume_session(session_id)
    manager.execute_query(session_id, "CREATE TABLE fresh (v INTEGER)")
    manager.execute_query(session_id, "INSERT INTO fresh VALUES (7)")
    result = manager.execute_query(session_id, "SELECT v FROM fresh")
    assert result.rows == [[7]]


# ---------------------------------------------------------------------------
# resume_session
# ---------------------------------------------------------------------------


def test_resume_changes_status_to_active(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    manager.resume_session(session_id)
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.ACTIVE


def test_resume_allows_query(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    manager.resume_session(session_id)
    result = manager.execute_query(session_id, "SELECT 99 AS n")
    assert result.rows == [[99]]


def test_resume_unknown_session_raises_key_error(manager) -> None:
    with pytest.raises(KeyError):
        manager.resume_session("ghost-session")


def test_resume_destroyed_session_raises_key_error(manager, session_id: str) -> None:
    manager.destroy_session(session_id)
    with pytest.raises(KeyError):
        manager.resume_session(session_id)


def test_resume_active_session_is_noop_or_raises_value_error(manager, session_id: str) -> None:
    """Resuming an already-active session must not crash with KeyError."""
    try:
        manager.resume_session(session_id)
    except (ValueError, RuntimeError):
        pass  # acceptable
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.ACTIVE


def test_suspend_then_resume_then_destroy(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    manager.resume_session(session_id)
    manager.destroy_session(session_id)
    assert manager.session_count == 0


# ---------------------------------------------------------------------------
# Transparent resume on execute_query
# ---------------------------------------------------------------------------


def test_query_on_suspended_session_auto_resumes(manager, session_id: str) -> None:
    """execute_query on a SUSPENDED session must transparently resume it."""
    manager.suspend_session(session_id)
    result = manager.execute_query(session_id, "SELECT 1 AS n")
    assert result.rows == [[1]]
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.ACTIVE


def test_query_after_auto_resume_can_create_tables(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    manager.execute_query(session_id, "CREATE TABLE post_resume (v INTEGER)")
    manager.execute_query(session_id, "INSERT INTO post_resume VALUES (55)")
    result = manager.execute_query(session_id, "SELECT v FROM post_resume")
    assert result.rows == [[55]]


def test_auto_resume_updates_last_active(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    time.sleep(0.02)
    manager.execute_query(session_id, "SELECT 1")
    info = manager.get_session(session_id)
    # last_active must have been updated during auto-resume / query
    assert info["last_active"] is not None


def test_query_on_destroyed_session_raises_key_error(manager, session_id: str) -> None:
    manager.destroy_session(session_id)
    with pytest.raises(KeyError):
        manager.execute_query(session_id, "SELECT 1")


# ---------------------------------------------------------------------------
# Multiple sessions — mixed lifecycle
# ---------------------------------------------------------------------------


def test_suspend_one_session_does_not_affect_another(manager) -> None:
    sid_a = manager.create_session()
    sid_b = manager.create_session()
    manager.execute_query(sid_a, "CREATE TABLE t (x INTEGER)")
    manager.suspend_session(sid_b)

    # sid_a still active and data intact
    result = manager.execute_query(sid_a, "SELECT COUNT(*) AS c FROM t")
    assert result.rows == [[0]]
    assert manager.get_session(sid_a)["status"] == SessionStatus.ACTIVE
    assert manager.get_session(sid_b)["status"] == SessionStatus.SUSPENDED


def test_destroy_does_not_affect_suspended_sibling(manager) -> None:
    sid_a = manager.create_session()
    sid_b = manager.create_session()
    manager.suspend_session(sid_a)
    manager.destroy_session(sid_b)

    assert manager.session_count == 1
    assert manager.get_session(sid_a)["status"] == SessionStatus.SUSPENDED


# ---------------------------------------------------------------------------
# Idle watchdog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_watchdog_once_suspends_idle_session() -> None:
    """run_watchdog_once() must suspend sessions idle longer than idle_timeout."""
    mgr = SessionManager(idle_timeout=0.05)  # 50 ms
    sid = mgr.create_session()

    await asyncio.sleep(0.15)  # exceed idle_timeout
    await mgr.run_watchdog_once()

    info = mgr.get_session(sid)
    assert info["status"] == SessionStatus.SUSPENDED

    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_run_watchdog_once_skips_recently_active_session() -> None:
    """Watchdog must NOT suspend a session that was active within the timeout."""
    mgr = SessionManager(idle_timeout=10.0)  # generous timeout
    sid = mgr.create_session()
    mgr.execute_query(sid, "SELECT 1")  # refresh last_active

    await mgr.run_watchdog_once()

    info = mgr.get_session(sid)
    assert info["status"] == SessionStatus.ACTIVE

    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_run_watchdog_once_skips_already_suspended_session() -> None:
    """Watchdog must not error on a session already suspended."""
    mgr = SessionManager(idle_timeout=0.05)
    sid = mgr.create_session()
    mgr.suspend_session(sid)  # manually suspend first

    await asyncio.sleep(0.1)
    await mgr.run_watchdog_once()  # must not raise

    info = mgr.get_session(sid)
    assert info["status"] == SessionStatus.SUSPENDED

    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_run_watchdog_once_skips_destroyed_sessions() -> None:
    """Watchdog must not error if a session is destroyed before it runs."""
    mgr = SessionManager(idle_timeout=0.0)
    sid = mgr.create_session()
    mgr.destroy_session(sid)

    await mgr.run_watchdog_once()  # must not raise
    assert mgr.session_count == 0


@pytest.mark.asyncio
async def test_start_watchdog_suspends_idle_session_eventually() -> None:
    """start_watchdog() background task suspends sessions that go idle."""
    mgr = SessionManager(idle_timeout=0.05)
    sid = mgr.create_session()

    task = asyncio.create_task(mgr.start_watchdog(poll_interval=0.05))
    await asyncio.sleep(0.4)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    info = mgr.get_session(sid)
    assert info["status"] == SessionStatus.SUSPENDED

    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_start_watchdog_does_not_suspend_active_session() -> None:
    """start_watchdog() must leave recently-active sessions alone."""
    mgr = SessionManager(idle_timeout=10.0)
    sid = mgr.create_session()

    task = asyncio.create_task(mgr.start_watchdog(poll_interval=0.05))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    info = mgr.get_session(sid)
    assert info["status"] == SessionStatus.ACTIVE

    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_watchdog_default_idle_timeout_is_300s() -> None:
    """Default POND_IDLE_TIMEOUT is 300 seconds."""
    mgr = SessionManager()
    assert mgr.idle_timeout == 300


@pytest.mark.asyncio
async def test_watchdog_idle_timeout_from_env(monkeypatch) -> None:
    """idle_timeout can be set via POND_IDLE_TIMEOUT env var."""

    monkeypatch.setenv("POND_IDLE_TIMEOUT", "42")
    mgr = SessionManager()
    assert mgr.idle_timeout == 42
