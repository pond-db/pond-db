"""Tests for workgroup HTMX tab partials and session suspend/resume.

Verifies:
  - Workgroup tab endpoints return HTML fragments (no <html>/<body>)
  - Auth required on all tab endpoints
  - Suspend/resume change session status and return updated row
  - Tab CSS and HTMX wiring present on workgroup page
"""

import base64
import hashlib
import hmac
import importlib
import json

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-wg-tabs-key"
SESSION_SECRET = "test-wg-tabs-session-secret"


def _sign_session(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-wg-tabs")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def logged_in_client(client: TestClient) -> TestClient:
    cookie = _sign_session({"tenant_id": "default"})
    client.cookies.set("pond_session", cookie)
    return client


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Create admin JWT headers for workgroup management."""
    from ponddb.auth.jwt_auth import create_access_token
    token = create_access_token("default", role="admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def wg_name(client: TestClient, admin_headers: dict) -> str:
    """Create a namespace + workgroup and return the workgroup name."""
    ns_resp = client.post(
        "/namespaces", json={"name": "tabs-ns"}, headers=admin_headers,
    )
    ns_id = ns_resp.json()["id"]
    client.post(
        "/workgroups",
        json={"name": "tabs-test-wg", "namespace_id": ns_id, "max_sessions": 10},
        headers=admin_headers,
    )
    return "tabs-test-wg"


# ===========================================================================
# Workgroup tab endpoints — auth required
# ===========================================================================


class TestTabAuth:
    def test_overview_requires_auth(self, client: TestClient, wg_name: str) -> None:
        resp = client.get(f"/htmx/workgroup/{wg_name}/overview")
        assert resp.status_code == 401

    def test_history_requires_auth(self, client: TestClient, wg_name: str) -> None:
        resp = client.get(f"/htmx/workgroup/{wg_name}/history")
        assert resp.status_code == 401

    def test_apikeys_requires_auth(self, client: TestClient, wg_name: str) -> None:
        resp = client.get(f"/htmx/workgroup/{wg_name}/apikeys")
        assert resp.status_code == 401


# ===========================================================================
# Workgroup tab endpoints — returns HTML fragments
# ===========================================================================


class TestTabOverview:
    def test_returns_200(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        resp = client.get(f"/htmx/workgroup/{wg_name}/overview", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_html_fragment(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/overview", headers=auth_headers).text
        assert "<html" not in body.lower()
        assert "<body" not in body.lower()

    def test_has_stat_cards(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/overview", headers=auth_headers).text
        assert "stat-card" in body

    def test_has_detail_rows(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/overview", headers=auth_headers).text
        assert "detail-row" in body

    def test_shows_workgroup_name(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/overview", headers=auth_headers).text
        assert wg_name in body

    def test_unknown_wg_returns_404(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/htmx/workgroup/nonexistent/overview", headers=auth_headers)
        assert resp.status_code == 404


class TestTabHistory:
    def test_returns_200(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        resp = client.get(f"/htmx/workgroup/{wg_name}/history", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_html_fragment(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/history", headers=auth_headers).text
        assert "<html" not in body.lower()
        assert "<body" not in body.lower()

    def test_has_history_content(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/history", headers=auth_headers).text
        # Should have either a table or "No recent queries" message
        assert "history" in body.lower() or "queries" in body.lower() or "<table" in body.lower()

    def test_unknown_wg_returns_404(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/htmx/workgroup/nonexistent/history", headers=auth_headers)
        assert resp.status_code == 404


class TestTabApikeys:
    def test_returns_200(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        resp = client.get(f"/htmx/workgroup/{wg_name}/apikeys", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_html_fragment(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/apikeys", headers=auth_headers).text
        assert "<html" not in body.lower()
        assert "<body" not in body.lower()

    def test_has_key_display(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/apikeys", headers=auth_headers).text
        assert "key-display" in body

    def test_has_auth_header_info(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/apikeys", headers=auth_headers).text
        assert "x-api-key" in body.lower()

    def test_has_jwt_endpoint(self, client: TestClient, auth_headers: dict, wg_name: str) -> None:
        body = client.get(f"/htmx/workgroup/{wg_name}/apikeys", headers=auth_headers).text
        assert "/auth/token" in body

    def test_unknown_wg_returns_404(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/htmx/workgroup/nonexistent/apikeys", headers=auth_headers)
        assert resp.status_code == 404


# ===========================================================================
# Session suspend/resume endpoints
# ===========================================================================


class TestSuspendResume:
    def test_suspend_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/htmx/session/fake-id/suspend")
        assert resp.status_code == 401

    def test_resume_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/htmx/session/fake-id/resume")
        assert resp.status_code == 401

    def test_suspend_unknown_session_returns_404(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/htmx/session/nonexistent/suspend", headers=auth_headers)
        assert resp.status_code == 404

    def test_resume_unknown_session_returns_404(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post("/htmx/session/nonexistent/resume", headers=auth_headers)
        assert resp.status_code == 404

    def test_suspend_returns_html_row(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        resp = client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.text
        assert "<html" not in body.lower()
        assert "<tr" in body.lower()

    def test_suspend_changes_status_badge(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        resp = client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        body = resp.text.lower()
        assert "suspended" in body

    def test_resume_after_suspend(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        resp = client.post(f"/htmx/session/{sid}/resume", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "active" in body

    def test_resume_returns_html_row(self, client: TestClient, auth_headers: dict) -> None:
        sid = client.post("/session").json()["session_id"]
        client.post(f"/htmx/session/{sid}/suspend", headers=auth_headers)
        resp = client.post(f"/htmx/session/{sid}/resume", headers=auth_headers)
        assert "<tr" in resp.text.lower()
        assert "<html" not in resp.text.lower()


# ===========================================================================
# Workgroup page — HTMX tab wiring
# ===========================================================================


class TestWorkgroupPageHTMX:
    def test_has_htmx_tab_attrs(self, logged_in_client: TestClient, wg_name: str) -> None:
        body = logged_in_client.get(f"/workgroup/{wg_name}").text
        assert "hx-get" in body
        assert "hx-target" in body

    def test_overview_tab_loaded_inline(self, logged_in_client: TestClient, wg_name: str) -> None:
        body = logged_in_client.get(f"/workgroup/{wg_name}").text
        assert "stat-card" in body  # Overview content is inline on first load

    def test_has_all_tab_links(self, logged_in_client: TestClient, wg_name: str) -> None:
        body = logged_in_client.get(f"/workgroup/{wg_name}").text.lower()
        assert "overview" in body
        assert "sessions" in body
        assert "history" in body
        assert "api key" in body

    def test_tabs_target_content_container(self, logged_in_client: TestClient, wg_name: str) -> None:
        body = logged_in_client.get(f"/workgroup/{wg_name}").text
        assert "wg-tab-content" in body

    def test_has_tab_switching_js(self, logged_in_client: TestClient, wg_name: str) -> None:
        body = logged_in_client.get(f"/workgroup/{wg_name}").text
        assert "data-tab" in body


# ===========================================================================
# CSS additions
# ===========================================================================


class TestCSSAdditions:
    def test_pond_css_has_tab_styles(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".wg-tabs" in body

    def test_pond_css_has_detail_row(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".detail-row" in body

    def test_pond_css_has_key_display(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".key-display" in body

    def test_pond_css_has_warning_button(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".btn-warning" in body
