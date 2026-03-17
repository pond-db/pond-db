"""Tests for the SessionManager library class.

Defines expected behavior for the ponddb.session_manager module.
Tests import ponddb.engine.session_manager — they will fail with ImportError
until the module is implemented.

SessionManager manages DuckDB connections per session:
  - create_session()  → session_id (str)
  - destroy_session(session_id) → None (raises KeyError if unknown)
  - execute_query(session_id, sql) → QueryResult
  - list_sessions() → list[dict]
  - session_count property
"""

import pytest


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    """Fresh SessionManager instance for each test."""
    from ponddb.engine.session_manager import SessionManager

    mgr = SessionManager()
    yield mgr
    # best-effort teardown — destroy any remaining sessions
    for s in mgr.list_sessions():
        try:
            mgr.destroy_session(s["session_id"])
        except Exception:
            pass


@pytest.fixture
def session_id(manager) -> str:
    return manager.create_session()


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


def test_create_session_returns_string(manager) -> None:
    sid = manager.create_session()
    assert isinstance(sid, str)
    assert len(sid) > 0


def test_create_session_ids_are_unique(manager) -> None:
    ids = {manager.create_session() for _ in range(10)}
    assert len(ids) == 10


def test_session_count_increments(manager) -> None:
    assert manager.session_count == 0
    manager.create_session()
    assert manager.session_count == 1
    manager.create_session()
    assert manager.session_count == 2


def test_new_session_is_active(manager) -> None:
    from ponddb.engine.session_manager import SessionStatus

    sid = manager.create_session()
    info = manager.get_session(sid)
    assert info["status"] == SessionStatus.ACTIVE


# ---------------------------------------------------------------------------
# destroy_session
# ---------------------------------------------------------------------------


def test_destroy_session_decrements_count(manager, session_id: str) -> None:
    assert manager.session_count == 1
    manager.destroy_session(session_id)
    assert manager.session_count == 0


def test_destroy_unknown_session_raises_key_error(manager) -> None:
    with pytest.raises(KeyError):
        manager.destroy_session("ghost-session-xyz")


def test_destroy_session_twice_raises_key_error(manager, session_id: str) -> None:
    manager.destroy_session(session_id)
    with pytest.raises(KeyError):
        manager.destroy_session(session_id)


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


def test_get_session_returns_dict(manager, session_id: str) -> None:
    info = manager.get_session(session_id)
    assert isinstance(info, dict)


def test_get_session_has_required_keys(manager, session_id: str) -> None:
    info = manager.get_session(session_id)
    assert "session_id" in info
    assert "status" in info
    assert "created_at" in info


def test_get_session_id_matches(manager, session_id: str) -> None:
    info = manager.get_session(session_id)
    assert info["session_id"] == session_id


def test_get_unknown_session_raises_key_error(manager) -> None:
    with pytest.raises(KeyError):
        manager.get_session("no-such-session")


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_empty_initially(manager) -> None:
    assert manager.list_sessions() == []


def test_list_sessions_returns_list_of_dicts(manager) -> None:
    manager.create_session()
    sessions = manager.list_sessions()
    assert isinstance(sessions, list)
    assert all(isinstance(s, dict) for s in sessions)


def test_list_sessions_count_matches(manager) -> None:
    manager.create_session()
    manager.create_session()
    assert len(manager.list_sessions()) == 2


def test_list_sessions_excludes_destroyed(manager, session_id: str) -> None:
    manager.destroy_session(session_id)
    ids = [s["session_id"] for s in manager.list_sessions()]
    assert session_id not in ids


# ---------------------------------------------------------------------------
# execute_query — happy path
# ---------------------------------------------------------------------------


