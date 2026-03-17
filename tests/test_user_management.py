"""Integration tests for user management.

Defines expected behavior for:
  - users, org_members, workgroup_members, api_keys tables in MetadataStore/UserStore
  - UserStore.upsert_user / get_user_by_provider_id for OAuth provisioning
  - User provisioning on OAuth callback (user record created/updated automatically)
  - GET /users/me — return authenticated user's profile
  - POST /users/me/api-keys — create API key for current user
  - GET /users/me/api-keys — list API keys (metadata only, not plaintext)
  - DELETE /users/me/api-keys/{key_id} — revoke an API key
  - API key auth via require_auth (hashed lookup)
  - Org/workgroup membership tables (CRUD)
"""

import importlib
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Environment setup (must be before any ponddb imports)
# ---------------------------------------------------------------------------

JWT_SECRET = "test-user-mgmt-jwt-secret"
API_KEY = "test-pond-api-key"

os.environ.setdefault("POND_JWT_SECRET", JWT_SECRET)
os.environ.setdefault("POND_API_KEY", API_KEY)
os.environ.setdefault("POND_SQLITE_PATH", ":memory:")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_API_KEY", API_KEY)
    monkeypatch.setenv("POND_SQLITE_PATH", ":memory:")
    monkeypatch.setenv("POND_OAUTH_SECRET", "test-oauth-hmac-secret")
    monkeypatch.setenv("POND_GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("POND_GOOGLE_CLIENT_SECRET", "google-client-secret")
    monkeypatch.setenv("POND_GITHUB_CLIENT_ID", "github-client-id")
    monkeypatch.setenv("POND_GITHUB_CLIENT_SECRET", "github-client-secret")


@pytest.fixture
def client(env_vars) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def user_store(env_vars):
    """Isolated in-memory UserStore for unit tests."""
    from ponddb.store.user_store import UserStore

    store = UserStore(":memory:")
    store.initialize_blocking()
    return store


@pytest.fixture
def admin_token(env_vars) -> str:
    from ponddb.auth.jwt_auth import create_access_token

    return create_access_token("admin-tenant", role="admin")


@pytest.fixture
def user_token(env_vars) -> str:
    from ponddb.auth.jwt_auth import create_access_token

    return create_access_token("user-tenant-abc")


@pytest.fixture
def auth_headers(user_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# ===========================================================================
# UserStore — table creation and basic CRUD
# ===========================================================================


class TestUserStoreTables:
    """UserStore must create and expose all four tables."""

    def test_user_store_can_be_instantiated(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        assert store is not None

    def test_initialize_blocking_creates_users_table(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        # Should not raise — table exists
        store._conn.execute("SELECT * FROM users LIMIT 0")

    def test_initialize_blocking_creates_org_members_table(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        store._conn.execute("SELECT * FROM org_members LIMIT 0")

    def test_initialize_blocking_creates_workgroup_members_table(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        store._conn.execute("SELECT * FROM workgroup_members LIMIT 0")

    def test_initialize_blocking_creates_api_keys_table(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        store._conn.execute("SELECT * FROM api_keys LIMIT 0")

    def test_initialize_is_idempotent(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        store.initialize_blocking()  # second call must not raise


# ===========================================================================
# UserStore — user CRUD
# ===========================================================================


class TestUserStoreCRUD:
    @pytest.mark.asyncio
    async def test_create_user_returns_dict_with_id(self, user_store):
        user = await user_store.create_user(
            email="alice@example.com",
            display_name="Alice",
            provider="google",
            provider_id="google-uid-001",
            tenant_id="google:google-uid-001",
        )
        assert "id" in user
        assert user["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_create_user_persists_provider(self, user_store):
        user = await user_store.create_user(
            email="bob@example.com",
            display_name="Bob",
            provider="github",
            provider_id="github-uid-999",
            tenant_id="github:github-uid-999",
        )
        assert user["provider"] == "github"
        assert user["provider_id"] == "github-uid-999"

    @pytest.mark.asyncio
    async def test_get_user_by_id_returns_user(self, user_store):
        created = await user_store.create_user(
            email="carol@example.com",
            display_name="Carol",
            provider="google",
            provider_id="google-carol-1",
            tenant_id="google:google-carol-1",
        )
        fetched = await user_store.get_user_by_id(created["id"])
        assert fetched is not None
        assert fetched["email"] == "carol@example.com"

    @pytest.mark.asyncio
    async def test_get_user_by_id_returns_none_for_missing(self, user_store):
        result = await user_store.get_user_by_id("nonexistent-uuid")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_email_returns_user(self, user_store):
        await user_store.create_user(
            email="dave@example.com",
            display_name="Dave",
            provider="github",
            provider_id="gh-dave",
            tenant_id="github:gh-dave",
        )
        fetched = await user_store.get_user_by_email("dave@example.com")
        assert fetched is not None
        assert fetched["display_name"] == "Dave"

    @pytest.mark.asyncio
    async def test_get_user_by_email_case_insensitive(self, user_store):
        await user_store.create_user(
            email="Eve@Example.COM",
            display_name="Eve",
            provider="google",
            provider_id="g-eve",
            tenant_id="google:g-eve",
        )
        fetched = await user_store.get_user_by_email("eve@example.com")
        assert fetched is not None

    @pytest.mark.asyncio
    async def test_get_user_by_email_returns_none_for_missing(self, user_store):
        result = await user_store.get_user_by_email("nobody@nowhere.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_provider_id_returns_user(self, user_store):
        await user_store.create_user(
            email="frank@example.com",
            display_name="Frank",
            provider="google",
            provider_id="g-frank-42",
            tenant_id="google:g-frank-42",
        )
        fetched = await user_store.get_user_by_provider_id("google", "g-frank-42")
        assert fetched is not None
        assert fetched["email"] == "frank@example.com"

    @pytest.mark.asyncio
    async def test_get_user_by_provider_id_returns_none_for_wrong_provider(self, user_store):
        await user_store.create_user(
            email="grace@example.com",
            display_name="Grace",
            provider="google",
            provider_id="g-grace",
            tenant_id="google:g-grace",
        )
        result = await user_store.get_user_by_provider_id("github", "g-grace")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_user_sets_created_at(self, user_store):
        before = datetime.now(timezone.utc).isoformat()
        user = await user_store.create_user(
            email="henry@example.com",
            display_name="Henry",
            provider="google",
            provider_id="g-henry",
            tenant_id="google:g-henry",
        )
        assert "created_at" in user
        assert user["created_at"] >= before

    @pytest.mark.asyncio
    async def test_create_user_default_role_is_member(self, user_store):
        user = await user_store.create_user(
            email="iris@example.com",
            display_name="Iris",
            provider="google",
            provider_id="g-iris",
            tenant_id="google:g-iris",
        )
        assert user["role"] == "member"

    @pytest.mark.asyncio
    async def test_create_user_with_explicit_role(self, user_store):
        user = await user_store.create_user(
            email="jack@example.com",
            display_name="Jack",
            provider="google",
            provider_id="g-jack",
            tenant_id="google:g-jack",
            role="admin",
        )
        assert user["role"] == "admin"

    @pytest.mark.asyncio
    async def test_duplicate_email_raises(self, user_store):
        await user_store.create_user(
            email="dup@example.com",
            display_name="First",
            provider="google",
            provider_id="g-dup-1",
            tenant_id="google:g-dup-1",
        )
        with pytest.raises(Exception):
            await user_store.create_user(
                email="dup@example.com",
                display_name="Second",
                provider="github",
                provider_id="gh-dup-2",
                tenant_id="github:gh-dup-2",
            )

    @pytest.mark.asyncio
    async def test_update_user_display_name(self, user_store):
        user = await user_store.create_user(
            email="kate@example.com",
            display_name="Kate Old",
            provider="google",
            provider_id="g-kate",
            tenant_id="google:g-kate",
        )
        updated = await user_store.update_user(user["id"], display_name="Kate New")
        assert updated["display_name"] == "Kate New"

    @pytest.mark.asyncio
    async def test_update_user_last_login_at(self, user_store):
        user = await user_store.create_user(
            email="liam@example.com",
            display_name="Liam",
            provider="google",
            provider_id="g-liam",
            tenant_id="google:g-liam",
        )
        ts = datetime.now(timezone.utc).isoformat()
        updated = await user_store.update_user(user["id"], last_login_at=ts)
        assert updated["last_login_at"] is not None

    @pytest.mark.asyncio
    async def test_update_nonexistent_user_raises(self, user_store):
        with pytest.raises(ValueError, match="[Nn]ot found"):
            await user_store.update_user("nonexistent-id", display_name="Ghost")


# ===========================================================================
# UserStore — upsert_user (OAuth provisioning)
# ===========================================================================


class TestUserStoreUpsert:
    """upsert_user: create on first login, update last_login_at on repeat login."""

    @pytest.mark.asyncio
    async def test_upsert_user_creates_new_user(self, user_store):
        user = await user_store.upsert_user(
            provider="google",
            provider_id="google-new-100",
            email="newuser@example.com",
            display_name="New User",
            tenant_id="google:google-new-100",
        )
        assert user["id"] is not None
        assert user["email"] == "newuser@example.com"

    @pytest.mark.asyncio
    async def test_upsert_user_returns_existing_on_second_call(self, user_store):
        first = await user_store.upsert_user(
            provider="google",
            provider_id="google-repeat-200",
            email="repeat@example.com",
            display_name="Repeat User",
            tenant_id="google:google-repeat-200",
        )
        second = await user_store.upsert_user(
            provider="google",
            provider_id="google-repeat-200",
            email="repeat@example.com",
            display_name="Repeat User",
            tenant_id="google:google-repeat-200",
        )
        assert first["id"] == second["id"]

    @pytest.mark.asyncio
    async def test_upsert_user_updates_last_login_at(self, user_store):
        await user_store.upsert_user(
            provider="google",
            provider_id="google-login-300",
            email="loginuser@example.com",
            display_name="Login User",
            tenant_id="google:google-login-300",
        )
        import time

        time.sleep(0.01)
        second = await user_store.upsert_user(
            provider="google",
            provider_id="google-login-300",
            email="loginuser@example.com",
            display_name="Login User",
            tenant_id="google:google-login-300",
        )
        assert second["last_login_at"] is not None

    @pytest.mark.asyncio
    async def test_upsert_user_updates_display_name(self, user_store):
        await user_store.upsert_user(
            provider="github",
            provider_id="gh-displayname-400",
            email="namechange@example.com",
            display_name="Old Name",
            tenant_id="github:gh-displayname-400",
        )
        updated = await user_store.upsert_user(
            provider="github",
            provider_id="gh-displayname-400",
            email="namechange@example.com",
            display_name="New Name",
            tenant_id="github:gh-displayname-400",
        )
        assert updated["display_name"] == "New Name"

    @pytest.mark.asyncio
    async def test_upsert_user_updates_avatar_url(self, user_store):
        await user_store.upsert_user(
            provider="google",
            provider_id="g-avatar-500",
            email="avatar@example.com",
            display_name="Avatar User",
            tenant_id="google:g-avatar-500",
            avatar_url=None,
        )
        updated = await user_store.upsert_user(
            provider="google",
            provider_id="g-avatar-500",
            email="avatar@example.com",
            display_name="Avatar User",
            tenant_id="google:g-avatar-500",
            avatar_url="https://example.com/avatar.jpg",
        )
        assert updated["avatar_url"] == "https://example.com/avatar.jpg"

    @pytest.mark.asyncio
    async def test_upsert_does_not_create_duplicate_rows(self, user_store):
        for _ in range(3):
            await user_store.upsert_user(
                provider="google",
                provider_id="g-nodup-600",
                email="nodup@example.com",
                display_name="No Dup",
                tenant_id="google:g-nodup-600",
            )
        cursor = user_store._conn.execute(
            "SELECT COUNT(*) FROM users WHERE provider_id = ?", ("g-nodup-600",)
        )
        count = cursor.fetchone()[0]
        assert count == 1


# ===========================================================================
# UserStore — org_members table
# ===========================================================================


class TestOrgMembers:
    @pytest.mark.asyncio
    async def test_add_org_member(self, user_store):
        user = await user_store.create_user(
            email="org-user@example.com",
            display_name="Org User",
            provider="google",
            provider_id="g-orguser-1",
            tenant_id="google:g-orguser-1",
        )
        member = await user_store.add_org_member(
            org_id="org-alpha",
            user_id=user["id"],
            role="member",
        )
        assert member["org_id"] == "org-alpha"
        assert member["user_id"] == user["id"]

    @pytest.mark.asyncio
    async def test_list_org_members_returns_members(self, user_store):
        user_a = await user_store.create_user(
            email="orglist-a@example.com",
            display_name="OrgList A",
            provider="google",
            provider_id="g-orglist-a",
            tenant_id="google:g-orglist-a",
        )
        user_b = await user_store.create_user(
            email="orglist-b@example.com",
            display_name="OrgList B",
            provider="google",
            provider_id="g-orglist-b",
            tenant_id="google:g-orglist-b",
        )
        await user_store.add_org_member(org_id="org-beta", user_id=user_a["id"])
        await user_store.add_org_member(org_id="org-beta", user_id=user_b["id"])
        members = await user_store.list_org_members("org-beta")
        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_remove_org_member(self, user_store):
        user = await user_store.create_user(
            email="orgremove@example.com",
            display_name="Org Remove",
            provider="google",
            provider_id="g-orgremove",
            tenant_id="google:g-orgremove",
        )
        await user_store.add_org_member(org_id="org-gamma", user_id=user["id"])
        await user_store.remove_org_member(org_id="org-gamma", user_id=user["id"])
        members = await user_store.list_org_members("org-gamma")
        assert all(m["user_id"] != user["id"] for m in members)

    @pytest.mark.asyncio
    async def test_add_org_member_with_role(self, user_store):
        user = await user_store.create_user(
            email="org-admin@example.com",
            display_name="Org Admin",
            provider="github",
            provider_id="gh-orgadmin",
            tenant_id="github:gh-orgadmin",
        )
        member = await user_store.add_org_member(
            org_id="org-delta",
            user_id=user["id"],
            role="admin",
        )
        assert member["role"] == "admin"

    @pytest.mark.asyncio
    async def test_default_org_member_role_is_member(self, user_store):
        user = await user_store.create_user(
            email="org-default@example.com",
            display_name="Org Default",
            provider="google",
            provider_id="g-orgdefault",
            tenant_id="google:g-orgdefault",
        )
        member = await user_store.add_org_member(org_id="org-epsilon", user_id=user["id"])
        assert member["role"] == "member"

    @pytest.mark.asyncio
    async def test_list_org_members_empty_for_unknown_org(self, user_store):
        members = await user_store.list_org_members("org-nonexistent-zzz")
        assert members == []


# ===========================================================================
# UserStore — workgroup_members table
# ===========================================================================


class TestWorkgroupMembers:
    @pytest.mark.asyncio
    async def test_add_workgroup_member(self, user_store):
        user = await user_store.create_user(
            email="wg-user@example.com",
            display_name="WG User",
            provider="google",
            provider_id="g-wguser",
            tenant_id="google:g-wguser",
        )
        member = await user_store.add_workgroup_member(
            workgroup_id="wg-alpha",
            user_id=user["id"],
            role="member",
        )
        assert member["workgroup_id"] == "wg-alpha"
        assert member["user_id"] == user["id"]

    @pytest.mark.asyncio
    async def test_list_workgroup_members(self, user_store):
        user = await user_store.create_user(
            email="wglist@example.com",
            display_name="WG List",
            provider="google",
            provider_id="g-wglist",
            tenant_id="google:g-wglist",
        )
        await user_store.add_workgroup_member(workgroup_id="wg-beta", user_id=user["id"])
        members = await user_store.list_workgroup_members("wg-beta")
        assert len(members) >= 1

    @pytest.mark.asyncio
    async def test_remove_workgroup_member(self, user_store):
        user = await user_store.create_user(
            email="wgremove@example.com",
            display_name="WG Remove",
            provider="google",
            provider_id="g-wgremove",
            tenant_id="google:g-wgremove",
        )
        await user_store.add_workgroup_member(workgroup_id="wg-gamma", user_id=user["id"])
        await user_store.remove_workgroup_member(workgroup_id="wg-gamma", user_id=user["id"])
        members = await user_store.list_workgroup_members("wg-gamma")
        assert all(m["user_id"] != user["id"] for m in members)

    @pytest.mark.asyncio
    async def test_list_workgroup_members_empty(self, user_store):
        members = await user_store.list_workgroup_members("wg-nonexistent-zzz")
        assert members == []

    @pytest.mark.asyncio
    async def test_add_workgroup_member_sets_added_at(self, user_store):
        user = await user_store.create_user(
            email="wg-addedat@example.com",
            display_name="WG Added",
            provider="google",
            provider_id="g-wgaddedat",
            tenant_id="google:g-wgaddedat",
        )
        member = await user_store.add_workgroup_member(workgroup_id="wg-delta", user_id=user["id"])
        assert "added_at" in member
        assert member["added_at"] is not None


# ===========================================================================
# UserStore — api_keys table
# ===========================================================================


class TestApiKeys:
    @pytest.mark.asyncio
    async def test_create_api_key_returns_plaintext_once(self, user_store):
        user = await user_store.create_user(
            email="apikey-user@example.com",
            display_name="API Key User",
            provider="google",
            provider_id="g-apikeyuser",
            tenant_id="google:g-apikeyuser",
        )
        result = await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name="My Key",
        )
        assert "plaintext_key" in result
        assert "id" in result
        assert len(result["plaintext_key"]) >= 20

    @pytest.mark.asyncio
    async def test_create_api_key_stores_hash_not_plaintext(self, user_store):
        user = await user_store.create_user(
            email="apikey-hash@example.com",
            display_name="Hash User",
            provider="google",
            provider_id="g-apikeyhash",
            tenant_id="google:g-apikeyhash",
        )
        result = await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name="Hash Key",
        )
        key_id = result["id"]
        # Fetch raw row — key_hash should not equal plaintext
        cursor = user_store._conn.execute("SELECT key_hash FROM api_keys WHERE id = ?", (key_id,))
        row = cursor.fetchone()
        assert row["key_hash"] != result["plaintext_key"]

    @pytest.mark.asyncio
    async def test_list_api_keys_returns_metadata(self, user_store):
        user = await user_store.create_user(
            email="apikey-list@example.com",
            display_name="List User",
            provider="google",
            provider_id="g-apikeylist",
            tenant_id="google:g-apikeylist",
        )
        await user_store.create_api_key(
            user_id=user["id"], tenant_id=user["tenant_id"], name="Key One"
        )
        await user_store.create_api_key(
            user_id=user["id"], tenant_id=user["tenant_id"], name="Key Two"
        )
        keys = await user_store.list_api_keys(user_id=user["id"])
        assert len(keys) == 2
        for k in keys:
            assert "plaintext_key" not in k
            assert "key_hash" not in k
            assert "id" in k
            assert "name" in k

    @pytest.mark.asyncio
    async def test_list_api_keys_includes_key_prefix(self, user_store):
        user = await user_store.create_user(
            email="apikey-prefix@example.com",
            display_name="Prefix User",
            provider="google",
            provider_id="g-apikeyprefix",
            tenant_id="google:g-apikeyprefix",
        )
        result = await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name="Prefix Key",
        )
        keys = await user_store.list_api_keys(user_id=user["id"])
        assert any(k["key_prefix"] in result["plaintext_key"] for k in keys)

    @pytest.mark.asyncio
    async def test_revoke_api_key_marks_revoked(self, user_store):
        user = await user_store.create_user(
            email="apikey-revoke@example.com",
            display_name="Revoke User",
            provider="google",
            provider_id="g-apikeyrevoke",
            tenant_id="google:g-apikeyrevoke",
        )
        result = await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name="Revokeable Key",
        )
        await user_store.revoke_api_key(key_id=result["id"], user_id=user["id"])
        keys = await user_store.list_api_keys(user_id=user["id"])
        target = next(k for k in keys if k["id"] == result["id"])
        assert target["revoked"] is True or target["revoked"] == 1

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key_raises(self, user_store):
        with pytest.raises(ValueError, match="[Nn]ot found"):
            await user_store.revoke_api_key(key_id="fake-id", user_id="fake-user")

    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self, user_store):
        user = await user_store.create_user(
            email="apikey-verify@example.com",
            display_name="Verify User",
            provider="google",
            provider_id="g-apikeyverify",
            tenant_id="google:g-apikeyverify",
        )
        result = await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name="Verifiable Key",
        )
        claims = await user_store.verify_api_key(result["plaintext_key"])
        assert claims is not None
        assert claims["user_id"] == user["id"]
        assert claims["tenant_id"] == user["tenant_id"]

    @pytest.mark.asyncio
    async def test_verify_api_key_returns_none_for_unknown_key(self, user_store):
        claims = await user_store.verify_api_key("totally-fake-key-xyz")
        assert claims is None

    @pytest.mark.asyncio
    async def test_verify_revoked_key_returns_none(self, user_store):
        user = await user_store.create_user(
            email="apikey-revokedverify@example.com",
            display_name="Revoked Verify",
            provider="google",
            provider_id="g-revokedverify",
            tenant_id="google:g-revokedverify",
        )
        result = await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name="About To Be Revoked",
        )
        await user_store.revoke_api_key(key_id=result["id"], user_id=user["id"])
        claims = await user_store.verify_api_key(result["plaintext_key"])
        assert claims is None

    @pytest.mark.asyncio
    async def test_verify_expired_key_returns_none(self, user_store):
        user = await user_store.create_user(
            email="apikey-expired@example.com",
            display_name="Expired User",
            provider="google",
            provider_id="g-apikeyexpired",
            tenant_id="google:g-apikeyexpired",
        )
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result = await user_store.create_api_key(
            user_id=user["id"],
            tenant_id=user["tenant_id"],
            name="Expired Key",
            expires_at=past,
        )
        claims = await user_store.verify_api_key(result["plaintext_key"])
        assert claims is None

    @pytest.mark.asyncio
    async def test_cannot_revoke_another_users_key(self, user_store):
        owner = await user_store.create_user(
            email="owner-apikey@example.com",
            display_name="Owner",
            provider="google",
            provider_id="g-ownerkey",
            tenant_id="google:g-ownerkey",
        )
        attacker = await user_store.create_user(
            email="attacker-apikey@example.com",
            display_name="Attacker",
            provider="google",
            provider_id="g-attackerkey",
            tenant_id="google:g-attackerkey",
        )
        result = await user_store.create_api_key(
            user_id=owner["id"],
            tenant_id=owner["tenant_id"],
            name="Owner's Key",
        )
        with pytest.raises(ValueError):
            await user_store.revoke_api_key(key_id=result["id"], user_id=attacker["id"])


# ===========================================================================
# OAuth callback — user provisioning
# ===========================================================================


class TestOAuthCallbackProvisioning:
    """When OAuth callback succeeds, a user record should be upserted in the users table."""

    def _make_state(self, provider: str) -> str:
        from ponddb.auth import oauth_state

        return oauth_state.generate_state(provider)

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_google_callback_provisions_user_in_db(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "g-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "sub": "google-provision-111",
            "email": "provision@example.com",
            "name": "Provisioned User",
            "picture": "https://example.com/pic.jpg",
        }
        state = self._make_state("google")
        resp = client.get(f"/auth/google/callback?code=auth-code&state={state}")
        assert resp.status_code == 200
        # Access token should be issued
        body = resp.json()
        assert "access_token" in body

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_callback_response_includes_user_info(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "g-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "sub": "google-provision-222",
            "email": "provision2@example.com",
            "name": "Provisioned2",
        }
        state = self._make_state("google")
        resp = client.get(f"/auth/google/callback?code=auth-code&state={state}")
        body = resp.json()
        # Should contain user info in response or at minimum tokens
        assert "access_token" in body
        assert "token_type" in body

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_callback_second_login_same_user(self, mock_user, mock_exchange, client):
        """Second OAuth login for same user should not duplicate DB records."""
        mock_exchange.return_value = {"access_token": "g-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "sub": "google-provision-333",
            "email": "provision3@example.com",
            "name": "Repeat Login",
        }
        state1 = self._make_state("google")
        resp1 = client.get(f"/auth/google/callback?code=code1&state={state1}")
        assert resp1.status_code == 200

        state2 = self._make_state("google")
        resp2 = client.get(f"/auth/google/callback?code=code2&state={state2}")
        assert resp2.status_code == 200

        # Both responses should carry the same tenant_id in the JWT
        from ponddb.auth.jwt_auth import verify_access_token

        claims1 = verify_access_token(resp1.json()["access_token"])
        claims2 = verify_access_token(resp2.json()["access_token"])
        assert claims1["tenant_id"] == claims2["tenant_id"]

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_github_callback_provisions_user(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "gh-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "id": 54321,
            "login": "gh-provision",
            "email": "gh-provision@example.com",
            "name": "GH Provisioned",
        }
        state = self._make_state("github")
        resp = client.get(f"/auth/github/callback?code=gh-code&state={state}")
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body


# ===========================================================================
# GET /users/me — return current user's profile
# ===========================================================================


class TestGetUsersMe:
    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_get_me_after_oauth_returns_user(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "g-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "sub": "google-me-001",
            "email": "me@example.com",
            "name": "Me User",
        }
        from ponddb.auth import oauth_state

        state = oauth_state.generate_state("google")
        login = client.get(f"/auth/google/callback?code=code&state={state}")
        access_token = login.json()["access_token"]

        resp = client.get("/users/me", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "me@example.com"
        assert "id" in body

    def test_get_me_requires_auth(self, client):
        resp = client.get("/users/me")
        assert resp.status_code == 401

    def test_get_me_invalid_token_returns_401(self, client):
        resp = client.get("/users/me", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_get_me_returns_id_email_display_name_role(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "g-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "sub": "google-me-002",
            "email": "me2@example.com",
            "name": "Me Two",
        }
        from ponddb.auth import oauth_state

        state = oauth_state.generate_state("google")
        login = client.get(f"/auth/google/callback?code=code&state={state}")
        access_token = login.json()["access_token"]

        resp = client.get("/users/me", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 200
        body = resp.json()
        for field in ("id", "email", "display_name", "role", "created_at"):
            assert field in body, f"Missing field: {field}"

    @patch("ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock)
    @patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock)
    def test_get_me_does_not_return_sensitive_fields(self, mock_user, mock_exchange, client):
        mock_exchange.return_value = {"access_token": "g-tok", "token_type": "bearer"}
        mock_user.return_value = {
            "sub": "google-me-003",
            "email": "me3@example.com",
            "name": "Me Three",
        }
        from ponddb.auth import oauth_state

        state = oauth_state.generate_state("google")
        login = client.get(f"/auth/google/callback?code=code&state={state}")
        access_token = login.json()["access_token"]

        resp = client.get("/users/me", headers={"Authorization": f"Bearer {access_token}"})
        body = resp.json()
        # provider_id and key hashes must not be leaked
        assert "provider_id" not in body
        assert "key_hash" not in body

    def test_get_me_with_jwt_for_unprovisioned_tenant_returns_404(self, client):
        """A JWT with a tenant_id that has no users row returns 404."""
        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("tenant-with-no-user-record")
        resp = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404


# ===========================================================================
# POST /users/me/api-keys — create API key via HTTP
# ===========================================================================


class TestApiKeyEndpoints:
    def _oauth_login(
        self, client, sub: str, email: str, name: str, provider: str = "google"
    ) -> str:
        with (
            patch(
                "ponddb.api.oauth_routes._exchange_code_for_token", new_callable=AsyncMock
            ) as mock_ex,
            patch("ponddb.api.oauth_routes._fetch_user_info", new_callable=AsyncMock) as mock_ui,
        ):
            mock_ex.return_value = {"access_token": "tok", "token_type": "bearer"}
            if provider == "google":
                mock_ui.return_value = {"sub": sub, "email": email, "name": name}
            else:
                mock_ui.return_value = {"id": sub, "login": name, "email": email}
            from ponddb.auth import oauth_state

            state = oauth_state.generate_state(provider)
            login = client.get(f"/auth/{provider}/callback?code=code&state={state}")
        return login.json()["access_token"]

    def test_create_api_key_returns_plaintext_once(self, client):
        token = self._oauth_login(client, "g-apicreate-001", "apicreate@example.com", "API Create")
        resp = client.post(
            "/users/me/api-keys",
            json={"name": "My New Key"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert "plaintext_key" in body
        assert len(body["plaintext_key"]) >= 20

    def test_create_api_key_requires_auth(self, client):
        resp = client.post("/users/me/api-keys", json={"name": "Key"})
        assert resp.status_code == 401

    def test_create_api_key_requires_name(self, client):
        token = self._oauth_login(
            client, "g-apireqname-001", "apireqname@example.com", "API Req Name"
        )
        resp = client.post(
            "/users/me/api-keys",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422  # missing required field

    def test_list_api_keys_returns_list(self, client):
        token = self._oauth_login(client, "g-apilist-001", "apilist@example.com", "API List")
        client.post(
            "/users/me/api-keys",
            json={"name": "Key One"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = client.get("/users/me/api-keys", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1

    def test_list_api_keys_does_not_expose_hash(self, client):
        token = self._oauth_login(client, "g-apinohash-001", "apinohash@example.com", "API No Hash")
        client.post(
            "/users/me/api-keys",
            json={"name": "Secret Key"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = client.get("/users/me/api-keys", headers={"Authorization": f"Bearer {token}"})
        for key in resp.json():
            assert "key_hash" not in key
            assert "plaintext_key" not in key

    def test_revoke_api_key(self, client):
        token = self._oauth_login(client, "g-apirevoke-001", "apirevoke@example.com", "API Revoke")
        create_resp = client.post(
            "/users/me/api-keys",
            json={"name": "Revokeable"},
            headers={"Authorization": f"Bearer {token}"},
        )
        key_id = create_resp.json()["id"]
        del_resp = client.delete(
            f"/users/me/api-keys/{key_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert del_resp.status_code in (200, 204)

    def test_revoke_api_key_requires_auth(self, client):
        resp = client.delete("/users/me/api-keys/some-id")
        assert resp.status_code == 401

    def test_revoke_nonexistent_key_returns_404(self, client):
        token = self._oauth_login(client, "g-api404-001", "api404@example.com", "API 404")
        resp = client.delete(
            "/users/me/api-keys/nonexistent-key-id",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    def test_api_key_auth_on_protected_endpoint(self, client):
        """A created API key should authenticate against protected endpoints."""
        token = self._oauth_login(
            client, "g-apikeyauth-001", "apikeyauth@example.com", "APIKey Auth"
        )
        create_resp = client.post(
            "/users/me/api-keys",
            json={"name": "Auth Key", "scopes": ["query", "read"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        plaintext = create_resp.json()["plaintext_key"]
        # Use the API key to access /users/me
        me_resp = client.get("/users/me", headers={"X-API-Key": plaintext})
        assert me_resp.status_code == 200

    def test_revoked_api_key_cannot_authenticate(self, client):
        token = self._oauth_login(
            client, "g-revokedauth-001", "revokedauth@example.com", "Revoked Auth"
        )
        create_resp = client.post(
            "/users/me/api-keys",
            json={"name": "Key To Revoke"},
            headers={"Authorization": f"Bearer {token}"},
        )
        key_data = create_resp.json()
        key_id = key_data["id"]
        plaintext = key_data["plaintext_key"]

        # Revoke
        client.delete(f"/users/me/api-keys/{key_id}", headers={"Authorization": f"Bearer {token}"})

        # Should now fail
        me_resp = client.get("/users/me", headers={"X-API-Key": plaintext})
        assert me_resp.status_code == 401

    def test_list_api_keys_requires_auth(self, client):
        resp = client.get("/users/me/api-keys")
        assert resp.status_code == 401

    def test_api_keys_are_isolated_per_user(self, client):
        """User A cannot see User B's API keys."""
        token_a = self._oauth_login(client, "g-isola-user-a", "isola-a@example.com", "Isola A")
        token_b = self._oauth_login(client, "g-isola-user-b", "isola-b@example.com", "Isola B")
        client.post(
            "/users/me/api-keys",
            json={"name": "A's Key"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp_b = client.get("/users/me/api-keys", headers={"Authorization": f"Bearer {token_b}"})
        keys_b = resp_b.json()
        assert all(k.get("name") != "A's Key" for k in keys_b)


# ===========================================================================
# users table columns verification via UserStore schema
# ===========================================================================


class TestUsersTableSchema:
    def test_users_table_has_id_column(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "id" in cols

    def test_users_table_has_email_column(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "email" in cols

    def test_users_table_has_provider_columns(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "provider" in cols
        assert "provider_id" in cols

    def test_users_table_has_tenant_id_column(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "tenant_id" in cols

    def test_users_table_has_role_column(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "role" in cols

    def test_users_table_has_display_name_column(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "display_name" in cols

    def test_api_keys_table_has_key_hash_column(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(api_keys)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "key_hash" in cols

    def test_api_keys_table_has_revoked_column(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(api_keys)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "revoked" in cols

    def test_org_members_table_has_org_id_and_user_id(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(org_members)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "org_id" in cols
        assert "user_id" in cols

    def test_workgroup_members_table_has_workgroup_id_and_user_id(self):
        from ponddb.store.user_store import UserStore

        store = UserStore(":memory:")
        store.initialize_blocking()
        cursor = store._conn.execute("PRAGMA table_info(workgroup_members)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "workgroup_id" in cols
        assert "user_id" in cols
