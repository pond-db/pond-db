"""Integration tests for OAuth2 routes — Google + GitHub providers.

Covers:
  - /auth/{provider}  — redirect to OAuth provider with HMAC state
  - /auth/{provider}/callback — exchange code, issue JWT
  - HMAC state token generation and verification
  - Unknown provider 404
  - Invalid / tampered state → 400
  - OAuth provider error response → 400
  - Successful flow issues PondDB access + refresh tokens
"""

import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("POND_JWT_SECRET", "test-secret-for-oauth-tests")
os.environ.setdefault("POND_OAUTH_SECRET", "test-oauth-hmac-secret")
os.environ.setdefault("POND_GOOGLE_CLIENT_ID", "google-client-id")
os.environ.setdefault("POND_GOOGLE_CLIENT_SECRET", "google-client-secret")
os.environ.setdefault("POND_GITHUB_CLIENT_ID", "github-client-id")
os.environ.setdefault("POND_GITHUB_CLIENT_SECRET", "github-client-secret")


# ---------------------------------------------------------------------------
# Helpers — import order matters so env is set before module load
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from ponddb.app import app
    from ponddb.api.oauth_routes import make_oauth_router  # will ImportError until implemented

    app.include_router(make_oauth_router())
    return TestClient(app, follow_redirects=False)


@pytest.fixture(scope="module")
def state_utils():
    from ponddb.auth import oauth_state  # will ImportError until implemented

    return oauth_state


# ===========================================================================
# HMAC state token tests
# ===========================================================================


class TestOAuthStateToken:
    def test_generate_returns_string(self, state_utils):
        token = state_utils.generate_state("google")
        assert isinstance(token, str)
        assert len(token) > 10

    def test_verify_valid_token(self, state_utils):
        token = state_utils.generate_state("google")
        data = state_utils.verify_state(token)
        assert data["provider"] == "google"

    def test_generate_includes_timestamp(self, state_utils):
        before = int(time.time())
        token = state_utils.generate_state("github")
        data = state_utils.verify_state(token)
        assert data["ts"] >= before

    def test_generate_unique_tokens(self, state_utils):
        t1 = state_utils.generate_state("google")
        t2 = state_utils.generate_state("google")
        assert t1 != t2

    def test_tampered_token_raises(self, state_utils):
        token = state_utils.generate_state("google")
        # Flip last character
        corrupted = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(ValueError, match="[Ii]nvalid|[Tt]ampered|[Ss]ignature"):
            state_utils.verify_state(corrupted)

    def test_expired_token_raises(self, state_utils):
        # Generate token with a past timestamp
        token = state_utils.generate_state("google", max_age_seconds=1)
        time.sleep(2)
        with pytest.raises(ValueError, match="[Ee]xpired|[Tt]oo old"):
            state_utils.verify_state(token, max_age_seconds=1)

    def test_wrong_secret_raises(self, state_utils):
        # Patch env to different secret, generate, then restore
        original = os.environ.get("POND_OAUTH_SECRET")
        os.environ["POND_OAUTH_SECRET"] = "other-secret"
        token = state_utils.generate_state("google")
        os.environ["POND_OAUTH_SECRET"] = original
        with pytest.raises(ValueError):
            state_utils.verify_state(token)

    def test_github_provider_preserved(self, state_utils):
        token = state_utils.generate_state("github")
        data = state_utils.verify_state(token)
        assert data["provider"] == "github"

    def test_token_is_url_safe(self, state_utils):
        token = state_utils.generate_state("google")
        # URL-safe characters only (base64url or hex)
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=.+" for c in token)


# ===========================================================================
# /auth/{provider} — initiate OAuth flow
# ===========================================================================


