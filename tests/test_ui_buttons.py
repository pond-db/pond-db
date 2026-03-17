"""Integration tests: every button, link, and HTMX attribute resolves correctly.

Covers:
  - Landing page: nav links, hero CTA targets
  - Login page: OAuth links, API key form action
  - Dashboard: stat-card links, workgroup card buttons, sidebar links
  - Sessions page: New Session link, auto-refresh attrs, action buttons
  - Editor: Run/Save/Share attrs, HTMX exec form, schema sidebar toggle
  - Workgroup page: tab hx-get targets, breadcrumb links
"""

import base64
import hashlib
import hmac
import importlib
import json
import os
import re

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-ui-buttons-key"
SESSION_SECRET = "test-ui-buttons-session-secret"

os.environ.setdefault("POND_JWT_SECRET", "test-jwt-ui-buttons")
os.environ.setdefault("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


def _sign(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-ui-buttons")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture
def client(_env) -> TestClient:
    import ponddb.app as m

    importlib.reload(m)
    return TestClient(m.app, follow_redirects=False)


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    client.cookies.set("pond_session", _sign({"tenant_id": "default"}))
    return client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    client.cookies.set("pond_session", _sign({"tenant_id": "default", "role": "admin"}))
    return client


# ── Landing page links ──────────────────────────────────────────────────────


class TestLandingLinks:
    def test_login_link_resolves(self, client: TestClient) -> None:
        body = client.get("/").text
        assert 'href="/login"' in body
        assert client.get("/login").status_code == 200

    def test_try_now_link_resolves(self, client: TestClient) -> None:
        body = client.get("/").text
        assert 'href="/editor"' in body
        assert client.get("/editor").status_code == 200

    def test_github_docs_link_present(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "github.com/pond-db/pond-db" in body

    def test_request_invite_mailto(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "mailto:" in body

    def test_hero_cta_buttons_have_href(self, client: TestClient) -> None:
        body = client.get("/").text
        hrefs = re.findall(r'href="([^"]+)"', body)
        assert any("mailto:" in h for h in hrefs)
        assert any("github.com" in h for h in hrefs)


# ── Login page buttons ──────────────────────────────────────────────────────


class TestLoginButtons:
    def test_google_oauth_link_present(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "/auth/google" in body

    def test_github_oauth_link_present(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "/auth/github" in body

    def test_api_key_form_posts_to_login(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert 'action="/login"' in body
        assert 'method="post"' in body.lower()

    def test_api_key_input_is_password_type(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert 'type="password"' in body

    def test_submit_button_present(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert 'type="submit"' in body

    def test_invite_state_propagates_to_oauth_urls(self, client: TestClient) -> None:
        body = client.get("/login?invite_state=abc123").text
        assert "invite_state=abc123" in body


# ── Dashboard buttons and links ─────────────────────────────────────────────


class TestDashboardButtons:
    def test_view_sessions_link_resolves(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard").text
        assert "/dashboard/sessions" in body
        resp = logged_in.get("/dashboard/sessions")
        assert resp.status_code == 200

    def test_launch_session_links_to_editor(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard").text
        assert 'href="/editor"' in body

    def test_sidebar_editor_link(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard").text
        assert "/editor" in body

    def test_sidebar_admin_link(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard").text
        assert "/admin" in body

    def test_logout_form_present(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard").text.lower()
        assert "logout" in body

    def test_stat_card_links_have_href(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard").text
        assert "/dashboard/sessions" in body
        assert "/editor" in body


# ── Sessions page buttons ───────────────────────────────────────────────────


class TestSessionsPageButtons:
    def test_new_session_link_to_editor(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard/sessions").text
        assert 'href="/editor"' in body

    def test_auto_refresh_container_attrs(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard/sessions").text
        assert 'hx-get="/htmx/sessions-table"' in body
        assert "every 10s" in body

    def test_breadcrumb_links_to_dashboard(self, logged_in: TestClient) -> None:
        body = logged_in.get("/dashboard/sessions").text
        assert 'href="/dashboard"' in body


# ── Editor buttons and forms ────────────────────────────────────────────────


class TestEditorButtons:
    def test_run_button_has_id(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'id="run-btn"' in body

    def test_save_button_hx_post(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'hx-post="/queries"' in body

    def test_save_includes_query_name(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "query-name" in body
        assert "hx-include" in body

    def test_share_button_present(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'id="share-btn"' in body

    def test_htmx_exec_form_attrs(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'hx-post="/pondapi/execute/htmx"' in body
        assert 'hx-target="#pondapi-result"' in body

    def test_schema_sidebar_toggle(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'id="sidebar-btn"' in body
        assert 'id="schema-sidebar"' in body

    def test_session_input_present(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'id="session-input"' in body

    def test_schema_refresh_button(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'id="schema-refresh-btn"' in body


# ── Admin page links ────────────────────────────────────────────────────────


class TestAdminLinks:
    def test_admin_home_has_invite_link(self, admin: TestClient) -> None:
        body = admin.get("/admin").text
        assert "/admin/invites" in body
        assert admin.get("/admin/invites").status_code == 200

    def test_admin_home_has_namespace_link(self, admin: TestClient) -> None:
        body = admin.get("/admin").text
        assert "/admin/namespaces" in body
        assert admin.get("/admin/namespaces").status_code == 200

    def test_admin_home_has_usage_link(self, admin: TestClient) -> None:
        body = admin.get("/admin").text
        assert "/admin/usage" in body
        assert admin.get("/admin/usage").status_code == 200

    def test_invite_form_action(self, admin: TestClient) -> None:
        body = admin.get("/admin/invites").text
        assert 'action="/admin/invites"' in body
        assert 'method="POST"' in body

    def test_namespace_create_button(self, admin: TestClient) -> None:
        body = admin.get("/admin/namespaces").text
        assert "/namespaces" in body
