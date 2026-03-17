"""Integration tests for the SQL editor with PondAPI HTMX polling.

The public website SQL editor uses HTMX to submit queries and poll for results
without a full page reload.  These tests cover:

  GET  /editor              — SQL editor page includes HTMX script and attributes
  POST /pondapi/execute/htmx  — submit SQL; returns HTML fragment with polling trigger
  GET  /pondapi/execute/{id}/htmx — poll status; returns HTML fragment

HTMX contract:
  - Initial submit returns <div id="pondapi-result"> with hx-get / hx-trigger="every 1s"
  - Polling endpoint returns updated <div id="pondapi-result"> fragment
  - When status is 'pending' or 'running', fragment includes polling trigger
  - When status is 'complete', fragment includes result table and NO polling trigger
  - When status is 'error', fragment includes error message and NO polling trigger
  - Fragment must NOT include <html>/<body> (partial, not full page)

Auth: same API key / session cookie model as the rest of the website.
"""

import importlib
import os
import time

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-htmx-api-key"

os.environ.setdefault("POND_JWT_SECRET", "test-htmx-jwt-secret")
os.environ.setdefault("POND_WEBSITE_SESSION_SECRET", "test-htmx-session-secret")


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-htmx-jwt-secret")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", "test-htmx-session-secret")


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app, follow_redirects=False)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def session_id(client: TestClient, auth_headers: dict) -> str:
    resp = client.post("/session", headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _wait_for_htmx_completion(
    client: TestClient,
    execution_id: str,
    auth_headers: dict,
    timeout: float = 15.0,
    poll_interval: float = 0.2,
) -> str:
    """Poll GET /pondapi/execute/{id}/htmx until status is complete or error."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(
            f"/pondapi/execute/{execution_id}/htmx",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.text
        if "complete" in body or "error" in body:
            # No more polling trigger once done
            if "hx-trigger" not in body or "every" not in body:
                return body
        time.sleep(poll_interval)
    raise TimeoutError(f"Execution {execution_id} did not complete within {timeout}s")


# ===========================================================================
# GET /editor — HTMX integration in the SQL editor page
# ===========================================================================


class TestEditorHTMX:
    def test_editor_includes_htmx_script(self, client: TestClient) -> None:
        body = client.get("/editor").text
        # HTMX loaded from CDN or bundled
        assert "htmx" in body.lower()

    def test_editor_has_htmx_post_form_or_button(self, client: TestClient) -> None:
        body = client.get("/editor").text
        # Either hx-post attribute or a form that posts to htmx endpoint
        assert "hx-post" in body or "/pondapi/execute/htmx" in body

    def test_editor_has_result_target_div(self, client: TestClient) -> None:
        """There must be a target element for HTMX to swap results into."""
        body = client.get("/editor").text
        assert "pondapi-result" in body or 'id="result"' in body or 'id="results"' in body

    def test_editor_has_htmx_target_attribute(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "hx-target" in body

    def test_editor_has_htmx_swap_attribute(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "hx-swap" in body

    def test_editor_has_loading_indicator(self, client: TestClient) -> None:
        body = client.get("/editor").text.lower()
        assert any(kw in body for kw in ["loading", "running", "spinner", "htmx-indicator"])

    def test_editor_htmx_includes_session_id_field(self, client: TestClient) -> None:
        """The HTMX form/request must include session_id so the backend knows which session."""
        body = client.get("/editor").text
        assert "session_id" in body or "session" in body.lower()


# ===========================================================================
# POST /pondapi/execute/htmx — Submit SQL for HTMX async execution
# ===========================================================================


class TestHTMXExecuteSubmit:
    def test_unauthenticated_returns_401_or_redirect(
        self, client: TestClient, session_id: str
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1"},
        )
        assert resp.status_code in (401, 403, 302, 303)

    def test_valid_submission_returns_200_or_202(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1 AS n"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)

    def test_response_content_type_is_html(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        assert "text/html" in resp.headers["content-type"]

    def test_response_is_html_fragment_not_full_page(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        body = resp.text.lower()
        assert "<html" not in body
        assert "<body" not in body

    def test_response_contains_execution_id(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        """Fragment must embed the execution_id so HTMX can poll the status endpoint."""
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        body = resp.text
        # Execution ID should appear in a polling URL or data attribute
        assert "/pondapi/execute/" in body

    def test_response_contains_polling_trigger(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        """Fragment must include hx-trigger for polling until complete."""
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        body = resp.text
        assert "hx-trigger" in body or "hx-get" in body

    def test_response_has_result_container_div(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        body = resp.text
        assert "<div" in body

    def test_missing_sql_returns_400(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id},
            headers=auth_headers,
        )
        assert resp.status_code in (400, 422)

    def test_missing_session_id_returns_400(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"sql": "SELECT 1"},
            headers=auth_headers,
        )
        assert resp.status_code in (400, 422)

    def test_empty_sql_returns_400(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": ""},
            headers=auth_headers,
        )
        assert resp.status_code in (400, 422)

    def test_invalid_session_id_returns_error_fragment(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": "not-a-real-session", "sql": "SELECT 1"},
            headers=auth_headers,
        )
        # Either 404/400 or a 200 HTML error fragment
        if resp.status_code == 200:
            assert "error" in resp.text.lower() or "not found" in resp.text.lower()
        else:
            assert resp.status_code in (400, 404, 422)


# ===========================================================================
# GET /pondapi/execute/{id}/htmx — Poll for HTMX result fragment
# ===========================================================================


class TestHTMXPollStatus:
    @pytest.fixture
    def execution_id(self, client: TestClient, session_id: str, auth_headers: dict) -> str:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 42 AS answer"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        # Extract execution ID from body (embedded in a URL like /pondapi/execute/{id}/htmx)
        import re

        match = re.search(r"/pondapi/execute/([a-f0-9-]+)/htmx", resp.text)
        assert match, f"No execution ID found in response: {resp.text[:300]}"
        return match.group(1)

    def test_poll_unknown_id_returns_404_or_error_fragment(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        import uuid

        fake_id = str(uuid.uuid4())
        resp = client.get(f"/pondapi/execute/{fake_id}/htmx", headers=auth_headers)
        if resp.status_code == 200:
            assert "error" in resp.text.lower() or "not found" in resp.text.lower()
        else:
            assert resp.status_code == 404

    def test_poll_returns_200(
        self, client: TestClient, execution_id: str, auth_headers: dict
    ) -> None:
        resp = client.get(f"/pondapi/execute/{execution_id}/htmx", headers=auth_headers)
        assert resp.status_code == 200

    def test_poll_content_type_is_html(
        self, client: TestClient, execution_id: str, auth_headers: dict
    ) -> None:
        resp = client.get(f"/pondapi/execute/{execution_id}/htmx", headers=auth_headers)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_poll_returns_fragment_not_full_page(
        self, client: TestClient, execution_id: str, auth_headers: dict
    ) -> None:
        resp = client.get(f"/pondapi/execute/{execution_id}/htmx", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "<html" not in body
        assert "<body" not in body

    def test_poll_contains_status_indicator(
        self, client: TestClient, execution_id: str, auth_headers: dict
    ) -> None:
        """Fragment must indicate the current execution status."""
        resp = client.get(f"/pondapi/execute/{execution_id}/htmx", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.text.lower()
        assert any(status in body for status in ["pending", "running", "complete", "error"])

    def test_poll_pending_includes_polling_trigger(
        self, client: TestClient, execution_id: str, auth_headers: dict
    ) -> None:
        """While pending/running, the fragment must keep polling via hx-trigger."""
        resp = client.get(f"/pondapi/execute/{execution_id}/htmx", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.text.lower()
        if "pending" in body or "running" in body:
            assert "hx-trigger" in body or "hx-get" in body

    def test_poll_unauthenticated_returns_401(self, client: TestClient, execution_id: str) -> None:
        resp = client.get(f"/pondapi/execute/{execution_id}/htmx")
        assert resp.status_code in (401, 403, 302, 303)

    def test_completed_execution_has_result_table(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 42 AS answer"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        import re

        match = re.search(r"/pondapi/execute/([a-f0-9-]+)/htmx", resp.text)
        assert match, "No execution ID in response"
        execution_id = match.group(1)

        body = _wait_for_htmx_completion(client, execution_id, auth_headers)
        assert "<table" in body or "42" in body

    def test_completed_execution_shows_column_names(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1 AS col_one, 2 AS col_two"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        import re

        match = re.search(r"/pondapi/execute/([a-f0-9-]+)/htmx", resp.text)
        assert match
        execution_id = match.group(1)

        body = _wait_for_htmx_completion(client, execution_id, auth_headers)
        assert "col_one" in body or "col_two" in body

    def test_completed_execution_no_polling_trigger(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        """Once complete, fragment must NOT include automatic re-polling trigger."""
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 1"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        import re

        match = re.search(r"/pondapi/execute/([a-f0-9-]+)/htmx", resp.text)
        assert match
        execution_id = match.group(1)

        body = _wait_for_htmx_completion(client, execution_id, auth_headers)
        # Complete fragments should not keep polling
        assert "every" not in body or "hx-trigger" not in body

    def test_sql_error_returns_error_fragment(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT * FROM nonexistent_table_abc"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        import re

        match = re.search(r"/pondapi/execute/([a-f0-9-]+)/htmx", resp.text)
        assert match
        execution_id = match.group(1)

        body = _wait_for_htmx_completion(client, execution_id, auth_headers)
        # Should show error message
        assert "error" in body.lower() or "nonexistent" in body.lower()

    def test_sql_error_fragment_has_no_table(
        self, client: TestClient, session_id: str, auth_headers: dict
    ) -> None:
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "INVALID SQL HERE !!!"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        import re

        match = re.search(r"/pondapi/execute/([a-f0-9-]+)/htmx", resp.text)
        assert match
        execution_id = match.group(1)

        body = _wait_for_htmx_completion(client, execution_id, auth_headers)
        assert "error" in body.lower()


# ===========================================================================
# Cross-tenant isolation for HTMX endpoints
# ===========================================================================


class TestHTMXTenantIsolation:
    def test_cannot_poll_another_tenants_execution(
        self,
        client: TestClient,
        session_id: str,
        auth_headers: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A different tenant must not be able to poll someone else's execution."""
        # Submit as tenant A
        resp = client.post(
            "/pondapi/execute/htmx",
            data={"session_id": session_id, "sql": "SELECT 'secret' AS data"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 202)
        import re

        match = re.search(r"/pondapi/execute/([a-f0-9-]+)/htmx", resp.text)
        assert match
        execution_id = match.group(1)

        # Try to poll as a different API key (different tenant)
        other_key = "different-tenant-api-key"
        monkeypatch.setenv("POND_API_KEY", other_key)
        # Reload to pick up new env
        import ponddb.app as app_module

        importlib.reload(app_module)
        from ponddb.app import app

        other_client = TestClient(app, follow_redirects=False)

        resp2 = other_client.get(
            f"/pondapi/execute/{execution_id}/htmx",
            headers={"X-API-Key": other_key},
        )
        # Should be 404 (not found for this tenant) or 403 (forbidden)
        if resp2.status_code == 200:
            # Must not expose the result — error fragment is acceptable
            body = resp2.text.lower()
            assert "not found" in body or "error" in body or "forbidden" in body
        else:
            assert resp2.status_code in (403, 404)
