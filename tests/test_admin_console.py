"""Integration tests for Admin Console HTML pages.

Admin console pages give administrators a browser-based UI for:
  - Invite management  (/admin/invites)
  - Namespace/workgroup overview (/admin/namespaces)
  - Usage dashboard (/admin/usage)
  - Quota editing (/admin/workgroups/{wg_id}/quota)

Auth model:
  - All /admin/* pages require a valid signed session cookie
  - Session cookie must carry role="admin" (set at login)
  - No cookie / bad cookie → 302 redirect to /login
  - Non-admin cookie → 403 Forbidden

Test strategy:
  - Tests use a pre-built admin session cookie injected directly (bypasses POST /login)
  - A member-level cookie is used for 403 checks
  - The app is reloaded fresh per test class to avoid shared state
"""

import base64
import hashlib
import hmac
import importlib
import json
import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JWT_SECRET = "test-admin-console-jwt-secret"
SESSION_SECRET = "test-admin-console-session-secret"
API_KEY = "test-admin-console-api-key"
COOKIE_NAME = "pond_session"

ADMIN_TENANT = "admin-tenant-001"
MEMBER_TENANT = "member-tenant-002"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign_session(data: dict, secret: str = SESSION_SECRET) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _admin_cookie() -> str:
    return _sign_session({"tenant_id": ADMIN_TENANT, "role": "admin"})