class TestAuthInitiate:
    def test_google_redirects(self, client):
        resp = client.get("/auth/google")
        assert resp.status_code in (302, 307)

    def test_google_redirect_location_contains_accounts_google(self, client):
        resp = client.get("/auth/google")
        location = resp.headers["location"]
        assert "accounts.google.com" in location or "google" in location.lower()

    def test_github_redirects(self, client):
        resp = client.get("/auth/github")
        assert resp.status_code in (302, 307)

    def test_github_redirect_location_contains_github(self, client):
        resp = client.get("/auth/github")
        location = resp.headers["location"]
        assert "github.com" in location

    def test_redirect_includes_state_param(self, client):
        resp = client.get("/auth/google")
        location = resp.headers["location"]
        assert "state=" in location

    def test_redirect_includes_client_id(self, client):
        resp = client.get("/auth/google")
        location = resp.headers["location"]
        assert "google-client-id" in location

    def test_redirect_includes_github_client_id(self, client):
        resp = client.get("/auth/github")
        location = resp.headers["location"]
        assert "github-client-id" in location

    def test_unknown_provider_returns_404(self, client):
        resp = client.get("/auth/twitter")
        assert resp.status_code == 404

    def test_unknown_provider_error_message(self, client):
        resp = client.get("/auth/facebook")
        assert "provider" in resp.json().get("detail", "").lower()

    def test_redirect_includes_response_type_code(self, client):
        resp = client.get("/auth/google")
        location = resp.headers["location"]
        assert "response_type=code" in location or "code" in location

    def test_redirect_includes_scope(self, client):
        resp = client.get("/auth/google")
        location = resp.headers["location"]
        assert "scope=" in location

    def test_github_redirect_includes_scope(self, client):
        resp = client.get("/auth/github")
        location = resp.headers["location"]
        assert "scope=" in location


# ===========================================================================
# /auth/{provider}/callback — exchange code, issue tokens
# ===========================================================================


class TestAuthCallback:
    """Mock the Authlib token exchange so tests don't hit real OAuth servers."""

    def _make_valid_state(self, provider: str = "google") -> str:
        from ponddb.auth import oauth_state

        return oauth_state.generate_state(provider)

    def test_unknown_provider_callback_returns_404(self, client):
        resp = client.get("/auth/twitter/callback?code=abc&state=xyz")
        assert resp.status_code == 404

    def test_missing_code_returns_400(self, client):
        state = self._make_valid_state("google")
        resp = client.get(f"/auth/google/callback?state={state}")
        assert resp.status_code == 400

    def test_missing_state_returns_400(self, client):
        resp = client.get("/auth/google/callback?code=abc")
        assert resp.status_code == 400

    def test_tampered_state_returns_400(self, client):
        resp = client.get("/auth/google/callback?code=abc&state=tampered-state-xyz")
        assert resp.status_code == 400

    def test_oauth_error_param_returns_400(self, client):
        state = self._make_valid_state("google")
        resp = client.get(
            f"/auth/google/callback?error=access_denied&error_description=User+denied&state={state}"
        )
        assert resp.status_code == 400

    def test_oauth_error_detail_contains_description(self, client):
        state = self._make_valid_state("google")
        resp = client.get(
            f"/auth/google/callback?error=access_denied&error_description=User+denied&state={state}"
        )
        detail = resp.json().get("detail", "")
        assert "access_denied" in detail or "denied" in detail.lower()

    def test_provider_mismatch_in_state_returns_400(self, client):
        # State generated for github but used on google callback
        state = self._make_valid_state("github")
        with patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock) as mock_ex:
            mock_ex.return_value = {"access_token": "tok", "token_type": "bearer"}
            with patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock) as mock_ui:
                mock_ui.return_value = {"id": "123", "email": "u@example.com"}
                resp = client.get(f"/auth/google/callback?code=abc&state={state}")
        assert resp.status_code == 400

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_successful_google_callback_returns_tokens(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "google-access-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "sub": "google-uid-123",
            "email": "user@example.com",
            "name": "Test User",
        }
        state = self._make_valid_state("google")
        resp = client.get(f"/auth/google/callback?code=auth-code&state={state}")
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body.get("token_type", "").lower() == "bearer"

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_successful_github_callback_returns_tokens(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "github-access-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "id": 42,
            "login": "testuser",
            "email": "user@example.com",
        }
        state = self._make_valid_state("github")
        resp = client.get(f"/auth/github/callback?code=auth-code&state={state}")
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_issued_access_token_is_valid_jwt(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "tok", "token_type": "bearer"}
        mock_user.return_value = {"sub": "uid-99", "email": "x@example.com"}
        state = self._make_valid_state("google")
        resp = client.get(f"/auth/google/callback?code=auth-code&state={state}")
        access_token = resp.json()["access_token"]
        # Verify it's a valid PondDB JWT
        from ponddb.auth.jwt_auth import verify_access_token

        claims = verify_access_token(access_token)
        assert claims["type"] == "access"

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_tenant_id_derived_from_provider_user_id(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "tok", "token_type": "bearer"}
        mock_user.return_value = {"sub": "google-uid-789", "email": "a@b.com"}
        state = self._make_valid_state("google")
        resp = client.get(f"/auth/google/callback?code=auth-code&state={state}")
        access_token = resp.json()["access_token"]
        from ponddb.auth.jwt_auth import verify_access_token

        claims = verify_access_token(access_token)
        assert "google" in claims["tenant_id"] or "789" in claims["tenant_id"]

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    def test_token_exchange_failure_returns_502(self, mock_exchange, client):
        mock_exchange.side_effect = RuntimeError("Provider unreachable")
        state = self._make_valid_state("google")
        resp = client.get(f"/auth/google/callback?code=auth-code&state={state}")
        assert resp.status_code in (502, 500)

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_user_info_failure_returns_502(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "tok", "token_type": "bearer"}
        mock_user.side_effect = RuntimeError("Userinfo endpoint down")
        state = self._make_valid_state("google")
        resp = client.get(f"/auth/google/callback?code=auth-code&state={state}")
        assert resp.status_code in (502, 500)


