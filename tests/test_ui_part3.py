"""Tests for UI refactor Part 3: landing page, login page, editor upgrade.

Verifies:
  - Landing page: hero, features, code example, no auth needed
  - Login page: OAuth buttons, invite banner, no auth needed
  - Editor: save button, metadata area, schema browser, query name input
  - CSS additions for landing, login, editor
"""

import base64
import hashlib
import hmac
import importlib
import json

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-ui-part3-key"
SESSION_SECRET = "test-ui-part3-session-secret"


def _sign_session(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-ui-part3")
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


# ===========================================================================
# Landing page tests
# ===========================================================================


class TestLandingPage:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/").status_code == 200

    def test_contains_serverless_duckdb(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "Serverless DuckDB" in body

    def test_contains_request_invite(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "Request Invite" in body

    def test_request_invite_mailto(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "mailto:" in body
        assert "2014houtianlu@gmail.com" in body

    def test_github_link(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "github.com/DatabaseCompany/db-engine" in body

    def test_no_auth_required(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "login" not in resp.headers.get("location", "").lower()

    def test_has_pico_css(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "pico" in body.lower()

    def test_has_pond_css(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "pond.css" in body


class TestLandingFeatures:
    def test_has_pondapi_card(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "PondAPI" in body

    def test_has_workgroup_card(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "Workgroup Isolation" in body

    def test_has_selfhosted_card(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "Self-Hosted" in body

    def test_has_feature_grid(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "feature-card" in body

    def test_three_feature_cards(self, client: TestClient) -> None:
        body = client.get("/").text
        assert body.count("feature-card") >= 3


class TestLandingCodeExample:
    def test_has_code_section(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "code-section" in body

    def test_has_curl_example(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "curl" in body.lower()
        assert "/pondapi/execute" in body

    def test_has_code_caption(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "One POST to submit" in body


class TestLandingFooter:
    def test_has_footer(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "DatabaseCompany" in body

    def test_footer_has_mit(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "MIT" in body


# ===========================================================================
# Login page tests
# ===========================================================================


class TestLoginPage:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/login").status_code == 200

    def test_has_google_button(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "Google" in body
        assert "/auth/google" in body

    def test_has_github_button(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "GitHub" in body
        assert "/auth/github" in body

    def test_has_oauth_btn_class(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "oauth-btn" in body

    def test_has_sign_in_heading(self, client: TestClient) -> None:
        body = client.get("/login").text.lower()
        assert "sign in" in body

    def test_no_auth_required(self, client: TestClient) -> None:
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_has_login_card(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "login-card" in body

    def test_has_pico_css(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "pico" in body.lower()


class TestLoginInviteBanner:
    def test_invite_banner_shown(self, client: TestClient) -> None:
        body = client.get("/login?invite_state=abc123&namespace_name=MyTeam").text
        assert "invite-banner" in body
        assert "MyTeam" in body

    def test_no_banner_without_invite(self, client: TestClient) -> None:
        body = client.get("/login").text
        assert "invite-banner" not in body

    def test_invite_state_in_oauth_links(self, client: TestClient) -> None:
        body = client.get("/login?invite_state=abc123&namespace_name=MyTeam").text
        assert "invite_state=abc123" in body

    def test_google_oauth_has_invite_state(self, client: TestClient) -> None:
        body = client.get("/login?invite_state=xyz").text
        assert "/auth/google?invite_state=xyz" in body

    def test_github_oauth_has_invite_state(self, client: TestClient) -> None:
        body = client.get("/login?invite_state=xyz").text
        assert "/auth/github?invite_state=xyz" in body


# ===========================================================================
# Editor tests
# ===========================================================================


class TestEditorPage:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/editor").status_code == 200

    def test_has_save_button(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "Save Query" in body

    def test_save_has_hx_post(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert 'hx-post="/queries"' in body

    def test_has_metadata_area(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "query-metadata" in body

    def test_has_schema_browser(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "schema-sidebar" in body
        assert "Schema Browser" in body

    def test_has_query_name_input(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "query-name" in body
        assert "Untitled query" in body

    def test_has_run_button(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "run-btn" in body

    def test_has_share_button(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "share-btn" in body

    def test_has_action_bar(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "action-bar" in body

    def test_has_codemirror_import(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "codemirror" in body.lower()

    def test_has_pond_css(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "pond.css" in body

    def test_has_htmx(self, client: TestClient) -> None:
        body = client.get("/editor").text
        assert "htmx" in body.lower()


# ===========================================================================
# CSS additions
# ===========================================================================


class TestPart3CSS:
    def test_has_landing_nav(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".landing-nav" in body

    def test_has_hero(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".hero" in body

    def test_has_features_grid(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".features-grid" in body

    def test_has_feature_card(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".feature-card" in body

    def test_has_code_section(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".code-section" in body

    def test_has_login_card(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".login-card" in body

    def test_has_oauth_btn(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".oauth-btn" in body

    def test_has_invite_banner(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".invite-banner" in body

    def test_has_query_metadata(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".query-metadata" in body

    def test_has_action_bar(self, client: TestClient) -> None:
        body = client.get("/static/pond.css").text
        assert ".action-bar" in body
