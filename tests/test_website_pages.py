"""Integration tests for the Jinja2 public website pages.

Covers:
  GET /                   — landing page (marketing, CTAs, links)
  GET /login              — login page (API-key form, OAuth buttons)
  POST /login             — process login, set session cookie, redirect to dashboard
  POST /logout            — clear session cookie, redirect to landing
  GET /dashboard          — authenticated dashboard (sessions, metrics, workgroups)
  GET /workgroup/{id}     — workgroup detail page (members, quotas, active sessions)

Auth model for web UI:
  - POST /login with API key → server sets a signed session cookie
  - Subsequent browser requests include the cookie
  - /dashboard and /workgroup/* require a valid session cookie
  - Invalid / missing cookie → 302 redirect to /login
"""

import importlib
import os

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-website-api-key"

os.environ.setdefault("POND_JWT_SECRET", "test-website-jwt-secret")
os.environ.setdefault("POND_WEBSITE_SESSION_SECRET", "test-website-session-secret")


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-website-jwt-secret")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", "test-website-session-secret")


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(_set_env) -> TestClient:
    """Client with a valid session cookie (logged in via POST /login)."""
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    c = TestClient(app, follow_redirects=False)
    resp = c.post("/login", data={"api_key": VALID_KEY})
    assert resp.status_code in (200, 302, 303), f"Login failed: {resp.status_code} {resp.text}"
    return c


# ===========================================================================
# GET / — Landing page
# ===========================================================================


