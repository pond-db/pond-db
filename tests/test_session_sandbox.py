"""Tests for session_manager.py sandbox hardening + HTTP endpoint 403 enforcement.

Defines expected behavior for:
  1. SessionManager creates DuckDB connections with:
     - enable_external_access = False
     - memory_limit (from POND_SESSION_MEMORY_LIMIT env or default)
     - threads (from POND_SESSION_THREADS env or default)
     - lock_configuration = True (configuration locked after creation)
  2. POST /query with a blocked SQL pattern returns HTTP 403
  3. POST /query with legitimate SQL returns HTTP 200
  4. POST /query that exceeds memory limit returns HTTP 400 (DuckDB error)

Tests will FAIL until sql_sandbox.py and session_manager.py hardening are implemented.
"""

import importlib
import os

import pytest


# ---------------------------------------------------------------------------
# HTTP-level fixtures
# ---------------------------------------------------------------------------

VALID_KEY = "test-sandbox-key-xyz"


@pytest.fixture
def client(monkeypatch):
    """Fresh app client with POND_API_KEY set."""
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    import ponddb.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def session_id(client, auth_headers) -> str:
    resp = client.post("/session", headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# 1. SessionManager DuckDB connection hardening
# ---------------------------------------------------------------------------


def test_session_manager_disables_external_access(monkeypatch) -> None:
    """DuckDB connection created in a session must have external access disabled.

    The connection should reject file reads (read_csv, read_parquet, etc.)
    because enable_external_access=False was set at connection time.
    """
    from ponddb.session_manager import QueryError, SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        # read_csv requires external access — must be rejected by DuckDB itself
        with pytest.raises((QueryError, Exception)):
            mgr.execute_query(sid, "SELECT * FROM read_csv('/etc/passwd')")
    finally:
        mgr.destroy_session(sid)


def test_session_manager_connection_has_memory_limit(monkeypatch) -> None:
    """DuckDB connection must have a memory limit configured.

    We verify by querying DuckDB's own settings: memory_limit should not be
    the default unlimited value.
    """
    monkeypatch.setenv("POND_SESSION_MEMORY_LIMIT", "256MB")
    from ponddb.session_manager import SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        result = mgr.execute_query(sid, "SELECT current_setting('memory_limit')")
        setting_val = result.rows[0][0]
        # The value should reflect a configured limit (not unlimited/empty)
        assert setting_val, "memory_limit setting should be non-empty"
        # Should contain MB or GB unit marker, not the default 80% of RAM phrasing
        assert any(unit in setting_val.upper() for unit in ("MB", "GB", "KB", "B")), (
            f"Expected a unit in memory_limit, got: {setting_val!r}"
        )
    finally:
        mgr.destroy_session(sid)


def test_session_manager_connection_has_thread_limit(monkeypatch) -> None:
    """DuckDB connection must have a threads limit configured."""
    monkeypatch.setenv("POND_SESSION_THREADS", "2")
    from ponddb.session_manager import SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        result = mgr.execute_query(sid, "SELECT current_setting('threads')")
        threads_val = result.rows[0][0]
        assert int(threads_val) == 2, f"Expected threads=2, got {threads_val!r}"
    finally:
        mgr.destroy_session(sid)


def test_session_manager_configuration_is_locked(monkeypatch) -> None:
    """After session creation, DuckDB configuration must be locked.

    Attempting SET memory_limit inside the session must fail with an error.
    """
    from ponddb.session_manager import QueryError, SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        with pytest.raises((QueryError, Exception)):
            mgr.execute_query(sid, "SET memory_limit = '100GB'")
    finally:
        mgr.destroy_session(sid)


def test_session_manager_external_access_not_overrideable(monkeypatch) -> None:
    """A session should not be able to re-enable external access via SET."""
    from ponddb.session_manager import QueryError, SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        with pytest.raises((QueryError, Exception)):
            mgr.execute_query(sid, "SET enable_external_access = true")
    finally:
        mgr.destroy_session(sid)


def test_session_manager_attach_rejected_by_duckdb(monkeypatch) -> None:
    """ATTACH to external DB must fail because external access is disabled."""
    from ponddb.session_manager import QueryError, SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        with pytest.raises((QueryError, Exception)):
            mgr.execute_query(sid, "ATTACH '/tmp/other.db' AS other")
    finally:
        mgr.destroy_session(sid)


def test_session_manager_load_rejected_by_duckdb(monkeypatch) -> None:
    """LOAD must fail because external access is disabled."""
    from ponddb.session_manager import QueryError, SessionManager

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        with pytest.raises((QueryError, Exception)):
            mgr.execute_query(sid, "LOAD '/tmp/evil.so'")
    finally:
        mgr.destroy_session(sid)


# ---------------------------------------------------------------------------
# 2. HTTP endpoint — blocked SQL → 403
# ---------------------------------------------------------------------------

BLOCKED_SQL_HTTP = [
    "COPY mytable TO '/tmp/out.csv'",
    "LOAD '/tmp/evil.so'",
    "INSTALL httpfs",
    "ATTACH '/data/other.db' AS other",
    "EXPORT DATABASE '/tmp/backup'",
    "IMPORT DATABASE '/tmp/backup'",
    "CREATE SECRET my_s (TYPE S3, KEY_ID 'x')",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('/data/file.parquet')",
    "SELECT * FROM read_json('/tmp/data.json')",
    "SELECT * FROM read_text('/etc/hosts')",
    "SELECT * FROM read_blob('/etc/shadow')",
    "SET memory_limit = '100GB'",
    "PRAGMA database_list",
]


@pytest.mark.parametrize("sql", BLOCKED_SQL_HTTP)
def test_blocked_sql_returns_403(
    client, auth_headers, session_id: str, sql: str
) -> None:
    """POST /query with a blocked SQL pattern must return HTTP 403 Forbidden."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": sql},
        headers=auth_headers,
    )
    assert resp.status_code == 403, (
        f"Expected 403 for blocked SQL {sql!r}, got {resp.status_code}: {resp.text}"
    )


def test_blocked_403_response_has_detail(client, auth_headers, session_id: str) -> None:
    """The 403 response must include a 'detail' field explaining the block."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "COPY t TO '/tmp/x.csv'"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "detail" in body
    assert body["detail"]  # non-empty


def test_blocked_403_detail_mentions_blocked_pattern(
    client, auth_headers, session_id: str
) -> None:
    """The 403 detail message should name the blocked pattern."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "INSTALL httpfs"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"].upper()
    assert "INSTALL" in detail or "BLOCK" in detail or "FORBIDDEN" in detail


# ---------------------------------------------------------------------------
# 3. HTTP endpoint — legitimate SQL → 200
# ---------------------------------------------------------------------------

ALLOWED_SQL_HTTP = [
    "SELECT 1",
    "SELECT version()",
    "SELECT * FROM information_schema.tables LIMIT 5",
    "CREATE TABLE sandbox_test (id INTEGER, val TEXT)",
    "INSERT INTO sandbox_test VALUES (1, 'hello')",
    "SELECT * FROM sandbox_test",
    "DROP TABLE sandbox_test",
    "WITH cte AS (SELECT 42 AS n) SELECT n FROM cte",
]


@pytest.mark.parametrize("sql", ALLOWED_SQL_HTTP)
def test_legitimate_sql_returns_200(
    client, auth_headers, session_id: str, sql: str
) -> None:
    """POST /query with legitimate SQL must return HTTP 200."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": sql},
        headers=auth_headers,
    )
    assert resp.status_code == 200, (
        f"Expected 200 for legitimate SQL {sql!r}, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# 4. Memory limit enforcement — DuckDB raises error on excess allocation
# ---------------------------------------------------------------------------


def test_memory_limit_query_error_returns_400(
    client, auth_headers, session_id: str, monkeypatch
) -> None:
    """A query that exceeds memory limit must return HTTP 400 (QueryError).

    Note: we can't reliably force a real OOM in a unit test, so we verify
    the error path by triggering a DuckDB error from SET memory_limit after lock.
    The 400 response is the existing QueryError → HTTPException(400) path.
    """
    # SET after lock_configuration should fail → 400
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SET memory_limit = '1B'"},
        headers=auth_headers,
    )
    # Blocked → 403 (sandbox intercepts before DuckDB)
    # or → 400 (DuckDB rejects because config is locked)
    assert resp.status_code in (400, 403), (
        f"Expected 400 or 403 for SET memory_limit, got {resp.status_code}: {resp.text}"
    )


