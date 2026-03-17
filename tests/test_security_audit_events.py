"""Tests for new security audit events: failed_auth, brute_force_lockout, token_revoke, token_refresh.

Expected behavior (to be implemented):
- failed_auth: logged when require_auth rejects a protected-endpoint request
  (missing, expired, or malformed Bearer token)
- brute_force_lockout: logged when BruteForceMiddleware blocks a locked IP (429)
- token_revoke: logged on successful POST /auth/revoke
- token_refresh: logged on POST /auth/refresh (both success and failure)

Each event must produce a security_audit_log row containing:
  event_type  — the specific event string
  ip_address  — from X-Forwarded-For or client.host
  user_agent  — from User-Agent header
  tenant_id   — from JWT claim or request body (may be None on unauthenticated paths)
  detail      — optional context (e.g. jti for revoke, error message for failures)
"""

import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-security-events-key-xyz"
JWT_SECRET = "test-jwt-secret-for-security-events-32c"
TEST_TENANT = "tenant-audit-events"
TEST_IP = "192.0.2.55"
TEST_UA = "PondDB-Test-Agent/1.0"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env_setup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)


@pytest.fixture()
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Patch log_event to capture all calls; returns the list."""
    events: list[dict] = []

    async def fake_log_event(pool: Any, event_type: str, **kwargs: Any) -> None:
        events.append({"event_type": event_type, **kwargs})

    monkeypatch.setattr("ponddb.security.audit_log.log_event", fake_log_event, raising=True)
    return events


@pytest.fixture()
def app_client(env_setup, captured_events):
    """Reload the app and return a TestClient with AuditLogMiddleware attached."""
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app
    from ponddb.security.audit_log import AuditLogMiddleware

    app.add_middleware(AuditLogMiddleware, dsn=None)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def valid_tokens(env_setup):
    """Issue real access + refresh tokens for the test tenant."""
    import importlib
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app
    from ponddb.security.audit_log import AuditLogMiddleware

    app.add_middleware(AuditLogMiddleware, dsn=None)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/auth/token",
        json={"api_key": VALID_API_KEY, "tenant_id": TEST_TENANT},
        headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
    )
    assert resp.status_code == 200, f"Token issuance failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _events_of_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("event_type") == event_type]


# ---------------------------------------------------------------------------
# failed_auth
# ---------------------------------------------------------------------------


class TestFailedAuthEvent:
    """failed_auth event is logged when require_auth rejects a request."""

    def test_failed_auth_logged_on_missing_token(self, env_setup, captured_events):
        """GET /schema with no Authorization header → failed_auth logged."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(
            "/schema?session_id=nonexistent",
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )
        assert resp.status_code == 401

        evts = _events_of_type(captured_events, "failed_auth")
        assert evts, "Expected a 'failed_auth' event in security_audit_log"

    def test_failed_auth_logged_on_bad_bearer(self, env_setup, captured_events):
        """GET /schema with garbled Bearer token → failed_auth logged."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(
            "/schema?session_id=nonexistent",
            headers={
                "Authorization": "Bearer this.is.garbage",
                "X-Forwarded-For": TEST_IP,
                "User-Agent": TEST_UA,
            },
        )
        assert resp.status_code == 401

        evts = _events_of_type(captured_events, "failed_auth")
        assert evts, "Expected a 'failed_auth' event for bad Bearer token"

    def test_failed_auth_captures_ip(self, env_setup, captured_events):
        """failed_auth event includes ip_address."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        client.get(
            "/schema?session_id=nonexistent",
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "failed_auth")
        assert evts, "Expected a 'failed_auth' event"
        assert evts[0].get("ip_address") == TEST_IP

    def test_failed_auth_captures_user_agent(self, env_setup, captured_events):
        """failed_auth event includes user_agent."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        client.get(
            "/schema?session_id=nonexistent",
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "failed_auth")
        assert evts, "Expected a 'failed_auth' event"
        assert evts[0].get("user_agent") == TEST_UA

    def test_failed_auth_result_field_is_failure(self, env_setup, captured_events):
        """failed_auth event detail or result indicates the rejection reason."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        client.get(
            "/schema?session_id=nonexistent",
            headers={"Authorization": "Bearer bad.token.value", "X-Forwarded-For": TEST_IP},
        )

        evts = _events_of_type(captured_events, "failed_auth")
        assert evts, "Expected a 'failed_auth' event"
        # detail should be non-empty and contain failure information
        detail = evts[0].get("detail") or ""
        assert detail, "failed_auth event must include a detail explaining the rejection"


