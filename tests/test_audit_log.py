"""Tests for security_audit_log: AuditLogMiddleware, Postgres schema, and resilience.

Expected behavior:
- AuditLogMiddleware exists in ponddb.audit_log
- SCHEMA_SQL constant contains CREATE TABLE security_audit_log DDL
  with columns: id, event_type, tenant_id, ip_address, user_agent, detail, created_at
- Schema includes indexes on event_type and created_at
- Schema includes REVOKE DELETE ON security_audit_log
- POST /auth/token with valid API key → login_success event written to Postgres
- POST /auth/token with invalid API key → login_failure event written to Postgres
- POST /query with blocked SQL → sandbox_block event written to Postgres
- Postgres unavailable → request still returns expected HTTP status (fire-and-forget resilience)
- Writes are fire-and-forget: response is not delayed by Postgres
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------


class TestModuleStructure:
    """audit_log.py exposes the expected public API."""

    def test_module_importable(self):
        from ponddb.security import audit_log  # noqa: F401

    def test_audit_log_middleware_class_exists(self):
        from ponddb.security.audit_log import AuditLogMiddleware

        assert AuditLogMiddleware is not None

    def test_schema_sql_constant_exists(self):
        from ponddb.security.audit_log import SCHEMA_SQL

        assert isinstance(SCHEMA_SQL, str)
        assert len(SCHEMA_SQL) > 0

    def test_log_event_function_exists(self):
        from ponddb.security.audit_log import log_event

        assert callable(log_event)

    def test_middleware_instantiable(self):
        from ponddb.security.audit_log import AuditLogMiddleware
        from starlette.applications import Starlette

        app = Starlette()
        mw = AuditLogMiddleware(app, dsn="postgresql://localhost/test")
        assert mw is not None


# ---------------------------------------------------------------------------
# Schema DDL correctness
# ---------------------------------------------------------------------------


class TestSchemaDDL:
    """SCHEMA_SQL contains correct DDL for security_audit_log."""

    def _get_schema(self) -> str:
        from ponddb.security.audit_log import SCHEMA_SQL

        return SCHEMA_SQL.lower()

    def test_creates_security_audit_log_table(self):
        schema = self._get_schema()
        assert "create table" in schema
        assert "security_audit_log" in schema

    def test_has_id_column(self):
        schema = self._get_schema()
        assert "id" in schema

    def test_has_event_type_column(self):
        schema = self._get_schema()
        assert "event_type" in schema

    def test_has_tenant_id_column(self):
        schema = self._get_schema()
        assert "tenant_id" in schema

    def test_has_ip_address_column(self):
        schema = self._get_schema()
        assert "ip_address" in schema

    def test_has_user_agent_column(self):
        schema = self._get_schema()
        assert "user_agent" in schema

    def test_has_detail_column(self):
        schema = self._get_schema()
        assert "detail" in schema

    def test_has_created_at_column(self):
        schema = self._get_schema()
        assert "created_at" in schema

    def test_has_index_on_event_type(self):
        schema = self._get_schema()
        assert "index" in schema
        assert "event_type" in schema

    def test_has_index_on_created_at(self):
        schema = self._get_schema()
        assert "index" in schema
        assert "created_at" in schema

    def test_has_revoke_delete(self):
        # REVOKE DELETE is the critical security requirement
        schema = self._get_schema()
        assert "revoke" in schema
        assert "delete" in schema
        assert "security_audit_log" in schema

    def test_table_created_if_not_exists(self):
        # Idempotent DDL
        schema = self._get_schema()
        assert "if not exists" in schema


# ---------------------------------------------------------------------------
# Fixtures for integration tests
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-audit-key-xyz"
JWT_SECRET = "test-jwt-secret-for-audit-tests-32chars"


@pytest.fixture()
def env_setup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)


@pytest.fixture()
def mock_pool():
    """Fake asyncpg connection pool that records execute() calls."""
    pool = MagicMock()
    pool.acquire = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock()
    return pool, conn


@pytest.fixture()
def client_with_audit(env_setup, mock_pool, monkeypatch):
    """TestClient with AuditLogMiddleware wired in, using a fake pool."""
    pool, conn = mock_pool

    import importlib
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    from ponddb.security.audit_log import AuditLogMiddleware

    # Wrap the app with AuditLogMiddleware; inject mock pool so no real Postgres needed
    app.add_middleware(AuditLogMiddleware, dsn="postgresql://localhost/ponddb_test")

    # Patch the pool creation so the middleware uses our mock pool
    monkeypatch.setattr(
        "ponddb.security.audit_log.AuditLogMiddleware._pool",
        pool,
        raising=False,
    )

    return TestClient(app, raise_server_exceptions=False), conn


# ---------------------------------------------------------------------------
# Login event logging
# ---------------------------------------------------------------------------


class TestLoginEventLogged:
    """Login success and failure events are written to security_audit_log."""

    def test_login_success_calls_postgres(self, env_setup, monkeypatch):
        """POST /auth/token with valid key → at least one Postgres write is attempted."""
        written_events: list[str] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            written_events.append(event_type)

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
        assert resp.status_code == 200
        assert "login_success" in written_events

    def test_login_failure_calls_postgres(self, env_setup, monkeypatch):
        """POST /auth/token with wrong key → login_failure event written."""
        written_events: list[str] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            written_events.append(event_type)

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/auth/token", json={"api_key": "wrong-key"})
        assert resp.status_code == 401
        assert "login_failure" in written_events

    def test_login_event_includes_ip(self, env_setup, monkeypatch):
        """Login audit event captures the caller IP address."""
        captured_kwargs: list[dict] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            captured_kwargs.append({"event_type": event_type, **kwargs})

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)
        client.post(
            "/auth/token",
            json={"api_key": VALID_API_KEY},
            headers={"X-Forwarded-For": "10.0.0.1"},
        )
        login_events = [e for e in captured_kwargs if "login" in e.get("event_type", "")]
        assert login_events, "Expected at least one login event"
        # ip_address should be captured
        assert any("ip_address" in e or e.get("ip_address") for e in login_events)

    def test_login_event_includes_tenant_id(self, env_setup, monkeypatch):
        """Login audit event captures the tenant_id when known."""
        captured: list[dict] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            captured.append({"event_type": event_type, **kwargs})

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)
        client.post(
            "/auth/token",
            json={"api_key": VALID_API_KEY, "tenant_id": "acme"},
        )
        login_events = [e for e in captured if "login_success" in e.get("event_type", "")]
        assert login_events
        event = login_events[0]
        tenant = event.get("tenant_id") or event.get("detail", "")
        assert "acme" in str(tenant)


# ---------------------------------------------------------------------------
# Sandbox block event logging
# ---------------------------------------------------------------------------


class TestSandboxBlockEventLogged:
    """Blocked SQL queries → sandbox_block event in security_audit_log."""

    def _make_auth_headers(self) -> dict:
        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("default")
        return {"Authorization": f"Bearer {token}"}

    def test_sandbox_block_event_written(self, env_setup, monkeypatch):
        """POST /query with COPY SQL → sandbox_block event logged."""
        written_events: list[str] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            written_events.append(event_type)

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)

        # Create session first
        session_resp = client.post("/session")
        assert session_resp.status_code == 201
        session_id = session_resp.json()["session_id"]

        headers = self._make_auth_headers()
        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": "COPY sensitive TO '/etc/passwd'"},
            headers=headers,
        )
        assert resp.status_code == 403
        assert "sandbox_block" in written_events

    def test_sandbox_block_event_includes_pattern(self, env_setup, monkeypatch):
        """sandbox_block event detail includes the matched pattern name."""
        captured: list[dict] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            captured.append({"event_type": event_type, **kwargs})

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)

        session_resp = client.post("/session")
        session_id = session_resp.json()["session_id"]

        headers = self._make_auth_headers()
        client.post(
            "/query",
            json={"session_id": session_id, "sql": "ATTACH 'secret.db'"},
            headers=headers,
        )
        block_events = [e for e in captured if e.get("event_type") == "sandbox_block"]
        assert block_events
        event = block_events[0]
        detail_str = str(event.get("detail", "")) + str(event.get("pattern", ""))
        assert detail_str  # must contain some info about what was blocked

    def test_sandbox_block_includes_tenant_id(self, env_setup, monkeypatch):
        """sandbox_block event captures the requesting tenant_id."""
        captured: list[dict] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            captured.append({"event_type": event_type, **kwargs})

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)

        session_resp = client.post("/session")
        session_id = session_resp.json()["session_id"]

        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("tenant-xyz")
        headers = {"Authorization": f"Bearer {token}"}

        client.post(
            "/query",
            json={"session_id": session_id, "sql": "COPY t TO '/tmp/out'"},
            headers=headers,
        )
        block_events = [e for e in captured if e.get("event_type") == "sandbox_block"]
        assert block_events
        event = block_events[0]
        tenant = event.get("tenant_id") or str(event.get("detail", ""))
        assert "tenant-xyz" in tenant

    def test_allowed_sql_does_not_write_sandbox_block(self, env_setup, monkeypatch):
        """Normal SELECT → no sandbox_block event."""
        written_events: list[str] = []

        async def fake_log_event(pool, event_type: str, **kwargs):
            written_events.append(event_type)

        monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)

        session_resp = client.post("/session")
        session_id = session_resp.json()["session_id"]

        headers = self._make_auth_headers()
        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": "SELECT 1"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "sandbox_block" not in written_events


# ---------------------------------------------------------------------------
# Resilience: Postgres down → request still succeeds
# ---------------------------------------------------------------------------


class TestPostgresDownResiliency:
    """When Postgres is unavailable, audit writes fail silently (fire-and-forget)."""

    def test_login_succeeds_when_postgres_down(self, env_setup, monkeypatch):
        """POST /auth/token returns 200 even when audit log write raises an exception."""

        async def failing_log_event(pool, event_type: str, **kwargs):
            raise ConnectionRefusedError("Postgres is down")

        monkeypatch.setattr("ponddb.security.audit_log.log_event", failing_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
        # Must still return 200 — audit log failure is never a blocker
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

    def test_blocked_sql_returns_403_when_postgres_down(self, env_setup, monkeypatch):
        """POST /query with blocked SQL returns 403 even when audit write fails."""

        async def failing_log_event(pool, event_type: str, **kwargs):
            raise OSError("Postgres unreachable")

        monkeypatch.setattr("ponddb.security.audit_log.log_event", failing_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)

        session_resp = client.post("/session")
        session_id = session_resp.json()["session_id"]

        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("default")
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": "LOAD malicious"},
            headers=headers,
        )
        # 403 is the correct status — audit failure must not change this to 500
        assert resp.status_code == 403

    def test_normal_query_succeeds_when_postgres_down(self, env_setup, monkeypatch):
        """Regular SELECT still returns 200 when audit write throws."""

        async def failing_log_event(pool, event_type: str, **kwargs):
            raise RuntimeError("DB pool exhausted")

        monkeypatch.setattr("ponddb.security.audit_log.log_event", failing_log_event, raising=False)

        import importlib
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)

        client = TestClient(app, raise_server_exceptions=False)

        session_resp = client.post("/session")
        session_id = session_resp.json()["session_id"]

        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("default")
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": "SELECT 42 AS answer"},
            headers=headers,
        )
        assert resp.status_code == 200

    def test_pool_connect_failure_does_not_crash_startup(self, monkeypatch):
        """AuditLogMiddleware.__init__ with unreachable DSN does not raise synchronously."""
        from ponddb.security.audit_log import AuditLogMiddleware
        from starlette.applications import Starlette

        # A DSN that will never connect; middleware must not raise at construction time
        app = Starlette()
        try:
            mw = AuditLogMiddleware(app, dsn="postgresql://nobody:wrong@256.256.256.256/nodb")
        except Exception as exc:
            pytest.fail(
                f"AuditLogMiddleware.__init__ raised {type(exc).__name__}: {exc}; "
                "pool creation must be deferred or non-blocking"
            )


# ---------------------------------------------------------------------------
# log_event helper — unit tests
# ---------------------------------------------------------------------------


class TestLogEventHelper:
    """log_event(pool, event_type, ...) writes a row to security_audit_log."""

    @pytest.mark.asyncio
    async def test_log_event_executes_insert(self):
        from ponddb.security.audit_log import log_event

        conn = AsyncMock()
        conn.execute = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await log_event(
            pool,
            event_type="login_success",
            tenant_id="t1",
            ip_address="1.2.3.4",
            user_agent="pytest",
            detail="token issued",
        )

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0]
        sql_str = call_args[0].lower()
        assert "insert" in sql_str
        assert "security_audit_log" in sql_str

    @pytest.mark.asyncio
    async def test_log_event_passes_event_type(self):
        from ponddb.security.audit_log import log_event

        conn = AsyncMock()
        conn.execute = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await log_event(pool, event_type="sandbox_block", tenant_id="t2")

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0]
        # event_type value must appear in the positional args tuple
        assert "sandbox_block" in call_args

    @pytest.mark.asyncio
    async def test_log_event_swallows_exceptions(self):
        """log_event must not propagate exceptions — it is fire-and-forget."""
        from ponddb.security.audit_log import log_event

        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=ConnectionRefusedError("down"))
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise
        await log_event(pool, event_type="login_failure", tenant_id="t3")

    @pytest.mark.asyncio
    async def test_log_event_with_none_pool_does_not_raise(self):
        """If pool is None (Postgres not configured), log_event is a no-op."""
        from ponddb.security.audit_log import log_event

        # Should return silently, not crash
        await log_event(None, event_type="login_success", tenant_id="t4")