def test_execute_query_returns_result_object(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS n")
    assert result is not None


def test_execute_query_result_has_columns(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS n")
    assert hasattr(result, "columns")
    assert isinstance(result.columns, list)
    assert "n" in result.columns


def test_execute_query_result_has_rows(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS n")
    assert hasattr(result, "rows")
    assert isinstance(result.rows, list)


def test_execute_query_result_has_rowcount(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS n")
    assert hasattr(result, "rowcount")
    assert result.rowcount == 1


def test_execute_query_result_has_elapsed_ms(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS n")
    assert hasattr(result, "elapsed_ms")
    assert result.elapsed_ms >= 0


def test_execute_query_simple_value(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 42 AS answer")
    assert result.rows == [[42]]


def test_execute_query_multiple_rows(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT unnest([1, 2, 3]) AS n")
    assert result.rowcount == 3
    assert len(result.rows) == 3


def test_execute_query_multiple_columns(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 'hello' AS word, 7 AS num")
    assert set(result.columns) == {"word", "num"}


def test_execute_query_empty_result(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS n WHERE 1 = 0")
    assert result.rowcount == 0
    assert result.rows == []


def test_execute_query_create_table(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "CREATE TABLE t (id INTEGER)")
    assert result.rows == []


def test_execute_query_insert(manager, session_id: str) -> None:
    manager.execute_query(session_id, "CREATE TABLE t (id INTEGER)")
    result = manager.execute_query(session_id, "INSERT INTO t VALUES (1)")
    # INSERT may return empty rows — just assert no exception
    assert result is not None


def test_execute_query_create_insert_select(manager, session_id: str) -> None:
    manager.execute_query(session_id, "CREATE TABLE nums (v INTEGER)")
    manager.execute_query(session_id, "INSERT INTO nums VALUES (10), (20), (30)")
    result = manager.execute_query(session_id, "SELECT SUM(v) AS total FROM nums")
    assert result.rows == [[60]]


def test_execute_query_string_type(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 'ponddb' AS name")
    assert result.rows == [["ponddb"]]


def test_execute_query_null_value(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT NULL AS n")
    assert result.rows == [[None]]


def test_execute_query_boolean_value(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT true AS flag")
    assert result.rows == [[True]]


# ---------------------------------------------------------------------------
# execute_query — error cases
# ---------------------------------------------------------------------------


def test_execute_query_invalid_sql_raises(manager, session_id: str) -> None:
    from ponddb.engine.session_manager import QueryError

    with pytest.raises(QueryError):
        manager.execute_query(session_id, "SELECT FROM WHERE GARBAGE")


def test_execute_query_unknown_table_raises(manager, session_id: str) -> None:
    from ponddb.engine.session_manager import QueryError

    with pytest.raises(QueryError):
        manager.execute_query(session_id, "SELECT * FROM does_not_exist")


def test_execute_query_empty_sql_raises(manager, session_id: str) -> None:
    from ponddb.engine.session_manager import QueryError

    with pytest.raises((QueryError, ValueError)):
        manager.execute_query(session_id, "")


def test_execute_query_unknown_session_raises(manager) -> None:
    with pytest.raises(KeyError):
        manager.execute_query("ghost-session", "SELECT 1")


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


def test_sessions_are_isolated(manager) -> None:
    """Table created in session A must not be visible in session B."""
    from ponddb.engine.session_manager import QueryError

    sid_a = manager.create_session()
    sid_b = manager.create_session()

    manager.execute_query(sid_a, "CREATE TABLE private_a (x INTEGER)")

    with pytest.raises(QueryError):
        manager.execute_query(sid_b, "SELECT * FROM private_a")


def test_data_persists_within_session(manager, session_id: str) -> None:
    manager.execute_query(session_id, "CREATE TABLE c (n INTEGER)")
    manager.execute_query(session_id, "INSERT INTO c VALUES (7)")
    result = manager.execute_query(session_id, "SELECT n FROM c")
    assert result.rows == [[7]]


# ---------------------------------------------------------------------------
# QueryResult contract
# ---------------------------------------------------------------------------


def test_query_result_columns_order_matches_select(manager, session_id: str) -> None:
    """Column order must match the SELECT list."""
    result = manager.execute_query(session_id, "SELECT 1 AS a, 2 AS b, 3 AS c")
    assert result.columns == ["a", "b", "c"]


def test_query_result_rows_are_lists(manager, session_id: str) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS n, 2 AS m")
    for row in result.rows:
        assert isinstance(row, list)


def test_query_result_row_length_matches_column_count(
    manager, session_id: str
) -> None:
    result = manager.execute_query(session_id, "SELECT 1 AS a, 2 AS b, 3 AS c")
    for row in result.rows:
        assert len(row) == len(result.columns)


# ---------------------------------------------------------------------------
# list_sessions — filtering
# ---------------------------------------------------------------------------


def test_list_sessions_filter_by_namespace(manager) -> None:
    manager.create_session(namespace="ns_a")
    manager.create_session(namespace="ns_b")
    ns_a = manager.list_sessions(namespace="ns_a")
    assert len(ns_a) == 1
    assert ns_a[0]["namespace"] == "ns_a"


def test_list_sessions_filter_by_workgroup(manager) -> None:
    manager.create_session(workgroup_id="wg1")
    manager.create_session(workgroup_id="wg2")
    wg1 = manager.list_sessions(workgroup_id="wg1")
    assert len(wg1) == 1
    assert wg1[0]["workgroup_id"] == "wg1"


def test_list_sessions_filter_no_match_returns_empty(manager) -> None:
    manager.create_session(namespace="ns_a")
    result = manager.list_sessions(namespace="ns_nonexistent")
    assert result == []


# ---------------------------------------------------------------------------
# suspend_session / resume_session
# ---------------------------------------------------------------------------


def test_suspend_session_changes_status(manager, session_id: str) -> None:
    from ponddb.engine.session_manager import SessionStatus

    manager.suspend_session(session_id)
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.SUSPENDED


def test_suspend_session_twice_raises(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    with pytest.raises(ValueError):
        manager.suspend_session(session_id)


def test_suspend_unknown_session_raises(manager) -> None:
    with pytest.raises(KeyError):
        manager.suspend_session("no-such-session")


def test_resume_session_changes_status_to_active(manager, session_id: str) -> None:
    from ponddb.engine.session_manager import SessionStatus

    manager.suspend_session(session_id)
    manager.resume_session(session_id)
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.ACTIVE


def test_resume_active_session_raises(manager, session_id: str) -> None:
    with pytest.raises(ValueError):
        manager.resume_session(session_id)


def test_resume_unknown_session_raises(manager) -> None:
    with pytest.raises(KeyError):
        manager.resume_session("no-such-session")


def test_suspend_then_destroy(manager, session_id: str) -> None:
    manager.suspend_session(session_id)
    manager.destroy_session(session_id)
    assert manager.session_count == 0


# ---------------------------------------------------------------------------
# execute_query — transparent resume of suspended session
# ---------------------------------------------------------------------------


def test_execute_query_resumes_suspended_session(manager, session_id: str) -> None:
    from ponddb.engine.session_manager import SessionStatus

    manager.suspend_session(session_id)
    # Query should transparently resume the session
    result = manager.execute_query(session_id, "SELECT 1 AS n")
    assert result.rows == [[1]]
    info = manager.get_session(session_id)
    assert info["status"] == SessionStatus.ACTIVE


# ---------------------------------------------------------------------------
# check_workgroup_access
# ---------------------------------------------------------------------------


def test_check_workgroup_access_passes_matching(manager) -> None:
    sid = manager.create_session(workgroup_id="wg_a")
    # Should not raise
    manager.check_workgroup_access(sid, "wg_a")


def test_check_workgroup_access_raises_on_mismatch(manager) -> None:
    from ponddb.engine.session_manager import WorkgroupAccessError

    sid = manager.create_session(workgroup_id="wg_a")
    with pytest.raises(WorkgroupAccessError):
        manager.check_workgroup_access(sid, "wg_b")


def test_check_workgroup_access_none_skips_check(manager) -> None:
    sid = manager.create_session(workgroup_id="wg_a")
    # None means skip the check — should not raise
    manager.check_workgroup_access(sid, None)


def test_check_workgroup_access_unknown_session_raises(manager) -> None:
    with pytest.raises(KeyError):
        manager.check_workgroup_access("ghost", "wg_a")


# ---------------------------------------------------------------------------
# WorkgroupQuota enforcement
# ---------------------------------------------------------------------------


def test_workgroup_quota_exceeded_raises(manager) -> None:
    from ponddb.engine.session_manager import WorkgroupQuotaExceeded

    manager.create_session(workgroup_id="wg_limited", max_concurrent_sessions=1)
    with pytest.raises(WorkgroupQuotaExceeded):
        manager.create_session(workgroup_id="wg_limited", max_concurrent_sessions=1)


def test_workgroup_quota_resumes_suspended_session(manager) -> None:
    from ponddb.engine.session_manager import SessionStatus

    # Create 2 sessions without limit to build up state
    sid_active = manager.create_session(workgroup_id="wg_x")
    sid_suspended = manager.create_session(workgroup_id="wg_x")
    # Suspend one — now 1 active + 1 suspended in wg_x
    manager.suspend_session(sid_suspended)
    # With limit=1 and 1 active already at quota, should resume the suspended session
    sid_returned = manager.create_session(workgroup_id="wg_x", max_concurrent_sessions=1)
    assert sid_returned == sid_suspended
    info = manager.get_session(sid_suspended)
    assert info["status"] == SessionStatus.ACTIVE
    manager.destroy_session(sid_active)
    manager.destroy_session(sid_suspended)


def test_workgroup_quota_default_workgroup_no_enforcement(manager) -> None:
    """default workgroup should never be quota-enforced."""
    for _ in range(5):
        manager.create_session(workgroup_id="default", max_concurrent_sessions=1)
    assert manager.session_count == 5


def test_workgroup_quota_no_limit_when_none(manager) -> None:
    """max_concurrent_sessions=None means no limit."""
    for _ in range(5):
        manager.create_session(workgroup_id="wg_unlimited", max_concurrent_sessions=None)
    assert manager.session_count == 5


# ---------------------------------------------------------------------------
# run_watchdog_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_suspends_idle_sessions() -> None:
    from ponddb.engine.session_manager import SessionManager, SessionStatus

    mgr = SessionManager(idle_timeout=0)  # 0s → immediately idle
    sid = mgr.create_session()
    suspended = await mgr.run_watchdog_once()
    assert sid in suspended
    info = mgr.get_session(sid)
    assert info["status"] == SessionStatus.SUSPENDED
    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_watchdog_skips_recently_active_sessions() -> None:
    from ponddb.engine.session_manager import SessionManager, SessionStatus

    mgr = SessionManager(idle_timeout=9999)  # very long timeout
    sid = mgr.create_session()
    suspended = await mgr.run_watchdog_once()
    assert sid not in suspended
    info = mgr.get_session(sid)
    assert info["status"] == SessionStatus.ACTIVE
    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_watchdog_returns_list() -> None:
    from ponddb.engine.session_manager import SessionManager

    mgr = SessionManager(idle_timeout=9999)
    result = await mgr.run_watchdog_once()
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# run_reaper_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_destroys_stale_suspended_sessions() -> None:
    from ponddb.engine.session_manager import SessionManager

    mgr = SessionManager(idle_timeout=0)
    sid = mgr.create_session()
    mgr.suspend_session(sid)
    destroyed = await mgr.run_reaper_once(max_suspend_age=0)
    assert sid in destroyed
    assert mgr.session_count == 0


@pytest.mark.asyncio
async def test_reaper_skips_recently_suspended_sessions() -> None:
    from ponddb.engine.session_manager import SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    mgr.suspend_session(sid)
    destroyed = await mgr.run_reaper_once(max_suspend_age=9999)
    assert sid not in destroyed
    assert mgr.session_count == 1
    mgr.destroy_session(sid)


@pytest.mark.asyncio
async def test_reaper_returns_list() -> None:
    from ponddb.engine.session_manager import SessionManager

    mgr = SessionManager()
    result = await mgr.run_reaper_once()
    assert isinstance(result, list)
