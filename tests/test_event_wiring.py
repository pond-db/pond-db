"""Tests for five new audit-log events.

Expected behavior (none of this is wired yet — all assertions FAIL until
the implementation is written):

  pondapi_execute   — logged when POST /pondapi/execute accepts an async submission
  sandbox_blocked   — logged when POST /pondapi/execute rejects blocked SQL
  invite_created    — logged when POST /invites creates a new invite token
  user_provisioned  — logged when POST /invites/{token}/accept succeeds
  workgroup_created — logged when POST /workgroups creates a workgroup

Tests monkeypatch ponddb.audit_log.log_event so no real Postgres is needed.
Tests fail because the route handlers do not yet call log_event.
Implementation note: route handlers should call log_event via the module
  reference (e.g. `await audit_log.log_event(pool, ...)`) so that patching
  ponddb.audit_log.log_event intercepts the call at runtime.
"""

import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JWT_SECRET = "test-jwt-secret-event-wiring-32ch"
ADMIN_API_KEY = "test-admin-key-event-wiring"


# ---------------------------------------------------------------------------
# Auto-use env setup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_API_KEY", ADMIN_API_KEY)


# ---------------------------------------------------------------------------
# captured_events — intercepts every log_event call
# ---------------------------------------------------------------------------


@pytest.fixture()
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace ponddb.audit_log.log_event with a zero-Postgres tracker.

    The fake accepts the same signature (pool, event_type, **kwargs) and
    appends each call to the returned list for test assertions.
    """
    events: list[dict[str, Any]] = []

    async def fake_log_event(pool: Any, event_type: str, **kwargs: Any) -> None:
        events.append({"event_type": event_type, **kwargs})

    monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event)
    return events


# ---------------------------------------------------------------------------
# Admin JWT helper
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_headers() -> dict[str, str]:
    from ponddb.auth.jwt_auth import create_access_token

    token = create_access_token("default", role="admin")
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 1. pondapi_execute event
# ===========================================================================


class TestPondapiExecuteEvent:
    """POST /pondapi/execute → pondapi_execute row in security_audit_log."""

    @pytest.fixture()
    def client_and_session(self, captured_events: list) -> tuple:
        from ponddb.engine.session_manager import SessionManager
        from ponddb.pondapi.executor import make_pondapi_execute_router

        manager = SessionManager()
        sid = manager.create_session()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_conn = sqlite3.connect(tmp.name, check_same_thread=False)
        db_conn.row_factory = sqlite3.Row

        router = make_pondapi_execute_router(manager, db_conn)
        app = FastAPI()
        app.include_router(router)

        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("acme-corp")
        headers = {"Authorization": f"Bearer {token}"}

        client = TestClient(app, raise_server_exceptions=False)
        yield client, sid, headers

        db_conn.close()
        manager.destroy_session(sid)

    def test_pondapi_execute_event_logged_on_submit(
        self, client_and_session: tuple, captured_events: list
    ) -> None:
        """Successful POST /pondapi/execute → pondapi_execute event written."""
        client, sid, headers = client_and_session
        resp = client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "SELECT 1"},
            headers=headers,
        )
        assert resp.status_code == 202

        event_types = [e["event_type"] for e in captured_events]
        assert "pondapi_execute" in event_types, (
            f"Expected pondapi_execute event; got: {event_types}"
        )

    def test_pondapi_execute_event_records_tenant_id(
        self, client_and_session: tuple, captured_events: list
    ) -> None:
        """pondapi_execute event captures the submitting tenant."""
        client, sid, headers = client_and_session
        client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "SELECT 42"},
            headers=headers,
        )
        exec_events = [e for e in captured_events if e["event_type"] == "pondapi_execute"]
        assert exec_events, "No pondapi_execute event captured"
        assert exec_events[0].get("tenant_id") == "acme-corp"

    def test_pondapi_execute_event_includes_detail(
        self, client_and_session: tuple, captured_events: list
    ) -> None:
        """pondapi_execute event detail contains execution_id or session context."""
        client, sid, headers = client_and_session
        client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "SELECT 99"},
            headers=headers,
        )
        exec_events = [e for e in captured_events if e["event_type"] == "pondapi_execute"]
        assert exec_events
        detail = exec_events[0].get("detail") or ""
        assert detail, "pondapi_execute event must carry a non-empty detail string"


# ===========================================================================
# 2. sandbox_blocked event
# ===========================================================================


class TestSandboxBlockedEvent:
    """POST /pondapi/execute with blocked SQL → sandbox_blocked event."""

    @pytest.fixture()
    def client_and_session(self, captured_events: list) -> tuple:
        from ponddb.engine.session_manager import SessionManager
        from ponddb.pondapi.executor import make_pondapi_execute_router

        manager = SessionManager()
        sid = manager.create_session()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_conn = sqlite3.connect(tmp.name, check_same_thread=False)
        db_conn.row_factory = sqlite3.Row

        router = make_pondapi_execute_router(manager, db_conn)
        app = FastAPI()
        app.include_router(router)

        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("attacker-corp")
        headers = {"Authorization": f"Bearer {token}"}

        client = TestClient(app, raise_server_exceptions=False)
        yield client, sid, headers

        db_conn.close()
        manager.destroy_session(sid)

    def test_sandbox_blocked_event_on_copy_sql(
        self, client_and_session: tuple, captured_events: list
    ) -> None:
        """COPY SQL to pondapi → 400/403 and sandbox_blocked event."""
        client, sid, headers = client_and_session
        resp = client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "COPY secrets TO '/tmp/exfil'"},
            headers=headers,
        )
        assert resp.status_code in (400, 403), (
            f"Expected 400 or 403 for blocked SQL, got {resp.status_code}"
        )
        event_types = [e["event_type"] for e in captured_events]
        assert "sandbox_blocked" in event_types, (
            f"Expected sandbox_blocked event; got: {event_types}"
        )

    def test_sandbox_blocked_event_on_attach_sql(
        self, client_and_session: tuple, captured_events: list
    ) -> None:
        """ATTACH SQL → 400/403 and sandbox_blocked event."""
        client, sid, headers = client_and_session
        resp = client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "ATTACH '/etc/shadow' AS shadow_db"},
            headers=headers,
        )
        assert resp.status_code in (400, 403)
        event_types = [e["event_type"] for e in captured_events]
        assert "sandbox_blocked" in event_types

    def test_sandbox_blocked_records_tenant_id(
        self, client_and_session: tuple, captured_events: list
    ) -> None:
        """sandbox_blocked event captures the blocked tenant."""
        client, sid, headers = client_and_session
        client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "COPY t TO '/tmp/out'"},
            headers=headers,
        )
        blocked = [e for e in captured_events if e["event_type"] == "sandbox_blocked"]
        assert blocked, "No sandbox_blocked event captured"
        assert blocked[0].get("tenant_id") == "attacker-corp"

    def test_safe_sql_does_not_emit_sandbox_blocked(
        self, client_and_session: tuple, captured_events: list
    ) -> None:
        """Normal SELECT → 202 accepted, no sandbox_blocked event."""
        client, sid, headers = client_and_session
        resp = client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "SELECT 1"},
            headers=headers,
        )
        assert resp.status_code == 202
        event_types = [e["event_type"] for e in captured_events]
        assert "sandbox_blocked" not in event_types, (
            f"sandbox_blocked must not fire for safe SQL; events: {event_types}"
        )


# ===========================================================================
# 3. invite_created event
# ===========================================================================


class TestInviteCreatedEvent:
    """POST /invites → invite_created row in security_audit_log."""

    @pytest.fixture()
    def invite_client(self, captured_events: list, admin_headers: dict) -> tuple:
        from ponddb.store.invite_store import InviteStore
        from ponddb.api.invite_routes import make_invite_router

        invite_store = MagicMock(spec=InviteStore)
        invite_store.create_invite = AsyncMock(
            return_value={
                "token": "tok-invite-001",
                "email": "alice@example.com",
                "tenant_id": "default",
                "role": "member",
                "status": "pending",
            }
        )
        invite_store.list_invites = AsyncMock(return_value=[])

        router = make_invite_router(invite_store)
        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False), admin_headers

    def test_invite_created_event_logged(
        self, invite_client: tuple, captured_events: list
    ) -> None:
        """POST /invites 201 → invite_created event."""
        client, headers = invite_client
        resp = client.post(
            "/invites",
            json={"email": "alice@example.com", "role": "member"},
            headers=headers,
        )
        assert resp.status_code == 201

        event_types = [e["event_type"] for e in captured_events]
        assert "invite_created" in event_types, (
            f"Expected invite_created event; got: {event_types}"
        )

    def test_invite_created_captures_tenant_id(
        self, invite_client: tuple, captured_events: list
    ) -> None:
        """invite_created event records the creating tenant."""
        client, headers = invite_client
        client.post(
            "/invites",
            json={"email": "bob@example.com"},
            headers=headers,
        )
        events = [e for e in captured_events if e["event_type"] == "invite_created"]
        assert events, "No invite_created event captured"
        assert events[0].get("tenant_id")  # must be non-empty

    def test_invite_created_detail_includes_invitee_email(
        self, invite_client: tuple, captured_events: list
    ) -> None:
        """invite_created event detail includes the invitee email."""
        client, headers = invite_client
        client.post(
            "/invites",
            json={"email": "carol@example.com"},
            headers=headers,
        )
        events = [e for e in captured_events if e["event_type"] == "invite_created"]
        assert events
        detail = str(events[0].get("detail", ""))
        assert "carol@example.com" in detail, (
            f"Expected invitee email in detail; got: {detail!r}"
        )


# ===========================================================================
# 4. user_provisioned event
# ===========================================================================


class TestUserProvisionedEvent:
    """POST /invites/{token}/accept → user_provisioned row in security_audit_log."""

    @pytest.fixture()
    def accept_client(self, captured_events: list) -> TestClient:
        from ponddb.store.invite_store import InviteStore
        from ponddb.api.invite_routes import make_invite_router

        invite_store = MagicMock(spec=InviteStore)
        invite_store.get_invite = AsyncMock(
            return_value={
                "token": "tok-accept-001",
                "email": "dave@example.com",
                "tenant_id": "acme",
                "role": "member",
                "status": "pending",
            }
        )
        invite_store.accept_invite = AsyncMock(
            return_value={
                "token": "tok-accept-001",
                "email": "dave@example.com",
                "tenant_id": "acme",
                "role": "member",
                "status": "accepted",
            }
        )

        router = make_invite_router(invite_store)
        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_user_provisioned_event_on_accept(
        self, accept_client: TestClient, captured_events: list
    ) -> None:
        """Successful invite acceptance → user_provisioned event."""
        resp = accept_client.post(
            "/invites/tok-accept-001/accept",
            json={"email": "dave@example.com"},
        )
        assert resp.status_code == 200

        event_types = [e["event_type"] for e in captured_events]
        assert "user_provisioned" in event_types, (
            f"Expected user_provisioned event; got: {event_types}"
        )

    def test_user_provisioned_records_tenant_id(
        self, accept_client: TestClient, captured_events: list
    ) -> None:
        """user_provisioned event captures the provisioned tenant_id."""
        accept_client.post(
            "/invites/tok-accept-001/accept",
            json={"email": "dave@example.com"},
        )
        events = [e for e in captured_events if e["event_type"] == "user_provisioned"]
        assert events, "No user_provisioned event captured"
        assert events[0].get("tenant_id") == "acme"

    def test_user_provisioned_detail_includes_email(
        self, accept_client: TestClient, captured_events: list
    ) -> None:
        """user_provisioned event detail includes the new user email."""
        accept_client.post(
            "/invites/tok-accept-001/accept",
            json={"email": "dave@example.com"},
        )
        events = [e for e in captured_events if e["event_type"] == "user_provisioned"]
        assert events
        detail = str(events[0].get("detail", ""))
        assert "dave@example.com" in detail, (
            f"Expected email in detail; got: {detail!r}"
        )

    def test_failed_accept_does_not_emit_user_provisioned(
        self, captured_events: list
    ) -> None:
        """ValueError from accept_invite → 40x response, no user_provisioned event."""
        from ponddb.store.invite_store import InviteStore
        from ponddb.api.invite_routes import make_invite_router

        invite_store = MagicMock(spec=InviteStore)
        invite_store.get_invite = AsyncMock(
            return_value={
                "token": "tok-fail",
                "email": "eve@example.com",
                "tenant_id": "acme",
                "role": "member",
                "status": "pending",
            }
        )
        invite_store.accept_invite = AsyncMock(
            side_effect=ValueError("email mismatch: forbidden")
        )

        router = make_invite_router(invite_store)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/invites/tok-fail/accept",
            json={"email": "wrong@example.com"},
        )
        assert resp.status_code in (400, 403, 409)
        event_types = [e["event_type"] for e in captured_events]
        assert "user_provisioned" not in event_types


# ===========================================================================
# 5. workgroup_created event
# ===========================================================================


class TestWorkgroupCreatedEvent:
    """POST /workgroups → workgroup_created row in security_audit_log."""

    @pytest.fixture()
    def wg_client(self, captured_events: list, admin_headers: dict) -> tuple:
        from ponddb.api.namespace_routes import make_namespace_workgroup_router

        namespaces: dict[str, Any] = {}
        workgroups: dict[str, Any] = {}

        ns_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        namespaces[ns_id] = {
            "id": ns_id,
            "name": "test-namespace",
            "description": "",
            "created_at": now,
            "updated_at": now,
        }

        router = make_namespace_workgroup_router(
            workgroups_ref=workgroups,
            namespaces_ref=namespaces,
        )
        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False), ns_id, admin_headers

    def test_workgroup_created_event_logged(
        self, wg_client: tuple, captured_events: list
    ) -> None:
        """POST /workgroups 201 → workgroup_created event."""
        client, ns_id, headers = wg_client
        resp = client.post(
            "/workgroups",
            json={"name": "team-alpha", "namespace_id": ns_id},
            headers=headers,
        )
        assert resp.status_code == 201

        event_types = [e["event_type"] for e in captured_events]
        assert "workgroup_created" in event_types, (
            f"Expected workgroup_created event; got: {event_types}"
        )

    def test_workgroup_created_records_tenant_id(
        self, wg_client: tuple, captured_events: list
    ) -> None:
        """workgroup_created event captures the requesting admin tenant."""
        client, ns_id, headers = wg_client
        client.post(
            "/workgroups",
            json={"name": "team-beta", "namespace_id": ns_id},
            headers=headers,
        )
        events = [e for e in captured_events if e["event_type"] == "workgroup_created"]
        assert events, "No workgroup_created event captured"
        assert events[0].get("tenant_id")  # must be non-empty

    def test_workgroup_created_detail_includes_name(
        self, wg_client: tuple, captured_events: list
    ) -> None:
        """workgroup_created event detail includes the workgroup name."""
        client, ns_id, headers = wg_client
        client.post(
            "/workgroups",
            json={"name": "team-gamma", "namespace_id": ns_id},
            headers=headers,
        )
        events = [e for e in captured_events if e["event_type"] == "workgroup_created"]
        assert events
        detail = str(events[0].get("detail", ""))
        assert "team-gamma" in detail, (
            f"Expected workgroup name in detail; got: {detail!r}"
        )

    def test_workgroup_created_not_emitted_on_bad_namespace(
        self, wg_client: tuple, captured_events: list
    ) -> None:
        """POST /workgroups with unknown namespace_id → 404, no workgroup_created event."""
        client, _, headers = wg_client
        resp = client.post(
            "/workgroups",
            json={"name": "team-delta", "namespace_id": "no-such-namespace"},
            headers=headers,
        )
        assert resp.status_code == 404
        event_types = [e["event_type"] for e in captured_events]
        assert "workgroup_created" not in event_types
