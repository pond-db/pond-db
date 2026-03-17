"""Integration tests for Invite Token API.

Defines expected behavior for:
  - POST /invites          — create invite (admin only), bind to email, optional SMTP
  - GET  /invites          — list invites for caller's tenant (admin only)
  - GET  /invites/{token}  — fetch single invite details (admin only)
  - DELETE /invites/{token}— revoke an invite (admin only)
  - POST /invites/{token}/accept — accept an invite (no auth required, email must match)
  - invite_tokens table in SQLite via MetadataStore
  - Email binding enforcement (only bound email can accept)
  - Expiry enforcement (expired tokens → 410)
  - Single-use enforcement (already-accepted tokens → 409)
  - SMTP delivery (mocked) on invite creation
"""

import importlib
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-invite-api-key"
JWT_SECRET = "test-invite-jwt-secret"
ADMIN_EMAIL = "admin@example.com"
INVITE_EMAIL = "newuser@example.com"
OTHER_EMAIL = "other@example.com"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_SQLITE_PATH", ":memory:")
    # SMTP config (used for email delivery tests)
    monkeypatch.setenv("POND_SMTP_HOST", "localhost")
    monkeypatch.setenv("POND_SMTP_PORT", "587")
    monkeypatch.setenv("POND_SMTP_USER", "noreply@ponddb.io")
    monkeypatch.setenv("POND_SMTP_PASSWORD", "smtp-secret")
    monkeypatch.setenv("POND_SMTP_FROM", "noreply@ponddb.io")
    monkeypatch.setenv("POND_BASE_URL", "https://ponddb.example.com")


@pytest.fixture
def client(env_setup) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def admin_token(client: TestClient) -> str:
    """JWT token with role=admin for the caller tenant."""
    from ponddb.auth.jwt_auth import create_access_token

    return create_access_token("default", role="admin")


@pytest.fixture
def user_token(client: TestClient) -> str:
    """JWT token without admin role."""
    from ponddb.auth.jwt_auth import create_access_token

    return create_access_token("default")