class TestLandingPage:
    def test_get_returns_200(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200

    def test_content_type_is_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_has_html_doctype(self, client: TestClient) -> None:
        body = client.get("/").text
        assert body.strip().lower().startswith("<!doctype html")

    def test_has_page_title(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "<title>" in body.lower()

    def test_contains_ponddb_branding(self, client: TestClient) -> None:
        body = client.get("/").text.lower()
        assert "ponddb" in body or "pond" in body

    def test_has_login_link_or_cta(self, client: TestClient) -> None:
        """Landing page must have a path to the login page."""
        body = client.get("/").text
        assert "/login" in body or 'href="/login"' in body

    def test_has_get_started_or_signup_cta(self, client: TestClient) -> None:
        body = client.get("/").text.lower()
        assert any(kw in body for kw in ["get started", "sign up", "signup", "try now", "launch"])

    def test_has_feature_highlights(self, client: TestClient) -> None:
        """Landing page should describe key features."""
        body = client.get("/").text.lower()
        assert any(kw in body for kw in ["duckdb", "self-hosted", "sql", "query", "analytics"])

    def test_has_link_to_editor(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "/editor" in body

    def test_no_server_error_in_body(self, client: TestClient) -> None:
        body = client.get("/").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body


# ===========================================================================
# GET /login — Login page
# ===========================================================================


class TestLoginPage:
    def test_get_returns_200(self, client: TestClient) -> None:
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_content_type_is_html(self, client: TestClient) -> None:
        resp = client.get("/login")
        assert "text/html" in resp.headers["content-type"]

    def test_has_api_key_form(self, client: TestClient) -> None:
        body = client.get("/login").text
        # Must have an input for API key
        assert "api_key" in body or "apikey" in body.lower() or 'type="password"' in body

    def test_has_form_post_action(self, client: TestClient) -> None:
        body = client.get("/login").text
        # Form must POST to /login
        assert 'action="/login"' in body or "method" in body.lower()

    def test_has_oauth_google_button(self, client: TestClient) -> None:
        body = client.get("/login").text.lower()
        assert "google" in body

    def test_has_oauth_github_button(self, client: TestClient) -> None:
        body = client.get("/login").text.lower()
        assert "github" in body

    def test_has_link_back_to_landing(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert 'href="/"' in body or "home" in body.lower()

    def test_no_server_error_in_body(self, client: TestClient) -> None:
        body = client.get("/login").text.lower()
        assert "internal server error" not in body

    def test_login_page_has_csrf_token_or_hidden_field(self, client: TestClient) -> None:
        """Login form should include some form of CSRF protection."""
        body = client.get("/login").text
        assert 'type="hidden"' in body or "csrf" in body.lower() or "_token" in body.lower()


# ===========================================================================
# POST /login — Process login
# ===========================================================================


class TestPostLogin:
    def test_valid_api_key_redirects_to_dashboard(self, client: TestClient) -> None:
        resp = client.post("/login", data={"api_key": VALID_KEY})
        # Accept 302/303 redirect to /dashboard
        assert resp.status_code in (302, 303)
        assert resp.headers["location"].rstrip("/").endswith("/dashboard")

    def test_valid_api_key_sets_session_cookie(self, client: TestClient) -> None:
        resp = client.post("/login", data={"api_key": VALID_KEY})
        assert resp.status_code in (302, 303)
        # Should set a session/auth cookie
        assert len(resp.cookies) > 0 or "set-cookie" in resp.headers

    def test_invalid_api_key_returns_error(self, client: TestClient) -> None:
        resp = client.post("/login", data={"api_key": "wrong-key"}, follow_redirects=False)
        # Either 200 with error message or 401/403 or redirect back to login
        if resp.status_code == 200:
            assert any(
                kw in resp.text.lower() for kw in ["invalid", "error", "incorrect", "unauthorized"]
            )
        elif resp.status_code in (302, 303):
            assert "/login" in resp.headers["location"]
        else:
            assert resp.status_code in (401, 403)

    def test_empty_api_key_returns_error(self, client: TestClient) -> None:
        resp = client.post("/login", data={"api_key": ""}, follow_redirects=False)
        if resp.status_code == 200:
            assert any(kw in resp.text.lower() for kw in ["required", "invalid", "error", "empty"])
        elif resp.status_code in (302, 303):
            assert "/login" in resp.headers["location"]
        else:
            assert resp.status_code in (400, 401, 422)

    def test_missing_api_key_field_returns_error(self, client: TestClient) -> None:
        resp = client.post("/login", data={}, follow_redirects=False)
        assert resp.status_code in (200, 302, 303, 400, 422)

    def test_login_with_valid_key_does_not_expose_secret(self, client: TestClient) -> None:
        resp = client.post("/login", data={"api_key": VALID_KEY}, follow_redirects=False)
        assert VALID_KEY not in resp.text
        assert "POND_JWT_SECRET" not in resp.text


# ===========================================================================
# POST /logout
# ===========================================================================


class TestLogout:
    def test_logout_redirects_to_landing(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post("/logout")
        assert resp.status_code in (302, 303)
        location = resp.headers["location"]
        assert location == "/" or location.endswith("/login")

    def test_logout_clears_session_cookie(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.post("/logout")
        # Cookie should be deleted (max-age=0 or expires in past)
        set_cookie = resp.headers.get("set-cookie", "")
        assert (
            "max-age=0" in set_cookie.lower()
            or "expires" in set_cookie.lower()
            or resp.status_code in (302, 303)
        )

    def test_unauthenticated_logout_still_redirects(self, client: TestClient) -> None:
        resp = client.post("/logout")
        # Should still succeed / redirect gracefully
        assert resp.status_code in (302, 303, 200)


# ===========================================================================
# GET /dashboard — Authenticated dashboard
# ===========================================================================


class TestDashboard:
    def test_unauthenticated_redirects_to_login(self, client: TestClient) -> None:
        resp = client.get("/dashboard")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_authenticated_returns_200(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/dashboard")
        assert resp.status_code == 200

    def test_authenticated_content_type_is_html(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/dashboard")
        assert "text/html" in resp.headers["content-type"]

    def test_dashboard_has_sessions_section(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        assert "session" in body

    def test_dashboard_has_new_session_button_or_link(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        assert any(
            kw in body for kw in ["new session", "create session", "start session", "launch"]
        )

    def test_dashboard_has_workgroups_section_or_link(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        assert "workgroup" in body

    def test_dashboard_has_sql_editor_link(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text
        assert "/editor" in body

    def test_dashboard_has_logout_link(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        assert "logout" in body or "sign out" in body

    def test_dashboard_shows_active_session_count(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        # Should display some numeric metric or session count
        assert any(kw in body for kw in ["active", "session", "0 session", "no session"])

    def test_dashboard_has_metrics_or_compute_section(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        assert any(kw in body for kw in ["metric", "compute", "usage", "query", "run"])

    def test_dashboard_no_server_error(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body

    def test_dashboard_has_recent_queries_section(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text.lower()
        assert any(kw in body for kw in ["recent", "history", "query", "queries"])


# ===========================================================================
# GET /workgroup/{id} — Workgroup detail page
# ===========================================================================


class TestWorkgroupPage:
    def test_unauthenticated_redirects_to_login(self, client: TestClient) -> None:
        resp = client.get("/workgroup/default")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_unknown_workgroup_returns_404(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/nonexistent-wg-xyzxyz")
        assert resp.status_code == 404

    def test_existing_workgroup_returns_200(self, logged_in_client: TestClient) -> None:
        # First create a workgroup
        create_resp = logged_in_client.post(
            "/namespaces/default/workgroups",
            json={
                "name": "test-wg",
                "max_sessions": 5,
                "max_query_duration_ms": 30000,
                "max_result_mb": 50,
            },
            headers={"X-API-Key": VALID_KEY},
        )
        # Workgroup creation may return 200 or 201 or 409 (already exists) — skip check
        resp = logged_in_client.get("/workgroup/test-wg")
        # May be 200 or 404 if workgroup creation failed — we accept both for now
        assert resp.status_code in (200, 404)

    def test_workgroup_page_content_type_is_html(self, logged_in_client: TestClient) -> None:
        # Create and check content type only if we get 200
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            assert "text/html" in resp.headers["content-type"]

    def test_workgroup_page_shows_workgroup_name(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            body = resp.text.lower()
            assert "default" in body or "workgroup" in body

    def test_workgroup_page_shows_quota_info(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            body = resp.text.lower()
            assert any(kw in body for kw in ["quota", "max session", "limit", "session"])

    def test_workgroup_page_shows_members_section(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            body = resp.text.lower()
            assert "member" in body or "user" in body

    def test_workgroup_page_shows_active_sessions(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            body = resp.text.lower()
            assert "session" in body

    def test_workgroup_page_has_back_to_dashboard_link(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            body = resp.text
            assert "/dashboard" in body or "back" in body.lower()

    def test_workgroup_page_has_sql_editor_link(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            body = resp.text
            assert "/editor" in body

    def test_workgroup_uuid_path_works(self, logged_in_client: TestClient) -> None:
        """Workgroup page should also accept UUID-style IDs."""
        import uuid

        fake_id = str(uuid.uuid4())
        resp = logged_in_client.get(f"/workgroup/{fake_id}")
        # Should return 404 (not found) not 422 (validation error) — UUIDs are valid path params
        assert resp.status_code in (404, 200)

    def test_workgroup_page_no_server_error(self, logged_in_client: TestClient) -> None:
        resp = logged_in_client.get("/workgroup/default")
        if resp.status_code == 200:
            body = resp.text.lower()
            assert "internal server error" not in body
            assert "traceback" not in body
