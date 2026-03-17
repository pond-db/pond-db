"""Shared pytest fixtures and hooks for the PondDB test suite."""

import gc

import pytest


@pytest.fixture(autouse=True)
def _cleanup_global_manager_sessions() -> None:
    """Destroy sessions from the global app._manager after each test.

    Many tests create DuckDB sessions via TestClient(app) POST /session calls.
    Those sessions accumulate in the module-level _manager in app.py across all
    tests in a process, each holding ~2 GB of DuckDB virtual address space.
    After 2,000+ tests the committed virtual address space exceeds the kernel's
    CommitLimit, causing os.fork() in subprocess_runner tests to fail with ENOMEM.

    This fixture cleans up stale sessions after every test and forces a GC cycle
    to help Python release the underlying DuckDB connection objects.
    """
    yield  # test runs here

    try:
        import ponddb.app as _app_module

        manager = getattr(_app_module, "_manager", None)
        if manager is not None:
            for session_info in list(manager.list_sessions()):
                try:
                    manager.destroy_session(session_info["session_id"])
                except Exception:
                    pass
    except Exception:
        pass

    gc.collect()


@pytest.fixture(autouse=True)
def _prepopulate_sandbox_test(request, monkeypatch) -> None:
    """Pre-create sandbox_test table in every session for test_session_sandbox tests.

    The test_legitimate_sql_returns_200 parametrized tests include INSERT, SELECT,
    and DROP on sandbox_test, but each parametrized invocation gets a fresh session.
    Pre-creating the table ensures these SQL statements succeed with HTTP 200.
    """
    if "test_session_sandbox" not in request.fspath.basename:
        return

    # Don't pre-create sandbox_test when the test itself is creating it
    if "CREATE TABLE sandbox_test" in str(
        request.node.callspec.params.get("sql", "") if hasattr(request.node, "callspec") else ""
    ):
        return

    from ponddb.engine import session_manager as sm_module

    original_create = sm_module.SessionManager.create_session

    def patched_create(
        self: sm_module.SessionManager,
        namespace: str = "default",
        workgroup_id: str = "default",
    ) -> str:
        sid = original_create(self, namespace=namespace, workgroup_id=workgroup_id)
        session = self._sessions.get(sid)
        if session is not None and session.conn is not None:
            try:
                session.conn.execute(
                    "CREATE TABLE IF NOT EXISTS sandbox_test (id INTEGER, val TEXT)"
                )
            except Exception:
                pass
        return sid

    monkeypatch.setattr(sm_module.SessionManager, "create_session", patched_create)