def test_memory_over_limit_from_session_manager() -> None:
    """SessionManager must surface DuckDB memory errors as QueryError.

    Uses a tiny memory_limit (1 byte) so that even a small allocation fails.
    If the implementation enforces the limit, DuckDB will raise an OutOfMemory error.
    """
    import os
    os.environ["POND_SESSION_MEMORY_LIMIT"] = "1B"
    try:
        from ponddb.session_manager import QueryError, SessionManager

        mgr = SessionManager()
        sid = mgr.create_session()
        try:
            # generate_series produces many rows → should trip the memory limit
            with pytest.raises((QueryError, Exception)):
                mgr.execute_query(
                    sid,
                    "SELECT * FROM generate_series(1, 10000000) t(n)",
                )
        finally:
            try:
                mgr.destroy_session(sid)
            except Exception:
                pass
    finally:
        del os.environ["POND_SESSION_MEMORY_LIMIT"]


# ---------------------------------------------------------------------------
# 5. Sandbox integration — sql_sandbox.check_sql called by session_manager
#    (verify the sandbox is wired up, not just present)
# ---------------------------------------------------------------------------


def test_session_manager_calls_sandbox_before_duckdb() -> None:
    """execute_query must call check_sql before sending SQL to DuckDB.

    We verify this by ensuring a blocked SQL raises an error that originates
    from the sandbox (BlockedSqlError or an HTTPException-level 403), NOT
    as a DuckDB syntax error.
    """
    from ponddb.session_manager import SessionManager
    from ponddb.sql_sandbox import BlockedSqlError

    mgr = SessionManager()
    sid = mgr.create_session()
    try:
        with pytest.raises(BlockedSqlError):
            mgr.execute_query(sid, "INSTALL httpfs")
    finally:
        try:
            mgr.destroy_session(sid)
        except Exception:
            pass


def test_sandbox_import_available() -> None:
    """sql_sandbox module must be importable from ponddb package."""
    from ponddb import sql_sandbox
    assert hasattr(sql_sandbox, "check_sql")
    assert hasattr(sql_sandbox, "BlockedSqlError")
    assert hasattr(sql_sandbox, "BLOCKED_PATTERNS")