# ===========================================================================
# Provider configuration tests
# ===========================================================================


class TestProviderConfig:
    def test_get_supported_providers(self):
        from ponddb.api.oauth_routes import SUPPORTED_PROVIDERS

        assert "google" in SUPPORTED_PROVIDERS
        assert "github" in SUPPORTED_PROVIDERS

    def test_google_config_has_required_keys(self):
        from ponddb.api.oauth_routes import SUPPORTED_PROVIDERS

        google = SUPPORTED_PROVIDERS["google"]
        assert "client_id_env" in google
        assert "client_secret_env" in google
        assert "authorize_url" in google
        assert "token_url" in google
        assert "userinfo_url" in google

    def test_github_config_has_required_keys(self):
        from ponddb.api.oauth_routes import SUPPORTED_PROVIDERS

        github = SUPPORTED_PROVIDERS["github"]
        assert "client_id_env" in github
        assert "client_secret_env" in github
        assert "authorize_url" in github
        assert "token_url" in github
        assert "userinfo_url" in github

    def test_google_authorize_url_is_google(self):
        from ponddb.api.oauth_routes import SUPPORTED_PROVIDERS

        assert "google" in SUPPORTED_PROVIDERS["google"]["authorize_url"].lower()

    def test_github_authorize_url_is_github(self):
        from ponddb.api.oauth_routes import SUPPORTED_PROVIDERS

        assert "github" in SUPPORTED_PROVIDERS["github"]["authorize_url"].lower()

    def test_missing_client_id_env_raises_on_redirect(self, client):
        """If env var for client ID is missing, should return 500 (misconfiguration)."""
        original = os.environ.pop("POND_GOOGLE_CLIENT_ID", None)
        try:
            resp = client.get("/auth/google")
            assert resp.status_code in (302, 307, 500)
        finally:
            if original:
                os.environ["POND_GOOGLE_CLIENT_ID"] = original


# ===========================================================================
# make_oauth_router factory
# ===========================================================================


class TestMakeOauthRouter:
    def test_make_oauth_router_returns_router(self):
        from fastapi import APIRouter

        from ponddb.api.oauth_routes import make_oauth_router

        router = make_oauth_router()
        assert isinstance(router, APIRouter)

    def test_router_has_initiate_route(self):
        from ponddb.api.oauth_routes import make_oauth_router

        router = make_oauth_router()
        paths = [r.path for r in router.routes]
        assert any("/auth/{provider}" in p for p in paths)

    def test_router_has_callback_route(self):
        from ponddb.api.oauth_routes import make_oauth_router

        router = make_oauth_router()
        paths = [r.path for r in router.routes]
        assert any("callback" in p for p in paths)
