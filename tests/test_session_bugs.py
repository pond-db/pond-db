"""Behavioral tests for session lifecycle bugs.

Bug 1: Sessions never auto-suspend — watchdog exists but was never started.
Bug 2: Suspend/Resume buttons break on edge cases (double-suspend, etc.).
Bug 3: Page refresh must not create new sessions as a side effect.
"""

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ponddb.session_manager import SessionManager, SessionStatus

VALID_KEY = "test-session-bugs-key"
JWT_SECRET = "test-session-bugs-jwt"
SESSION_SECRET = "test-session-bugs-session"


def _sign_session(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def logged_in_client(client: TestClient) -> TestClient:
    cookie = _sign_session({"tenant_id": "default", "role": "admin"})
    client.cookies.set("pond_session", cookie)
    return client


# =========================================================================
# Bug 1: Auto-suspend idle sessions + reap stale suspended sessions
# =========================================================================


class TestWatchdogSuspendsIdleSessions:
    """run_watchdog_once() should suspend sessions idle > idle_timeout."""

    @pytest.mark.asyncio
    async def test_idle_session_gets_suspended(self) -> None:
        """A session idle longer than timeout is suspended by the watchdog."""
        mgr = SessionManager(idle_timeout=5)
        sid = mgr.create_session()
        # Backdate last_active to 10 seconds ago
        mgr._sessions[sid].last_active = datetime.now(timezone.utc) - timedelta(seconds=10)

        suspended = await mgr.run_watchdog_once()

        assert sid in suspended
        info = mgr.get_session(sid)
        assert info["status"] == SessionStatus.SUSPENDED

    @pytest.mark.asyncio
    async def test_recently_active_session_not_suspended(self) -> None:
        """A session with recent activity stays ACTIVE."""
        mgr = SessionManager(idle_timeout=300)
        sid = mgr.create_session()
        # last_active is now (just created)

        suspended = await mgr.run_watchdog_once()

        assert sid not in suspended
        info = mgr.get_session(sid)
        assert info["status"] == SessionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_already_suspended_session_not_touched(self) -> None:
        """A session that is already SUSPENDED is not suspended again."""
        mgr = SessionManager(idle_timeout=5)
        sid = mgr.create_session()
        mgr.suspend_session(sid)

        suspended = await mgr.run_watchdog_once()

        assert sid not in suspended
        # Still suspended, not errored
        info = mgr.get_session(sid)
        assert info["status"] == SessionStatus.SUSPENDED

    @pytest.mark.asyncio
    async def test_watchdog_suspends_multiple_idle_sessions(self) -> None:
        """Watchdog handles batch suspension correctly."""
        mgr = SessionManager(idle_timeout=5)
        sids = [mgr.create_session() for _ in range(3)]
        old = datetime.now(timezone.utc) - timedelta(seconds=60)
        for sid in sids:
            mgr._sessions[sid].last_active = old

        suspended = await mgr.run_watchdog_once()

        assert set(sids) == set(suspended)
        for sid in sids:
            assert mgr.get_session(sid)["status"] == SessionStatus.SUSPENDED


class TestReaperDestroysStaleSession:
    """run_reaper_once() should destroy sessions suspended > max_suspend_age."""

    @pytest.mark.asyncio
    async def test_stale_suspended_session_destroyed(self) -> None:
        """Session suspended more than max_suspend_age gets destroyed."""
        mgr = SessionManager(idle_timeout=5)
        sid = mgr.create_session()
        mgr.suspend_session(sid)
        # Backdate suspended_at to 2 hours ago
        mgr._sessions[sid].suspended_at = datetime.now(timezone.utc) - timedelta(hours=2)

        destroyed = await mgr.run_reaper_once(max_suspend_age=3600)

        assert sid in destroyed
        assert sid not in mgr._sessions  # removed from memory

    @pytest.mark.asyncio
    async def test_recently_suspended_session_not_reaped(self) -> None:
        """Session suspended recently stays in memory."""
        mgr = SessionManager(idle_timeout=5)
        sid = mgr.create_session()
        mgr.suspend_session(sid)
        # suspended_at is now (just suspended)

        destroyed = await mgr.run_reaper_once(max_suspend_age=3600)

        assert sid not in destroyed
        assert sid in mgr._sessions

    @pytest.mark.asyncio
    async def test_active_sessions_not_reaped(self) -> None:
        """Active sessions are never touched by the reaper."""
        mgr = SessionManager(idle_timeout=5)
        sid = mgr.create_session()
        # Active session, even if old
        mgr._sessions[sid].last_active = datetime.now(timezone.utc) - timedelta(hours=5)

        destroyed = await mgr.run_reaper_once(max_suspend_age=3600)

        assert sid not in destroyed
        assert mgr.get_session(sid)["status"] == SessionStatus.ACTIVE


class TestWatchdogIntegration:
    """start_watchdog() runs both watchdog and reaper in a loop."""

    @pytest.mark.asyncio
    async def test_start_watchdog_runs_at_least_once(self) -> None:
        """The watchdog loop suspends an idle session within one poll cycle."""
        mgr = SessionManager(idle_timeout=0)  # 0s = immediate
        sid = mgr.create_session()
        mgr._sessions[sid].last_active = datetime.now(timezone.utc) - timedelta(seconds=1)

        task = asyncio.create_task(mgr.start_watchdog(poll_interval=0.05))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert mgr.get_session(sid)["status"] == SessionStatus.SUSPENDED


# =========================================================================
# Bug 2: Suspend/Resume button edge cases
# =========================================================================


class TestSuspendButtonBehavior:
    """HTMX suspend endpoint returns correct HTML and handles edge cases."""

    def test_suspend_returns_html_row_with_resume_button(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """After suspend, response HTML contains a Resume button."""
        sid = client.post("/session").json()["session_id"]
        resp = client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        assert resp.status_code == 200
        assert "<tr" in resp.text
        assert "Resume" in resp.text
        assert "SUSPENDED" in resp.text.upper() or "badge-suspended" in resp.text

    def test_suspend_actually_changes_db_status(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """After suspend, the session status is SUSPENDED in the session list."""
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        sessions = client.get("/sessions").json()
        match = [s for s in sessions if s["session_id"] == sid]
        assert len(match) == 1
        assert match[0]["status"] == "SUSPENDED"

    def test_double_suspend_returns_409(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """Suspending an already-suspended session returns 409, not 500."""
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        resp = client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        assert resp.status_code == 409

    def test_suspend_nonexistent_returns_404(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        resp = client.post("/htmx/session/fake-id-000/suspend", headers=auth_headers)
        assert resp.status_code == 404


class TestResumeButtonBehavior:
    """HTMX resume endpoint returns correct HTML and handles edge cases."""

    def test_resume_returns_html_row_with_suspend_button(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """After resume, response HTML contains a Suspend button."""
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        resp = client.post(f"/htmx/session/{sid}/resume", headers=auth_headers)
        assert resp.status_code == 200
        assert "<tr" in resp.text
        assert "Suspend" in resp.text
        assert "ACTIVE" in resp.text.upper() or "badge-active" in resp.text

    def test_resume_actually_changes_db_status(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """After resume, the session status is ACTIVE in the session list."""
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        client.post(f"/htmx/session/{sid}/resume", headers=auth_headers)
        sessions = client.get("/sessions").json()
        match = [s for s in sessions if s["session_id"] == sid]
        assert len(match) == 1
        assert match[0]["status"] == "ACTIVE"

    def test_double_resume_returns_409(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """Resuming an already-active session returns 409, not 500."""
        sid = client.post("/session").json()["session_id"]
        resp = client.post(f"/htmx/session/{sid}/resume", headers=auth_headers)
        assert resp.status_code == 409

    def test_resume_nonexistent_returns_404(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        resp = client.post("/htmx/session/fake-id-000/resume", headers=auth_headers)
        assert resp.status_code == 404


class TestSuspendResumeRoundTrip:
    """Full suspend -> resume -> query cycle works end-to-end."""

    def test_suspend_resume_query(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """Session can execute queries after suspend+resume cycle."""
        sid = client.post("/session").json()["session_id"]
        # Suspend
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        # Resume
        client.post(f"/htmx/session/{sid}/resume", headers=auth_headers)
        # Query
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 42 AS answer"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["rows"] == [[42]]

    def test_htmx_response_has_7_columns(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """The session_row.html partial returns exactly 7 <td> cells."""
        sid = client.post("/session").json()["session_id"]
        resp = client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        td_count = resp.text.count("<td>") + resp.text.count("<td ")
        assert td_count == 7, f"Expected 7 <td> cells, got {td_count}"


# =========================================================================
# Bug 3: Page views must NOT create sessions as a side effect
# =========================================================================


class TestNoSideEffectSessionCreation:
    """GET requests to dashboard/sessions pages must not create sessions."""

    def test_dashboard_no_session_creation(
        self, logged_in_client: TestClient
    ) -> None:
        """Visiting /dashboard does not increase session count."""
        before = len(logged_in_client.get("/sessions").json())
        logged_in_client.get("/dashboard")
        logged_in_client.get("/dashboard")
        logged_in_client.get("/dashboard")
        after = len(logged_in_client.get("/sessions").json())
        assert after == before

    def test_sessions_page_no_session_creation(
        self, logged_in_client: TestClient
    ) -> None:
        """Visiting /dashboard/sessions 5 times does not increase count."""
        before = len(logged_in_client.get("/sessions").json())
        for _ in range(5):
            logged_in_client.get("/dashboard/sessions")
        after = len(logged_in_client.get("/sessions").json())
        assert after == before

    def test_settings_page_no_session_creation(
        self, logged_in_client: TestClient
    ) -> None:
        """Visiting /settings does not create sessions."""
        before = len(logged_in_client.get("/sessions").json())
        logged_in_client.get("/settings")
        after = len(logged_in_client.get("/sessions").json())
        assert after == before

    def test_session_only_created_by_explicit_post(
        self, client: TestClient
    ) -> None:
        """Sessions are only created by POST /session."""
        before = len(client.get("/sessions").json())
        resp = client.post("/session")
        assert resp.status_code == 201
        after = len(client.get("/sessions").json())
        assert after == before + 1
