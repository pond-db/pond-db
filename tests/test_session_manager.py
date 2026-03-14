"""Tests for the SessionManager library class.

Defines expected behavior for the ponddb.session_manager module.
Tests import ponddb.session_manager — they will fail with ImportError
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
    from ponddb.session_manager import SessionManager

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
    from ponddb.session_manager import SessionStatus

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
    from ponddb.session_manager import QueryError

    with pytest.raises(QueryError):
        manager.execute_query(session_id, "SELECT FROM WHERE GARBAGE")


def test_execute_query_unknown_table_raises(manager, session_id: str) -> None:
    from ponddb.session_manager import QueryError

    with pytest.raises(QueryError):
        manager.execute_query(session_id, "SELECT * FROM does_not_exist")


def test_execute_query_empty_sql_raises(manager, session_id: str) -> None:
    from ponddb.session_manager import QueryError

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
    from ponddb.session_manager import QueryError

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