def _member_cookie() -> str:
    return _sign_session({"tenant_id": MEMBER_TENANT, "role": "member"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", SESSION_SECRET)


@pytest.fixture
def client(env_setup) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def admin_client(env_setup) -> TestClient:
    """TestClient with admin session cookie pre-set."""
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(COOKIE_NAME, _admin_cookie())
    return c


@pytest.fixture
def member_client(env_setup) -> TestClient:
    """TestClient with non-admin member session cookie pre-set."""
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(COOKIE_NAME, _member_cookie())
    return c


def _admin_jwt_headers() -> dict[str, str]:
    from ponddb.jwt_auth import create_access_token
    token = create_access_token(ADMIN_TENANT, role="admin")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Helper: create a namespace + workgroup via the API
# ---------------------------------------------------------------------------


def _create_ns_and_wg(admin_client: TestClient) -> tuple[str, str]:
    """Create one namespace and one workgroup; return (ns_id, wg_id)."""
    ns_resp = admin_client.post(
        "/namespaces",
        json={"name": "test-ns", "description": "Test namespace"},
        headers=_admin_jwt_headers(),
    )
    assert ns_resp.status_code in (201, 409), ns_resp.text
    if ns_resp.status_code == 201:
        ns_id = ns_resp.json()["id"]
    else:
        nses = admin_client.get("/namespaces", headers=_admin_jwt_headers()).json()
        ns_id = next(n["id"] for n in nses if n["name"] == "test-ns")

    wg_resp = admin_client.post(
        "/workgroups",
        json={
            "name": "test-wg",
            "namespace_id": ns_id,
            "description": "Test workgroup",
            "quota": {"max_sessions": 10, "max_query_duration_ms": 60000, "max_result_mb": 100},
        },
        headers=_admin_jwt_headers(),
    )
    assert wg_resp.status_code in (201, 409), wg_resp.text
    if wg_resp.status_code == 201:
        wg_id = wg_resp.json()["id"]
    else:
        wgs = admin_client.get("/workgroups", headers=_admin_jwt_headers()).json()
        wg_id = next(w["id"] for w in wgs if w["name"] == "test-wg")

    return ns_id, wg_id


# ===========================================================================
# GET /admin — Admin console home
# ===========================================================================


class TestAdminHome:
    def test_unauthenticated_redirects_to_login(self, client: TestClient) -> None:
        resp = client.get("/admin")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.get("/admin")
        assert resp.status_code in (302, 303, 403)

    def test_admin_returns_200(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin")
        assert resp.status_code == 200

    def test_admin_returns_html(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin")
        assert "text/html" in resp.headers["content-type"]

    def test_admin_home_has_page_title(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "<title>" in body.lower()

    def test_admin_home_has_admin_heading(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text.lower()
        assert "admin" in body

    def test_admin_home_has_invites_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "/admin/invites" in body

    def test_admin_home_has_namespaces_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "/admin/namespaces" in body

    def test_admin_home_has_usage_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "/admin/usage" in body

    def test_admin_home_no_server_error(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body

    def test_admin_home_has_back_to_dashboard_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin").text
        assert "/dashboard" in body


# ===========================================================================
# GET /admin/invites — Invite management page
# ===========================================================================


class TestAdminInvitesPage:
    def test_unauthenticated_redirects_to_login(self, client: TestClient) -> None:
        resp = client.get("/admin/invites")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.get("/admin/invites")
        assert resp.status_code in (302, 303, 403)

    def test_admin_returns_200(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin/invites")
        assert resp.status_code == 200

    def test_admin_returns_html(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin/invites")
        assert "text/html" in resp.headers["content-type"]

    def test_invites_page_has_invite_heading(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/invites").text.lower()
        assert "invite" in body

    def test_invites_page_has_create_invite_form(self, admin_client: TestClient) -> None:
        """Page must include a form to send a new invite."""
        body = admin_client.get("/admin/invites").text
        assert "<form" in body.lower()
        # email input required for invite creation
        assert "email" in body.lower()

    def test_invites_form_posts_to_invites_endpoint(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/invites").text
        assert "/admin/invites" in body or "action=" in body.lower()

    def test_invites_page_lists_existing_invites(self, admin_client: TestClient) -> None:
        """Page should have a section listing existing invites (even if empty)."""
        body = admin_client.get("/admin/invites").text.lower()
        assert any(kw in body for kw in ["pending", "invite", "token", "no invite", "revoke", "list"])

    def test_invites_page_shows_invite_status_column(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/invites").text.lower()
        assert any(kw in body for kw in ["status", "pending", "accepted", "revoked", "state"])

    def test_invites_page_shows_expires_column(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/invites").text.lower()
        assert any(kw in body for kw in ["expire", "valid until", "expiry", "sent"])

    def test_invites_page_has_role_selector_in_form(self, admin_client: TestClient) -> None:
        """Create-invite form should let admin pick member vs admin role."""
        body = admin_client.get("/admin/invites").text.lower()
        assert any(kw in body for kw in ["role", "member", "admin", "select"])

    def test_invites_page_has_back_to_admin_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/invites").text
        assert "/admin" in body

    def test_invites_page_no_server_error(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/invites").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body


# ===========================================================================
# POST /admin/invites — Create invite form submission
# ===========================================================================


class TestAdminCreateInviteForm:
    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        resp = client.post("/admin/invites", data={"email": "user@example.com", "role": "member"})
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.post(
            "/admin/invites", data={"email": "user@example.com", "role": "member"}
        )
        assert resp.status_code in (302, 303, 403)

    def test_valid_invite_creates_and_redirects(self, admin_client: TestClient) -> None:
        resp = admin_client.post(
            "/admin/invites",
            data={"email": "newuser@example.com", "role": "member", "expires_in_hours": "168"},
        )
        # Should redirect back to /admin/invites after success
        assert resp.status_code in (200, 302, 303)
        if resp.status_code in (302, 303):
            assert "/admin/invites" in resp.headers["location"]

    def test_invalid_email_returns_error_page(self, admin_client: TestClient) -> None:
        resp = admin_client.post(
            "/admin/invites",
            data={"email": "not-an-email", "role": "member"},
        )
        # Returns 200 with form + error OR 400/422
        assert resp.status_code in (200, 400, 422)
        if resp.status_code == 200:
            body = resp.text.lower()
            assert any(kw in body for kw in ["invalid", "error", "email"])

    def test_missing_email_returns_error(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/admin/invites", data={"role": "member"})
        assert resp.status_code in (200, 400, 422)

    def test_invite_appears_in_list_after_creation(self, admin_client: TestClient) -> None:
        email = "listcheck@example.com"
        create_resp = admin_client.post(
            "/admin/invites",
            data={"email": email, "role": "member"},
        )
        assert create_resp.status_code in (200, 302, 303)
        # Now fetch the invites page and check the email appears
        resp = admin_client.get("/admin/invites")
        assert resp.status_code == 200
        assert email in resp.text

    def test_duplicate_invite_handled_gracefully(self, admin_client: TestClient) -> None:
        email = "duplicate@example.com"
        admin_client.post("/admin/invites", data={"email": email, "role": "member"})
        resp = admin_client.post("/admin/invites", data={"email": email, "role": "member"})
        # Should not 500
        assert resp.status_code not in (500,)


# ===========================================================================
# POST /admin/invites/{token}/revoke — Revoke invite
# ===========================================================================


class TestAdminRevokeInvite:
    def _create_invite(self, admin_client: TestClient, email: str) -> str:
        """Create an invite and return its token."""
        from ponddb.invite_store import InviteStore
        resp = admin_client.post(
            "/admin/invites",
            data={"email": email, "role": "member"},
        )
        # The invite page should show the token; also fetch via API
        token_resp = admin_client.get("/invites", headers=_admin_jwt_headers())
        assert token_resp.status_code == 200, token_resp.text
        invites = token_resp.json()
        matching = [i for i in invites if i["email"] == email]
        assert matching, f"Invite for {email} not found"
        return matching[0]["token"]

    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        resp = client.post("/admin/invites/sometoken/revoke")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.post("/admin/invites/sometoken/revoke")
        assert resp.status_code in (302, 303, 403)

    def test_revoke_valid_token_redirects(self, admin_client: TestClient) -> None:
        token = self._create_invite(admin_client, "revoke-me@example.com")
        resp = admin_client.post(f"/admin/invites/{token}/revoke")
        assert resp.status_code in (200, 302, 303)
        if resp.status_code in (302, 303):
            assert "/admin/invites" in resp.headers["location"]

    def test_revoke_shows_revoked_status(self, admin_client: TestClient) -> None:
        token = self._create_invite(admin_client, "revoke-status@example.com")
        admin_client.post(f"/admin/invites/{token}/revoke")
        page = admin_client.get("/admin/invites")
        assert page.status_code == 200
        body = page.text.lower()
        assert "revoked" in body

    def test_revoke_unknown_token_returns_error(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/admin/invites/nonexistent-token-xyz/revoke")
        assert resp.status_code in (200, 302, 303, 404)
        if resp.status_code == 200:
            body = resp.text.lower()
            assert any(kw in body for kw in ["not found", "error", "invalid"])

    def test_double_revoke_handled_gracefully(self, admin_client: TestClient) -> None:
        token = self._create_invite(admin_client, "double-revoke@example.com")
        admin_client.post(f"/admin/invites/{token}/revoke")
        resp = admin_client.post(f"/admin/invites/{token}/revoke")
        assert resp.status_code not in (500,)


# ===========================================================================
# GET /admin/namespaces — Namespace/workgroup overview page
# ===========================================================================


class TestAdminNamespacesPage:
    def test_unauthenticated_redirects_to_login(self, client: TestClient) -> None:
        resp = client.get("/admin/namespaces")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.get("/admin/namespaces")
        assert resp.status_code in (302, 303, 403)

    def test_admin_returns_200(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin/namespaces")
        assert resp.status_code == 200

    def test_admin_returns_html(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin/namespaces")
        assert "text/html" in resp.headers["content-type"]

    def test_namespaces_page_has_namespace_heading(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/namespaces").text.lower()
        assert "namespace" in body

    def test_namespaces_page_lists_empty_state(self, admin_client: TestClient) -> None:
        """When no namespaces exist, page should show empty state gracefully."""
        body = admin_client.get("/admin/namespaces").text.lower()
        # No traceback, server error, or crash
        assert "internal server error" not in body
        assert "traceback" not in body

    def test_namespaces_page_lists_created_namespaces(self, admin_client: TestClient) -> None:
        ns_id, _ = _create_ns_and_wg(admin_client)
        body = admin_client.get("/admin/namespaces").text
        assert "test-ns" in body

    def test_namespaces_page_shows_workgroup_count(self, admin_client: TestClient) -> None:
        _create_ns_and_wg(admin_client)
        body = admin_client.get("/admin/namespaces").text.lower()
        assert any(kw in body for kw in ["workgroup", "group", "1"])

    def test_namespaces_page_shows_workgroup_names(self, admin_client: TestClient) -> None:
        _create_ns_and_wg(admin_client)
        body = admin_client.get("/admin/namespaces").text
        assert "test-wg" in body

    def test_namespaces_page_has_create_namespace_form_or_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/namespaces").text.lower()
        assert any(kw in body for kw in ["create", "new namespace", "add namespace", "<form"])

    def test_namespaces_page_has_workgroup_quota_link(self, admin_client: TestClient) -> None:
        """Each workgroup row should have a link to edit its quota."""
        _create_ns_and_wg(admin_client)
        body = admin_client.get("/admin/namespaces").text
        assert "/admin/workgroups/" in body or "quota" in body.lower()

    def test_namespaces_page_has_back_to_admin_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/namespaces").text
        assert "/admin" in body

    def test_namespaces_page_no_server_error(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/namespaces").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body


# ===========================================================================
# GET /admin/usage — Usage dashboard
# ===========================================================================


class TestAdminUsageDashboard:
    def test_unauthenticated_redirects_to_login(self, client: TestClient) -> None:
        resp = client.get("/admin/usage")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.get("/admin/usage")
        assert resp.status_code in (302, 303, 403)

    def test_admin_returns_200(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin/usage")
        assert resp.status_code == 200

    def test_admin_returns_html(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin/usage")
        assert "text/html" in resp.headers["content-type"]

    def test_usage_page_has_usage_heading(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text.lower()
        assert any(kw in body for kw in ["usage", "metrics", "dashboard"])

    def test_usage_page_shows_active_sessions_metric(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text.lower()
        assert any(kw in body for kw in ["active session", "session", "sessions active"])

    def test_usage_page_shows_total_queries_metric(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text.lower()
        assert any(kw in body for kw in ["total quer", "queries", "query count", "execut"])

    def test_usage_page_shows_compute_metric(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text.lower()
        assert any(kw in body for kw in ["compute", "cpu", "wall time", "duration", "ms"])

    def test_usage_page_shows_per_workgroup_breakdown(self, admin_client: TestClient) -> None:
        """Usage page should show metrics broken down by workgroup."""
        _create_ns_and_wg(admin_client)
        body = admin_client.get("/admin/usage").text.lower()
        assert any(kw in body for kw in ["workgroup", "namespace", "by workgroup", "per group"])

    def test_usage_page_shows_utilization_percentage(self, admin_client: TestClient) -> None:
        _create_ns_and_wg(admin_client)
        body = admin_client.get("/admin/usage").text.lower()
        assert any(kw in body for kw in ["%", "utilization", "percent", "capacity"])

    def test_usage_page_has_query_history_link_or_section(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text.lower()
        assert any(kw in body for kw in ["history", "recent", "log", "query"])

    def test_usage_page_has_back_to_admin_link(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text
        assert "/admin" in body

    def test_usage_page_no_server_error(self, admin_client: TestClient) -> None:
        body = admin_client.get("/admin/usage").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body

    def test_usage_page_renders_with_no_data(self, admin_client: TestClient) -> None:
        """Page must not crash when there are zero sessions/queries."""
        resp = admin_client.get("/admin/usage")
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "internal server error" not in body


# ===========================================================================
# GET /admin/workgroups/{wg_id}/quota — Quota editing form page
# ===========================================================================


class TestAdminQuotaEditPage:
    def test_unauthenticated_redirects_to_login(self, client: TestClient) -> None:
        resp = client.get("/admin/workgroups/fake-id/quota")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.get("/admin/workgroups/fake-id/quota")
        assert resp.status_code in (302, 303, 403)

    def test_unknown_workgroup_returns_404(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/admin/workgroups/nonexistent-wg-xyz/quota")
        assert resp.status_code == 404

    def test_known_workgroup_returns_200(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.get(f"/admin/workgroups/{wg_id}/quota")
        assert resp.status_code == 200

    def test_quota_page_returns_html(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.get(f"/admin/workgroups/{wg_id}/quota")
        assert "text/html" in resp.headers["content-type"]

    def test_quota_page_shows_workgroup_name(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text.lower()
        assert "test-wg" in body or "workgroup" in body

    def test_quota_page_has_edit_form(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text
        assert "<form" in body.lower()

    def test_quota_form_has_max_sessions_field(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text.lower()
        assert "max_sessions" in body or "max session" in body

    def test_quota_form_has_max_query_duration_field(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text.lower()
        assert any(kw in body for kw in ["duration", "max_query", "timeout", "query duration"])

    def test_quota_form_has_max_result_mb_field(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text.lower()
        assert any(kw in body for kw in ["result_mb", "max_result", "result size", "mb"])

    def test_quota_form_prepopulates_current_quota(self, admin_client: TestClient) -> None:
        """Form should show current quota values so admin can see what they're editing."""
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text
        # Should include current value 10 (max_sessions set during _create_ns_and_wg)
        assert "10" in body

    def test_quota_form_posts_to_correct_endpoint(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text
        assert wg_id in body or f"/admin/workgroups/{wg_id}" in body

    def test_quota_page_has_back_to_namespaces_link(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text
        assert "/admin" in body

    def test_quota_page_no_server_error(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        body = admin_client.get(f"/admin/workgroups/{wg_id}/quota").text.lower()
        assert "internal server error" not in body
        assert "traceback" not in body


# ===========================================================================
# POST /admin/workgroups/{wg_id}/quota — Quota form submission
# ===========================================================================


class TestAdminQuotaEditSubmit:
    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/workgroups/fake-id/quota",
            data={"max_sessions": "5"},
        )
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers["location"]

    def test_member_cookie_forbidden(self, member_client: TestClient) -> None:
        resp = member_client.post(
            "/admin/workgroups/fake-id/quota",
            data={"max_sessions": "5"},
        )
        assert resp.status_code in (302, 303, 403)

    def test_unknown_workgroup_returns_404(self, admin_client: TestClient) -> None:
        resp = admin_client.post(
            "/admin/workgroups/nonexistent-wg-xyz/quota",
            data={"max_sessions": "5"},
        )
        assert resp.status_code == 404

    def test_valid_quota_update_redirects(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "20", "max_query_duration_ms": "30000", "max_result_mb": "50"},
        )
        # Should redirect back to namespaces or quota page with success
        assert resp.status_code in (200, 302, 303)
        if resp.status_code in (302, 303):
            assert "/admin" in resp.headers["location"]

    def test_quota_update_persists(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "25", "max_query_duration_ms": "45000", "max_result_mb": "75"},
        )
        # Verify via API that quota was actually updated
        api_resp = admin_client.get(f"/workgroups/{wg_id}", headers=_admin_jwt_headers())
        assert api_resp.status_code == 200
        wg = api_resp.json()
        quota = wg.get("quota", {})
        assert quota.get("max_sessions") == 25

    def test_quota_update_page_shows_success_message(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "15"},
            follow_redirects=True,
        )
        # Either the redirect target or inline success message
        assert resp.status_code in (200, 302, 303)
        if resp.status_code == 200:
            body = resp.text.lower()
            assert any(kw in body for kw in ["saved", "updated", "success", "quota"])

    def test_invalid_max_sessions_zero_returns_error(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "0"},
        )
        # max_sessions must be > 0
        assert resp.status_code in (200, 400, 422)
        if resp.status_code == 200:
            body = resp.text.lower()
            assert any(kw in body for kw in ["invalid", "error", "positive", "must be"])

    def test_invalid_max_sessions_negative_returns_error(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "-5"},
        )
        assert resp.status_code in (200, 400, 422)

    def test_non_numeric_max_sessions_returns_error(self, admin_client: TestClient) -> None:
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "abc"},
        )
        assert resp.status_code in (200, 400, 422)

    def test_partial_quota_update_allowed(self, admin_client: TestClient) -> None:
        """Submitting only max_sessions (omitting other fields) should work."""
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "8"},
        )
        assert resp.status_code in (200, 302, 303)
        assert resp.status_code != 500

    def test_clear_quota_with_empty_values(self, admin_client: TestClient) -> None:
        """Submitting empty strings for all quota fields should clear the quota."""
        _, wg_id = _create_ns_and_wg(admin_client)
        resp = admin_client.post(
            f"/admin/workgroups/{wg_id}/quota",
            data={"max_sessions": "", "max_query_duration_ms": "", "max_result_mb": ""},
        )
        # Should succeed (clear quota) or show a clear/reset confirmation
        assert resp.status_code not in (500,)


# ===========================================================================
# Admin console navigation and consistent header/footer
# ===========================================================================


class TestAdminConsoleNavigation:
    """All admin pages should share a consistent navigation structure."""

    ADMIN_PAGES = [
        "/admin",
        "/admin/invites",
        "/admin/namespaces",
        "/admin/usage",
    ]

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_page_returns_200_for_admin(self, admin_client: TestClient, path: str) -> None:
        resp = admin_client.get(path)
        assert resp.status_code == 200

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_page_redirects_unauthenticated(self, client: TestClient, path: str) -> None:
        resp = client.get(path)
        assert resp.status_code in (302, 303)

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_page_has_admin_nav_link(self, admin_client: TestClient, path: str) -> None:
        """Every admin page should have a link back to /admin (breadcrumb or nav)."""
        body = admin_client.get(path).text
        assert "/admin" in body

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_page_has_logout_link(self, admin_client: TestClient, path: str) -> None:
        body = admin_client.get(path).text.lower()
        assert "logout" in body or "sign out" in body

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_page_returns_html(self, admin_client: TestClient, path: str) -> None:
        resp = admin_client.get(path)
        assert "text/html" in resp.headers["content-type"]

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_member_blocked_from_all_admin_pages(
        self, member_client: TestClient, path: str
    ) -> None:
        resp = member_client.get(path)
        assert resp.status_code in (302, 303, 403)
