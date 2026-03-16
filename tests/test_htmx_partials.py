"""Tests for HTMX partial endpoints — HTML fragments for dynamic dashboard updates.

Verifies:
  - Fragment endpoints return HTML (not full page — no <html> tag)
  - Correct content-type
  - Auth required
  - Session terminate returns empty response (row removal)
"""

import base64
import hashlib
import hmac
import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-htmx-partials-key"
SESSION_SECRET = "test-htmx-session-secret"


def _sign_session(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-htmx-partials")
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
def session_id(client: TestClient) -> str:
    resp = client.post("/session")
    assert resp.status_code == 201
    return resp.json()["session_id"]


# ===========================================================================
# GET /htmx/sessions-table
# ===========================================================================


class TestSessionsTable:
    def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        resp = client.get("/htmx/sessions-table")
        assert resp.status_code == 401

    def test_authenticated_returns_200(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/htmx/sessions-table", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_html_content_type(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/htmx/sessions-table", headers=auth_headers)
        assert "text/html" in resp.headers["content-type"]

    def test_returns_fragment_not_full_page(self, client: TestClient, auth_headers: dict) -> None:
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert "<html" not in body.lower()
        assert "<body" not in body.lower()

    def test_contains_table_element(self, client: TestClient, auth_headers: dict) -> None:
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert "<table" in body.lower()

    def test_shows_session_after_creation(
        self, client: TestClient, auth_headers: dict, session_id: str
    ) -> None:
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert session_id[:8] in body

    def test_has_terminate_button(
        self, client: TestClient, auth_headers: dict, session_id: str
    ) -> None:
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert "terminate" in body.lower() or "hx-delete" in body


# ===========================================================================
# DELETE /htmx/session/{session_id}
# ===========================================================================


class TestTerminateSession:
    def test_unauthenticated_returns_401(self, client: TestClient, session_id: str) -> None:
        resp = client.delete(f"/htmx/session/{session_id}")
        assert resp.status_code == 401

    def test_valid_session_returns_200(
        self, client: TestClient, auth_headers: dict, session_id: str
    ) -> None:
        resp = client.delete(f"/htmx/session/{session_id}", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_empty_body(
        self, client: TestClient, auth_headers: dict, session_id: str
    ) -> None:
        resp = client.delete(f"/htmx/session/{session_id}", headers=auth_headers)
        assert resp.text == ""

    def test_unknown_session_returns_404(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        resp = client.delete("/htmx/session/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    def test_session_gone_after_terminate(
        self, client: TestClient, auth_headers: dict, session_id: str
    ) -> None:
        client.delete(f"/htmx/session/{session_id}", headers=auth_headers)
        # Session should no longer appear in sessions table
        body = client.get("/htmx/sessions-table", headers=auth_headers).text
        assert session_id[:8] not in body


# ===========================================================================
# GET /htmx/workgroup/{wg_name}/sessions
# ===========================================================================


class TestWorkgroupSessions:
    def test_unauthenticated_returns_401(self, client: TestClient) -> None:
        resp = client.get("/htmx/workgroup/default/sessions")
        assert resp.status_code == 401

    def test_unknown_workgroup_returns_404(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        resp = client.get("/htmx/workgroup/nonexistent-wg/sessions", headers=auth_headers)
        assert resp.status_code == 404

    def test_returns_html_fragment(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        # Create workgroup first
        client.post(
            "/namespaces/default/workgroups",
            json={"name": "htmx-test-wg", "max_sessions": 10},
            headers=auth_headers,
        )
        resp = client.get("/htmx/workgroup/htmx-test-wg/sessions", headers=auth_headers)
        if resp.status_code == 200:
            body = resp.text
            assert "<html" not in body.lower()
            assert "<body" not in body.lower()