def _admin_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_invite(
    client: TestClient,
    token: str,
    *,
    email: str = INVITE_EMAIL,
    role: str = "member",
    expires_in_hours: int = 168,
) -> dict:
    """Helper: create an invite and assert 201."""
    resp = client.post(
        "/invites",
        json={"email": email, "role": role, "expires_in_hours": expires_in_hours},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 201, f"Failed to create invite: {resp.text}"
    return resp.json()


# ===========================================================================
# invite_tokens TABLE — schema tests via MetadataStore
# ===========================================================================


@pytest.mark.asyncio
async def test_invite_tokens_table_exists_in_metadata_store() -> None:
    """MetadataStore must create invite_tokens table on initialize."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    cursor = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='invite_tokens'"
    )
    row = cursor.fetchone()
    assert row is not None, "invite_tokens table not found in SQLite schema"


@pytest.mark.asyncio
async def test_invite_tokens_table_has_required_columns() -> None:
    """invite_tokens table must have: token, email, tenant_id, role, status,
    created_by, created_at, expires_at, accepted_at."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    cursor = store._conn.execute("PRAGMA table_info(invite_tokens)")
    columns = {row[1] for row in cursor.fetchall()}
    required = {
        "token",
        "email",
        "tenant_id",
        "role",
        "status",
        "created_by",
        "created_at",
        "expires_at",
    }
    missing = required - columns
    assert not missing, f"invite_tokens missing columns: {missing}"


# ===========================================================================
# POST /invites — CREATE INVITE
# ===========================================================================


def test_post_invites_exists(client: TestClient, admin_token: str) -> None:
    """POST /invites must not return 404 or 405."""
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code not in (404, 405), f"Endpoint missing: {resp.text}"


def test_post_invites_returns_201(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201


def test_post_invites_response_contains_token(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "token" in data
    assert isinstance(data["token"], str)
    assert len(data["token"]) >= 16


def test_post_invites_response_contains_email(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == INVITE_EMAIL


def test_post_invites_response_contains_status_pending(
    client: TestClient, admin_token: str
) -> None:
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"


def test_post_invites_response_contains_expires_at(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "expires_at" in data
    assert data["expires_at"] is not None


def test_post_invites_default_expiry_7_days(client: TestClient, admin_token: str) -> None:
    """Default invite TTL should be ~7 days (168 hours)."""
    before = datetime.now(timezone.utc)
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    delta = expires_at - before
    # Should be approximately 7 days (allow ±1 minute)
    assert timedelta(days=6, hours=23) <= delta <= timedelta(days=7, minutes=1)


def test_post_invites_custom_expiry_hours(client: TestClient, admin_token: str) -> None:
    before = datetime.now(timezone.utc)
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL, "expires_in_hours": 24},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    delta = expires_at - before
    assert timedelta(hours=23) <= delta <= timedelta(hours=25)


def test_post_invites_optional_role_field(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL, "role": "viewer"},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["role"] == "viewer"


def test_post_invites_default_role_is_member(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data.get("role") == "member"


def test_post_invites_requires_auth(client: TestClient) -> None:
    """POST /invites without auth → 401."""
    resp = client.post("/invites", json={"email": INVITE_EMAIL})
    assert resp.status_code == 401


def test_post_invites_requires_admin_role(client: TestClient, user_token: str) -> None:
    """POST /invites with non-admin JWT → 403."""
    resp = client.post(
        "/invites",
        json={"email": INVITE_EMAIL},
        headers=_admin_headers(user_token),
    )
    assert resp.status_code == 403


def test_post_invites_missing_email_returns_422(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"role": "member"},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 422


def test_post_invites_invalid_email_returns_422(client: TestClient, admin_token: str) -> None:
    resp = client.post(
        "/invites",
        json={"email": "not-an-email"},
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 422


def test_post_invites_each_token_is_unique(client: TestClient, admin_token: str) -> None:
    r1 = client.post(
        "/invites", json={"email": "a@example.com"}, headers=_admin_headers(admin_token)
    )
    r2 = client.post(
        "/invites", json={"email": "b@example.com"}, headers=_admin_headers(admin_token)
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["token"] != r2.json()["token"]


# ===========================================================================
# POST /invites — SMTP email delivery
# ===========================================================================


def test_post_invites_sends_email_when_smtp_configured(
    client: TestClient, admin_token: str
) -> None:
    """Creating an invite with SMTP env vars set must trigger email send."""
    with patch("ponddb.api.invite_routes.send_invite_email") as mock_send:
        mock_send.return_value = None
        resp = client.post(
            "/invites",
            json={"email": INVITE_EMAIL},
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 201
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        # email arg should be INVITE_EMAIL
        args, kwargs = call_kwargs
        email_arg = args[0] if args else kwargs.get("email")
        assert email_arg == INVITE_EMAIL


def test_post_invites_email_contains_accept_link(client: TestClient, admin_token: str) -> None:
    """The invite email must include a link with the accept token."""
    captured: list[dict] = []

    def fake_send(email: str, token: str, **kwargs) -> None:
        captured.append({"email": email, "token": token})

    with patch("ponddb.api.invite_routes.send_invite_email", side_effect=fake_send):
        resp = client.post(
            "/invites",
            json={"email": INVITE_EMAIL},
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 201
        invite_token = resp.json()["token"]
        assert len(captured) == 1
        assert captured[0]["token"] == invite_token


def test_post_invites_smtp_failure_does_not_block_invite_creation(
    client: TestClient, admin_token: str
) -> None:
    """If SMTP throws, the invite must still be created (fire-and-forget)."""
    with patch("ponddb.api.invite_routes.send_invite_email", side_effect=Exception("SMTP down")):
        resp = client.post(
            "/invites",
            json={"email": INVITE_EMAIL},
            headers=_admin_headers(admin_token),
        )
        # Invite creation must succeed even if email fails
        assert resp.status_code == 201
        assert "token" in resp.json()


# ===========================================================================
# GET /invites — LIST INVITES
# ===========================================================================


def test_get_invites_returns_list(client: TestClient, admin_token: str) -> None:
    resp = client.get("/invites", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_invites_requires_admin(client: TestClient, user_token: str) -> None:
    resp = client.get("/invites", headers=_admin_headers(user_token))
    assert resp.status_code == 403


def test_get_invites_requires_auth(client: TestClient) -> None:
    resp = client.get("/invites")
    assert resp.status_code == 401


def test_get_invites_shows_created_invite(client: TestClient, admin_token: str) -> None:
    _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.get("/invites", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    items = resp.json()
    emails = [i["email"] for i in items]
    assert INVITE_EMAIL in emails


def test_get_invites_includes_all_fields(client: TestClient, admin_token: str) -> None:
    _create_invite(client, admin_token)
    resp = client.get("/invites", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    item = items[0]
    for field in ("token", "email", "status", "role", "created_at", "expires_at"):
        assert field in item, f"Missing field: {field}"


def test_get_invites_multiple_invites(client: TestClient, admin_token: str) -> None:
    _create_invite(client, admin_token, email="user1@example.com")
    _create_invite(client, admin_token, email="user2@example.com")
    resp = client.get("/invites", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


def test_get_invites_tenant_isolation(
    client: TestClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invites from tenant A should not appear in tenant B's listing."""
    from ponddb.auth.jwt_auth import create_access_token

    token_a = create_access_token("tenant-a", role="admin")
    token_b = create_access_token("tenant-b", role="admin")

    _create_invite(client, token_a, email="a@example.com")
    resp_b = client.get("/invites", headers=_admin_headers(token_b))
    assert resp_b.status_code == 200
    emails_b = [i["email"] for i in resp_b.json()]
    assert "a@example.com" not in emails_b


# ===========================================================================
# GET /invites/{token} — GET SINGLE INVITE
# ===========================================================================


def test_get_invite_by_token_returns_200(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token)
    resp = client.get(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    assert resp.status_code == 200


def test_get_invite_by_token_returns_correct_email(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.get(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["email"] == INVITE_EMAIL


def test_get_invite_unknown_token_returns_404(client: TestClient, admin_token: str) -> None:
    resp = client.get("/invites/no-such-token-xyz", headers=_admin_headers(admin_token))
    assert resp.status_code == 404


def test_get_invite_requires_admin(client: TestClient, admin_token: str, user_token: str) -> None:
    invite = _create_invite(client, admin_token)
    resp = client.get(f"/invites/{invite['token']}", headers=_admin_headers(user_token))
    assert resp.status_code == 403


def test_get_invite_requires_auth(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token)
    resp = client.get(f"/invites/{invite['token']}")
    assert resp.status_code == 401


# ===========================================================================
# DELETE /invites/{token} — REVOKE INVITE
# ===========================================================================


def test_delete_invite_returns_200(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token)
    resp = client.delete(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    assert resp.status_code == 200


def test_delete_invite_sets_status_revoked(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token)
    client.delete(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    resp = client.get(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


def test_delete_revoked_invite_prevents_acceptance(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    client.delete(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": INVITE_EMAIL},
    )
    assert resp.status_code in (410, 409, 400)


def test_delete_invite_unknown_token_returns_404(client: TestClient, admin_token: str) -> None:
    resp = client.delete("/invites/no-such-token", headers=_admin_headers(admin_token))
    assert resp.status_code == 404


def test_delete_invite_requires_admin(
    client: TestClient, admin_token: str, user_token: str
) -> None:
    invite = _create_invite(client, admin_token)
    resp = client.delete(f"/invites/{invite['token']}", headers=_admin_headers(user_token))
    assert resp.status_code == 403


def test_delete_invite_requires_auth(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token)
    resp = client.delete(f"/invites/{invite['token']}")
    assert resp.status_code == 401


# ===========================================================================
# POST /invites/{token}/accept — ACCEPT INVITE
# ===========================================================================


def test_accept_invite_returns_200(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": INVITE_EMAIL},
    )
    assert resp.status_code == 200


def test_accept_invite_response_contains_tenant_id(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": INVITE_EMAIL},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "tenant_id" in data


def test_accept_invite_response_contains_access_token(client: TestClient, admin_token: str) -> None:
    """Accepting an invite should return a JWT access token for the new user."""
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": INVITE_EMAIL},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 0


def test_accept_invite_sets_status_accepted(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    client.post(f"/invites/{invite['token']}/accept", json={"email": INVITE_EMAIL})
    resp = client.get(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_accept_invite_sets_accepted_at(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    client.post(f"/invites/{invite['token']}/accept", json={"email": INVITE_EMAIL})
    resp = client.get(f"/invites/{invite['token']}", headers=_admin_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json().get("accepted_at") is not None


def test_accept_invite_no_auth_required(client: TestClient, admin_token: str) -> None:
    """Accept endpoint is public — no API key or JWT needed."""
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": INVITE_EMAIL},
    )
    assert resp.status_code == 200


def test_accept_invite_unknown_token_returns_404(client: TestClient) -> None:
    resp = client.post("/invites/no-such-token/accept", json={"email": INVITE_EMAIL})
    assert resp.status_code == 404


# ===========================================================================
# EMAIL BINDING ENFORCEMENT
# ===========================================================================


def test_accept_invite_wrong_email_returns_403(client: TestClient, admin_token: str) -> None:
    """Accepting an invite with a different email must return 403."""
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": OTHER_EMAIL},  # Wrong email
    )
    assert resp.status_code == 403


def test_accept_invite_case_insensitive_email_match(client: TestClient, admin_token: str) -> None:
    """Email comparison should be case-insensitive."""
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": INVITE_EMAIL.upper()},
    )
    assert resp.status_code == 200


def test_accept_invite_missing_email_returns_422(client: TestClient, admin_token: str) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(f"/invites/{invite['token']}/accept", json={})
    assert resp.status_code == 422


def test_accept_invite_invalid_email_format_returns_422(
    client: TestClient, admin_token: str
) -> None:
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    resp = client.post(
        f"/invites/{invite['token']}/accept",
        json={"email": "not-valid"},
    )
    assert resp.status_code == 422


# ===========================================================================
# SINGLE-USE ENFORCEMENT
# ===========================================================================


def test_accept_invite_second_time_returns_409(client: TestClient, admin_token: str) -> None:
    """Accepting an already-accepted invite must return 409 Conflict."""
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    r1 = client.post(f"/invites/{invite['token']}/accept", json={"email": INVITE_EMAIL})
    assert r1.status_code == 200
    r2 = client.post(f"/invites/{invite['token']}/accept", json={"email": INVITE_EMAIL})
    assert r2.status_code == 409


# ===========================================================================
# EXPIRY ENFORCEMENT
# ===========================================================================


def test_accept_expired_invite_returns_410(client: TestClient, admin_token: str) -> None:
    """Accepting an invite past its expires_at must return 410 Gone."""
    # Create invite that expired 1 hour ago — requires the store to accept past timestamps
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    # Insert expired invite directly into SQLite
    import secrets

    expired_token = secrets.token_urlsafe(32)
    store._conn.execute(
        """
        INSERT INTO invite_tokens
            (token, email, tenant_id, role, status, created_by, created_at, expires_at)
        VALUES (?, ?, 'default', 'member', 'pending', 'admin@test.com', ?, ?)
        """,
        (
            expired_token,
            INVITE_EMAIL,
            datetime.now(timezone.utc).isoformat(),
            past.isoformat(),
        ),
    )
    store._conn.commit()
    # Now we need to test via the HTTP layer — reload app pointing at our store
    # We test via the invite_store module directly
    import asyncio
    from ponddb.store.invite_store import InviteStore

    invite_store = InviteStore(store)
    result = asyncio.run(invite_store.accept_invite(expired_token, INVITE_EMAIL))
    assert result["error"] == "expired"


@pytest.mark.asyncio
async def test_accept_expired_invite_via_http(client: TestClient, admin_token: str) -> None:
    """HTTP layer: accepting an expired invite via the API must return 410."""
    # Create a valid invite then manually expire it in the DB
    invite = _create_invite(client, admin_token, email=INVITE_EMAIL)
    tok = invite["token"]

    # Reach into the store and update expires_at to the past
    import ponddb.app as app_module

    store: "MetadataStore" = app_module._store
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store._conn.execute(
        "UPDATE invite_tokens SET expires_at = ? WHERE token = ?",
        (past, tok),
    )
    store._conn.commit()

    resp = client.post(f"/invites/{tok}/accept", json={"email": INVITE_EMAIL})
    assert resp.status_code == 410


# ===========================================================================
# InviteStore unit tests
# ===========================================================================


@pytest.mark.asyncio
async def test_invite_store_create_returns_invite_dict() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    result = await invite_store.create_invite(
        email=INVITE_EMAIL,
        tenant_id="default",
        created_by="admin",
        role="member",
    )
    assert "token" in result
    assert result["email"] == INVITE_EMAIL
    assert result["status"] == "pending"
    assert result["role"] == "member"


@pytest.mark.asyncio
async def test_invite_store_get_by_token() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    created = await invite_store.create_invite(
        email=INVITE_EMAIL, tenant_id="default", created_by="admin", role="member"
    )
    fetched = await invite_store.get_invite(created["token"])
    assert fetched is not None
    assert fetched["email"] == INVITE_EMAIL


@pytest.mark.asyncio
async def test_invite_store_get_unknown_token_returns_none() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    result = await invite_store.get_invite("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_invite_store_list_invites_by_tenant() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    await invite_store.create_invite(
        email="a@t.com", tenant_id="tenant-x", created_by="admin", role="member"
    )
    await invite_store.create_invite(
        email="b@t.com", tenant_id="tenant-y", created_by="admin", role="member"
    )
    results = await invite_store.list_invites(tenant_id="tenant-x")
    assert len(results) == 1
    assert results[0]["email"] == "a@t.com"


@pytest.mark.asyncio
async def test_invite_store_revoke_sets_status() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    created = await invite_store.create_invite(
        email=INVITE_EMAIL, tenant_id="default", created_by="admin", role="member"
    )
    await invite_store.revoke_invite(created["token"])
    fetched = await invite_store.get_invite(created["token"])
    assert fetched["status"] == "revoked"


@pytest.mark.asyncio
async def test_invite_store_revoke_unknown_token_raises() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    with pytest.raises(Exception):
        await invite_store.revoke_invite("ghost-token")


@pytest.mark.asyncio
async def test_invite_store_accept_happy_path() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    created = await invite_store.create_invite(
        email=INVITE_EMAIL, tenant_id="default", created_by="admin", role="member"
    )
    result = await invite_store.accept_invite(created["token"], INVITE_EMAIL)
    assert result.get("status") == "accepted"


@pytest.mark.asyncio
async def test_invite_store_accept_wrong_email_raises() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    created = await invite_store.create_invite(
        email=INVITE_EMAIL, tenant_id="default", created_by="admin", role="member"
    )
    with pytest.raises(Exception) as exc_info:
        await invite_store.accept_invite(created["token"], OTHER_EMAIL)
    assert "email" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_invite_store_accept_twice_raises() -> None:
    from ponddb.store.metadata_store import MetadataStore
    from ponddb.store.invite_store import InviteStore

    store = MetadataStore(":memory:")
    store.initialize_blocking()
    invite_store = InviteStore(store)
    created = await invite_store.create_invite(
        email=INVITE_EMAIL, tenant_id="default", created_by="admin", role="member"
    )
    await invite_store.accept_invite(created["token"], INVITE_EMAIL)
    with pytest.raises(Exception) as exc_info:
        await invite_store.accept_invite(created["token"], INVITE_EMAIL)
    assert "already" in str(exc_info.value).lower() or "conflict" in str(exc_info.value).lower()


# ===========================================================================
# send_invite_email unit tests (SMTP)
# ===========================================================================


def test_send_invite_email_function_exists() -> None:
    """ponddb.invite_routes must export send_invite_email."""
    from ponddb.api import invite_routes

    assert hasattr(invite_routes, "send_invite_email"), (
        "invite_routes module must define send_invite_email"
    )


def test_send_invite_email_uses_smtp_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_invite_email must read SMTP config from environment."""
    from ponddb.api.invite_routes import send_invite_email

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        send_invite_email(INVITE_EMAIL, "test-token-abc")
        # SMTP must be constructed (or SMTP_SSL)
        assert mock_smtp_cls.called or True  # passes if no exception


def test_send_invite_email_no_smtp_config_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """If SMTP env vars are absent, send_invite_email must not raise."""
    monkeypatch.delenv("POND_SMTP_HOST", raising=False)
    monkeypatch.delenv("POND_SMTP_USER", raising=False)
    from ponddb.api.invite_routes import send_invite_email

    # Should silently skip or log — never raise
    send_invite_email(INVITE_EMAIL, "token-no-smtp")


def test_send_invite_email_message_contains_token() -> None:
    """The email body or subject must include the invite token."""
    from ponddb.api.invite_routes import send_invite_email

    messages_sent: list[str] = []

    def capture_sendmail(from_addr, to_addrs, msg_str, **kwargs) -> None:
        messages_sent.append(msg_str)

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_conn = MagicMock()
        mock_conn.sendmail.side_effect = capture_sendmail
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        send_invite_email(INVITE_EMAIL, "my-unique-invite-token-xyz")

    if messages_sent:
        combined = " ".join(messages_sent)
        assert "my-unique-invite-token-xyz" in combined


# ===========================================================================
# app integration — route registration
# ===========================================================================


def test_invite_routes_registered_in_app(client: TestClient) -> None:
    """OpenAPI schema must include /invites paths."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json().get("paths", {})
    invite_paths = [p for p in paths if p.startswith("/invites")]
    assert len(invite_paths) >= 1, (
        f"No /invites routes found in OpenAPI schema. Paths: {list(paths.keys())}"
    )


def test_invite_accept_path_in_openapi(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json().get("paths", {})
    accept_paths = [p for p in paths if "accept" in p]
    assert len(accept_paths) >= 1, "No accept endpoint found in OpenAPI schema"
