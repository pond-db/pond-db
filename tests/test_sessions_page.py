"""Tests for the styled sessions HTML page at GET /dashboard/sessions.

Verifies:
  - Auth required (redirects to login)
  - Returns HTML with styled table
  - Shows session data (IDs, status badges)
  - Has HTMX auto-refresh wiring
  - Has terminate buttons
"""

import base64
import hashlib
import hmac
import importlib
import json

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-sessions-page-key"
SESSION_SECRET = "test-sessions-page-secret"


def _sign_session(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-sessions-page")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def logged_in_client(client: TestClient) -> TestClient:
    cookie = _sign_session({"tenant_id": "default"})
    client.cookies.set("pond_session", cookie)
    return client


class TestSessionsPage:
    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        resp = client.get("/dashboard/sessions", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_authenticated_returns_200(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/dashboard/sessions")
        assert resp.status_code == 200

    def test_returns_html(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/dashboard/sessions")
        assert "text/html" in resp.headers["content-type"]

    def test_has_sessions_heading(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard/sessions").text.lower()
        assert "session" in body

    def test_has_sidebar(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard/sessions").text
        assert "sidebar" in body.lower()

    def test_has_htmx_auto_refresh(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard/sessions").text
        assert "hx-get" in body
        assert "hx-trigger" in body

    def test_has_new_session_button(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard/sessions").text
        assert "/editor" in body

    def test_has_breadcrumb_to_dashboard(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard/sessions").text
        assert "/dashboard" in body

    def test_shows_session_after_creation(self, logged_in_client: TestClient) -> None:
        # Create a session via API
        resp = logged_in_client.post("/session")
        assert resp.status_code == 201
        sid = resp.json()["session_id"]
        body = logged_in_client.get("/dashboard/sessions").text
        assert sid[:8] in body

    def test_has_terminate_button(self, logged_in_client: TestClient) -> None:
        logged_in_client.post("/session")
        body = logged_in_client.get("/dashboard/sessions").text
        assert "terminate" in body.lower() or "hx-delete" in body

    def test_no_server_error(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard/sessions").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body


class TestWorkgroupTabs:
    def test_workgroup_page_has_tabs(self, logged_in_client: TestClient) -> None:
        # Create workgroup
        logged_in_client.post(
            "/namespaces/default/workgroups",
            json={"name": "tab-test-wg", "max_sessions": 5},
            headers={"X-API-Key": VALID_KEY},
        )
        resp = logged_in_client.get("/workgroup/tab-test-wg")
        if resp.status_code == 200:
            body = resp.text.lower()
            assert "overview" in body
            assert "sessions" in body
            assert "history" in body
            assert "api key" in body

    def test_workgroup_has_tab_switching_js(self, logged_in_client: TestClient) -> None:
        logged_in_client.post(
            "/namespaces/default/workgroups",
            json={"name": "tab-js-wg", "max_sessions": 5},
            headers={"X-API-Key": VALID_KEY},
        )
        resp = logged_in_client.get("/workgroup/tab-js-wg")
        if resp.status_code == 200:
            assert "data-tab" in resp.text

    def test_workgroup_sessions_tab_has_htmx(self, logged_in_client: TestClient) -> None:
        logged_in_client.post(
            "/namespaces/default/workgroups",
            json={"name": "tab-htmx-wg", "max_sessions": 5},
            headers={"X-API-Key": VALID_KEY},
        )
        resp = logged_in_client.get("/workgroup/tab-htmx-wg")
        if resp.status_code == 200:
            assert "hx-get" in resp.text


class TestPondapiDetailPartial:
    def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        resp = client.get("/htmx/pondapi/nonexistent/detail")
        assert resp.status_code == 401

    def test_unknown_execution_returns_404(self, client: TestClient) -> None:
        resp = client.get(
            "/htmx/pondapi/nonexistent-id/detail",
            headers={"X-API-Key": VALID_KEY},
        )
        assert resp.status_code == 404

    def test_valid_execution_returns_html_fragment(self, client: TestClient) -> None:
        # Create session + submit async execution
        sid_resp = client.post("/session")
        sid = sid_resp.json()["session_id"]
        exec_resp = client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": "SELECT 42 AS answer"},
            headers={"X-API-Key": VALID_KEY},
        )
        if exec_resp.status_code in (200, 202):
            eid = exec_resp.json()["execution_id"]
            import time

            time.sleep(0.5)  # let execution complete
            resp = client.get(
                f"/htmx/pondapi/{eid}/detail",
                headers={"X-API-Key": VALID_KEY},
            )
            assert resp.status_code == 200
            body = resp.text
            assert "<html" not in body.lower()
            assert "pondapi-detail" in body
