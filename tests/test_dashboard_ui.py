"""Tests for the refactored SaaS dashboard UI.

Verifies that the new template infrastructure (Pico.css, pond.css, sidebar,
macros, HTMX) is correctly wired across all pages.
"""

import base64
import hashlib
import hmac
import importlib
import json

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-dashboard-ui-key"
ADMIN_TENANT = "test-dashboard-admin"
SESSION_SECRET = "test-dashboard-session-secret"


def _sign_session(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-dashboard-ui")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    cookie = _sign_session({"tenant_id": ADMIN_TENANT, "role": "admin"})
    client.cookies.set("pond_session", cookie)
    return client


@pytest.fixture
def logged_in_client(client: TestClient) -> TestClient:
    cookie = _sign_session({"tenant_id": "default"})
    client.cookies.set("pond_session", cookie)
    return client


# ===========================================================================
# CSS integration (pond.css — Pico CSS removed)
# ===========================================================================


class TestCSSIntegration:
    def test_landing_has_inline_styles(self, client: TestClient) -> None:
        """Landing page uses self-contained inline styles."""
        body = client.get("/").text
        assert "<style>" in body

    def test_landing_no_pico_css(self, client: TestClient) -> None:
        """Landing page does not load Pico CSS (removed to avoid conflicts)."""
        body = client.get("/").text.lower()
        assert "picocss" not in body

    def test_login_has_pond_css(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "pond.css" in body

    def test_dashboard_has_pond_css(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text
        assert "pond.css" in body

    def test_editor_has_pond_css(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "pond.css" in body

    def test_admin_has_pond_css(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "pond.css" in body


# ===========================================================================
# HTMX loaded on auth pages
# ===========================================================================


class TestHTMXLoaded:
    def test_dashboard_loads_htmx(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text
        assert "htmx" in body.lower()

    def test_admin_loads_htmx(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "htmx" in body.lower()


# ===========================================================================
# Sidebar navigation present on auth pages
# ===========================================================================


class TestSidebarNav:
    def test_dashboard_has_sidebar(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text
        assert "sidebar" in body.lower()

    def test_dashboard_sidebar_has_editor_link(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text
        assert "/editor" in body

    def test_dashboard_sidebar_has_admin_link(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text
        assert "/admin" in body

    def test_admin_has_sidebar(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "sidebar" in body.lower()


# ===========================================================================
# Stat cards with correct classes
# ===========================================================================


class TestStatCards:
    def test_dashboard_has_stat_cards(self, logged_in_client: TestClient) -> None:
        body = logged_in_client.get("/dashboard").text
        assert "stat-card" in body or "stat-value" in body

    def test_usage_page_has_stat_cards(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text
        assert "stat-card" in body or "stat-value" in body


# ===========================================================================
# Badge rendering
# ===========================================================================


class TestBadges:
    def test_invites_page_uses_badge_class(self, admin_client: TestClient) -> None:
        # Create an invite first
        admin_client.post(
            "/admin/invites",
            data={"email": "badge-test@example.com", "role": "member", "expires_in_hours": "168"},
        )
        body = admin_client.get("/admin/invites").text
        assert "badge" in body


# ===========================================================================
# Breadcrumbs present
# ===========================================================================


class TestBreadcrumbs:
    def test_admin_home_has_breadcrumb(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "breadcrumb" in body.lower() or "/dashboard" in body

    def test_admin_invites_has_breadcrumb(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/invites").text
        assert "breadcrumb" in body.lower()

    def test_admin_namespaces_has_breadcrumb(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/namespaces").text
        assert "breadcrumb" in body.lower()


# ===========================================================================
# Static file serving
# ===========================================================================


class TestStaticFiles:
    def test_pond_css_accessible(self, client: TestClient) -> None:
        resp = client.get("/static/pond.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_pond_css_has_variables(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert "--pond-primary" in body

    def test_pond_css_has_sidebar_styles(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".sidebar" in body

    def test_pond_css_has_badge_styles(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".badge" in body

    def test_pond_css_has_data_table_styles(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert "data-table" in body