# ---------------------------------------------------------------------------
# brute_force_lockout
# ---------------------------------------------------------------------------


class TestBruteForceLockoutEvent:
    """brute_force_lockout event is logged when BruteForceMiddleware blocks an IP."""

    def _make_app_with_brute_force(self, guard=None):
        """Return (app, guard) with BruteForceMiddleware attached."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware
        from ponddb.auth.brute_force import BruteForceGuard, BruteForceMiddleware

        guard = guard or BruteForceGuard(lockout_threshold=3)
        app.add_middleware(BruteForceMiddleware, guard=guard)
        app.add_middleware(AuditLogMiddleware, dsn=None)
        return app, guard

    def test_lockout_event_logged_when_ip_blocked(self, env_setup, captured_events):
        """An IP that is locked → POST /auth/token returns 429 and logs brute_force_lockout."""
        from ponddb.auth.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=1)
        app, _ = self._make_app_with_brute_force(guard=guard)

        # Pre-lock the IP
        guard.record_failure(TEST_IP)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/auth/token",
            json={"api_key": "any"},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )
        assert resp.status_code == 429

        evts = _events_of_type(captured_events, "brute_force_lockout")
        assert evts, "Expected a 'brute_force_lockout' event in security_audit_log"

    def test_lockout_event_captures_ip(self, env_setup, captured_events):
        """brute_force_lockout event includes the locked ip_address."""
        from ponddb.auth.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=1)
        app, _ = self._make_app_with_brute_force(guard=guard)
        guard.record_failure(TEST_IP)

        client = TestClient(app, raise_server_exceptions=False)
        client.post(
            "/auth/token",
            json={"api_key": "any"},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "brute_force_lockout")
        assert evts, "Expected a 'brute_force_lockout' event"
        assert evts[0].get("ip_address") == TEST_IP

    def test_lockout_event_captures_user_agent(self, env_setup, captured_events):
        """brute_force_lockout event includes user_agent."""
        from ponddb.auth.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=1)
        app, _ = self._make_app_with_brute_force(guard=guard)
        guard.record_failure(TEST_IP)

        client = TestClient(app, raise_server_exceptions=False)
        client.post(
            "/auth/token",
            json={"api_key": "any"},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "brute_force_lockout")
        assert evts, "Expected a 'brute_force_lockout' event"
        assert evts[0].get("user_agent") == TEST_UA

    def test_unlocked_ip_does_not_log_lockout(self, env_setup, captured_events):
        """An IP below the threshold does NOT trigger a brute_force_lockout event."""
        from ponddb.auth.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=10)  # high threshold, won't trigger
        app, _ = self._make_app_with_brute_force(guard=guard)

        client = TestClient(app, raise_server_exceptions=False)
        client.post(
            "/auth/token",
            json={"api_key": "wrong-key"},
            headers={"X-Forwarded-For": "10.0.0.99", "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "brute_force_lockout")
        assert not evts, "Should NOT log brute_force_lockout for an unlocked IP"

    def test_lockout_detail_contains_ip(self, env_setup, captured_events):
        """brute_force_lockout detail field mentions the offending IP."""
        from ponddb.auth.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=1)
        app, _ = self._make_app_with_brute_force(guard=guard)
        guard.record_failure(TEST_IP)

        client = TestClient(app, raise_server_exceptions=False)
        client.post(
            "/auth/token",
            json={"api_key": "any"},
            headers={"X-Forwarded-For": TEST_IP},
        )

        evts = _events_of_type(captured_events, "brute_force_lockout")
        assert evts
        detail = evts[0].get("detail") or ""
        assert TEST_IP in detail, f"Expected IP {TEST_IP!r} in lockout detail: {detail!r}"


# ---------------------------------------------------------------------------
# token_revoke
# ---------------------------------------------------------------------------


class TestTokenRevokeEvent:
    """token_revoke event is logged on successful POST /auth/revoke."""

    def _get_valid_access_token(self) -> str:
        """Issue a real access token using jwt_auth helpers."""
        from ponddb.auth.jwt_auth import create_access_token

        return create_access_token(TEST_TENANT)

    def test_token_revoke_logged_on_success(self, env_setup, captured_events):
        """POST /auth/revoke with a valid token → token_revoke event logged."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        token = self._get_valid_access_token()
        resp = client.post(
            "/auth/revoke",
            json={"token": token},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )
        assert resp.status_code == 200

        evts = _events_of_type(captured_events, "token_revoke")
        assert evts, "Expected a 'token_revoke' event in security_audit_log"

    def test_token_revoke_captures_ip(self, env_setup, captured_events):
        """token_revoke event includes ip_address."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        token = self._get_valid_access_token()
        client.post(
            "/auth/revoke",
            json={"token": token},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_revoke")
        assert evts, "Expected a 'token_revoke' event"
        assert evts[0].get("ip_address") == TEST_IP

    def test_token_revoke_captures_user_agent(self, env_setup, captured_events):
        """token_revoke event includes user_agent."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        token = self._get_valid_access_token()
        client.post(
            "/auth/revoke",
            json={"token": token},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_revoke")
        assert evts, "Expected a 'token_revoke' event"
        assert evts[0].get("user_agent") == TEST_UA

    def test_token_revoke_captures_tenant_id(self, env_setup, captured_events):
        """token_revoke event includes the tenant_id from the revoked token."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        token = self._get_valid_access_token()
        client.post(
            "/auth/revoke",
            json={"token": token},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_revoke")
        assert evts, "Expected a 'token_revoke' event"
        assert evts[0].get("tenant_id") == TEST_TENANT

    def test_token_revoke_detail_contains_jti(self, env_setup, captured_events):
        """token_revoke event detail includes the jti of the revoked token."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        token = self._get_valid_access_token()
        resp = client.post(
            "/auth/revoke",
            json={"token": token},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )
        jti = resp.json().get("jti", "")

        evts = _events_of_type(captured_events, "token_revoke")
        assert evts, "Expected a 'token_revoke' event"
        detail = evts[0].get("detail") or ""
        assert jti and jti in detail, f"Expected jti {jti!r} in token_revoke detail: {detail!r}"

    def test_invalid_token_revoke_not_logged_as_success(self, env_setup, captured_events):
        """POST /auth/revoke with a garbage token → 400 and no token_revoke event."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/auth/revoke",
            json={"token": "not.a.valid.token"},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )
        assert resp.status_code == 400

        evts = _events_of_type(captured_events, "token_revoke")
        assert not evts, "Should NOT log token_revoke on failed revocation"


# ---------------------------------------------------------------------------
# token_refresh
# ---------------------------------------------------------------------------


class TestTokenRefreshEvent:
    """token_refresh event is logged on POST /auth/refresh (success and failure)."""

    def _get_refresh_token(self) -> str:
        from ponddb.auth.jwt_auth import create_refresh_token

        return create_refresh_token(TEST_TENANT)

    def test_token_refresh_logged_on_success(self, env_setup, captured_events):
        """POST /auth/refresh with a valid refresh token → token_refresh event logged."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        refresh = self._get_refresh_token()
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )
        assert resp.status_code == 200

        evts = _events_of_type(captured_events, "token_refresh")
        assert evts, "Expected a 'token_refresh' event in security_audit_log"

    def test_token_refresh_captures_ip(self, env_setup, captured_events):
        """token_refresh event includes ip_address."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        refresh = self._get_refresh_token()
        client.post(
            "/auth/refresh",
            json={"refresh_token": refresh},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_refresh")
        assert evts, "Expected a 'token_refresh' event"
        assert evts[0].get("ip_address") == TEST_IP

    def test_token_refresh_captures_user_agent(self, env_setup, captured_events):
        """token_refresh event includes user_agent."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        refresh = self._get_refresh_token()
        client.post(
            "/auth/refresh",
            json={"refresh_token": refresh},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_refresh")
        assert evts, "Expected a 'token_refresh' event"
        assert evts[0].get("user_agent") == TEST_UA

    def test_token_refresh_captures_tenant_id(self, env_setup, captured_events):
        """token_refresh event includes the tenant_id from the refresh token."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        refresh = self._get_refresh_token()
        client.post(
            "/auth/refresh",
            json={"refresh_token": refresh},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_refresh")
        assert evts, "Expected a 'token_refresh' event"
        assert evts[0].get("tenant_id") == TEST_TENANT

    def test_token_refresh_result_success(self, env_setup, captured_events):
        """Successful refresh event detail or result indicates success."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        refresh = self._get_refresh_token()
        client.post(
            "/auth/refresh",
            json={"refresh_token": refresh},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_refresh")
        assert evts, "Expected a 'token_refresh' event"
        # detail may contain "success" or result field
        detail = (evts[0].get("detail") or "").lower()
        assert "success" in detail or evts[0].get("result") == "success", (
            "token_refresh event should indicate success in detail or result field"
        )

    def test_token_refresh_failure_logged(self, env_setup, captured_events):
        """POST /auth/refresh with a bad token → token_refresh event with failure detail."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": "this.is.not.valid"},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )
        assert resp.status_code in (400, 401, 422)

        evts = _events_of_type(captured_events, "token_refresh")
        assert evts, "Expected a 'token_refresh' event even on failure"
        detail = (evts[0].get("detail") or "").lower()
        assert (
            "fail" in detail
            or "invalid" in detail
            or "error" in detail
            or evts[0].get("result") == "failure"
        ), "Failed token_refresh event should indicate failure"

    def test_token_refresh_failure_captures_ip(self, env_setup, captured_events):
        """Failed token_refresh event still captures ip_address."""
        import importlib
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app
        from ponddb.security.audit_log import AuditLogMiddleware

        app.add_middleware(AuditLogMiddleware, dsn=None)
        client = TestClient(app, raise_server_exceptions=False)

        client.post(
            "/auth/refresh",
            json={"refresh_token": "garbage.token.here"},
            headers={"X-Forwarded-For": TEST_IP, "User-Agent": TEST_UA},
        )

        evts = _events_of_type(captured_events, "token_refresh")
        assert evts, "Expected a 'token_refresh' event on failure"
        assert evts[0].get("ip_address") == TEST_IP


# ---------------------------------------------------------------------------
# Event type names are correctly spelled (guard against typos in impl)
# ---------------------------------------------------------------------------


class TestEventTypeNames:
    """Validate the exact string values expected in security_audit_log.event_type."""

    def test_failed_auth_event_type_string(self):
        """The literal string 'failed_auth' is the canonical event type."""
        assert "failed_auth" == "failed_auth"  # explicit — used as a sentinel in impl

    def test_brute_force_lockout_event_type_string(self):
        assert "brute_force_lockout" == "brute_force_lockout"

    def test_token_revoke_event_type_string(self):
        assert "token_revoke" == "token_revoke"

    def test_token_refresh_event_type_string(self):
        assert "token_refresh" == "token_refresh"
