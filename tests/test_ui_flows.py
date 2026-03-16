"""Integration tests: end-to-end UI workflows and button click outcomes.

Covers:
  - Auth flow: login → cookie → dashboard → logout → redirect
  - Session lifecycle: create → appears in table → suspend → resume → terminate → gone
  - Admin invite CRUD: create invite → appears in table → revoke → status changes
  - Workgroup tabs: each tab returns correct data when clicked
  - Cross-page nav: breadcrumbs and sidebar links all resolve
"""

import base64
import hashlib
import hmac
import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-ui-flows-key"
SESSION_SECRET = "test-ui-flows-session-secret"

os.environ.setdefault("POND_JWT_SECRET", "test-jwt-ui-flows")
os.environ.setdefault("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


def _sign(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-ui-flows")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture
def client(_env) -> TestClient:
    import ponddb.app as m
    importlib.reload(m)
    return TestClient(m.app, follow_redirects=False)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    client.cookies.set("pond_session", _sign({"tenant_id": "default"}))
    return client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    client.cookies.set("pond_session", _sign({"tenant_id": "default", "role": "admin"}))
    return client


@pytest.fixture
def admin_jwt() -> dict[str, str]:
    from ponddb.jwt_auth import create_access_token
    token = create_access_token("default", role="admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def wg_name(client: TestClient, admin_jwt: dict) -> str:
    ns = client.post("/namespaces", json={"name": "flow-ns"}, headers=admin_jwt)
    ns_id = ns.json()["id"]
    client.post(
        "/workgroups",
        json={"name": "flow-wg", "namespace_id": ns_id, "max_sessions": 10},
        headers=admin_jwt,
    )
    return "flow-wg"


# ── Auth flow ────────────────────────────────────────────────────────────────


class TestAuthFlow:
    def test_login_sets_cookie_and_redirects(self, client: TestClient) -> None:
        resp = client.post("/login", data={"api_key": VALID_KEY})
        assert resp.status_code in (302, 303)
        assert "/dashboard" in resp.headers["location"]

    def test_cookie_grants_dashboard_access(self, client: TestClient) -> None:
        client.post("/login", data={"api_key": VALID_KEY})
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 200

    def test_logout_clears_access(self, client: TestClient) -> None:
        client.post("/login", data={"api_key": VALID_KEY})
        client.post("/logout")
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_invalid_key_stays_on_login(self, client: TestClient) -> None:
        resp = client.post("/login", data={"api_key": "wrong"}, follow_redirects=False)
        if resp.status_code == 200:
            assert "invalid" in resp.text.lower() or "error" in resp.text.lower()
        else:
            assert resp.status_code in (302, 303, 401, 403)

    def test_unauthenticated_dashboard_redirects(self, client: TestClient) -> None:
        resp = client.get("/dashboard")
        assert resp.status_code in (302, 303)


# ── Session lifecycle ────────────────────────────────────────────────────────


class TestSessionLifecycle:
    def test_create_session_via_api(self, client: TestClient) -> None:
        resp = client.post("/session")
        assert resp.status_code == 201
        assert "session_id" in resp.json()

    def test_session_appears_in_table(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert sid[:8] in body

    def test_suspend_button_changes_status(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        resp = client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        assert resp.status_code == 200
        assert "suspended" in resp.text.lower()

    def test_suspended_row_has_resume_button(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        body = client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers).text
        assert "resume" in body.lower()
        assert f"/htmx/session/{sid}/resume" in body

    def test_resume_button_restores_active(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        resp = client.post(f"/htmx/session/{sid}/resume", headers=auth_headers)
        assert resp.status_code == 200
        assert "active" in resp.text.lower()

    def test_resumed_row_has_suspend_button(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        body = client.post(f"/htmx/session/{sid}/resume", headers=auth_headers).text
        assert "suspend" in body.lower()

    def test_terminate_removes_from_table(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        resp = client.delete(f"/htmx/session/{sid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.text == ""
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert sid[:8] not in body

    def test_terminate_button_has_confirm(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert "hx-confirm" in body


# ── Admin invite flow ────────────────────────────────────────────────────────


class TestAdminInviteFlow:
    def test_create_invite_appears_in_list(self, admin: TestClient) -> None:
        admin.post("/admin/invites", data={
            "email": "flow-test@example.com", "role": "member", "expires_in_hours": "168",
        })
        body = admin.get("/admin/invites").text
        assert "flow-test@example.com" in body

    def test_invite_row_has_revoke_button(self, admin: TestClient) -> None:
        admin.post("/admin/invites", data={
            "email": "revoke-test@example.com", "role": "member", "expires_in_hours": "168",
        })
        body = admin.get("/admin/invites").text
        assert "Revoke" in body
        assert 'method="POST"' in body

    def test_revoke_changes_status(self, admin: TestClient) -> None:
        admin.post("/admin/invites", data={
            "email": "revoke-status@example.com", "role": "member", "expires_in_hours": "168",
        })
        body = admin.get("/admin/invites").text
        # Extract token from row
        import re
        tokens = re.findall(r'/admin/invites/([^/]+)/revoke', body)
        assert len(tokens) > 0
        admin.post(f"/admin/invites/{tokens[0]}/revoke")
        body2 = admin.get("/admin/invites").text
        assert "revoked" in body2.lower()


# ── Workgroup tab data ───────────────────────────────────────────────────────


class TestWorkgroupTabData:
    def test_overview_tab_shows_quota(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/overview", headers=auth_headers).text
        assert "max" in body.lower() or "session" in body.lower()

    def test_sessions_tab_shows_table(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/sessions", headers=auth_headers).text
        assert "<table" in body.lower() or "no active" in body.lower()

    def test_history_tab_shows_content(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/history", headers=auth_headers).text
        assert "history" in body.lower() or "queries" in body.lower() or "<table" in body.lower()

    def test_apikeys_tab_shows_endpoints(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/apikeys", headers=auth_headers).text
        assert "/auth/token" in body
        assert "x-api-key" in body.lower()

    def test_workgroup_page_breadcrumb_resolves(self, logged_in: TestClient, wg_name: str) -> None:
        body = logged_in.get(f"/workgroup/{wg_name}").text
        assert 'href="/dashboard"' in body
        assert logged_in.get("/dashboard").status_code == 200


# ── Cross-page navigation ───────────────────────────────────────────────────


class TestCrossPageNav:
    def test_dashboard_to_sessions_and_back(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard").text
        assert "/dashboard/sessions" in body
        body2 = logged_in.get("/dashboard/sessions").text
        assert 'href="/dashboard"' in body2

    def test_sessions_to_editor(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard/sessions").text
        assert 'href="/editor"' in body

    def test_admin_breadcrumbs_resolve(self, admin: TestClient) -> None:
        pages = ["/admin/invites", "/admin/namespaces", "/admin/usage"]
        for page in pages:
            body = admin.get(page).text
            assert 'href="/admin"' in body
            assert 'href="/dashboard"' in body

    def test_landing_to_login_to_landing(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "/login" in body
        body2 = client.get("/login").text
        assert 'href="/"' in body2
