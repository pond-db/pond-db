"""Tests for workgroup_id extension on SessionManager and HTTP cross-workgroup 403.

Expected new behaviour (not yet implemented):
- SessionManager.create_session() accepts an optional workgroup_id parameter
- _Session stores workgroup_id; defaults to "default" for backwards compat
- get_session() dict includes "workgroup_id"
- list_sessions() accepts optional workgroup_id filter
- SessionManager exposes a WorkgroupAccessError (or similar) for cross-workgroup violations
- POST /query enforces workgroup isolation: JWT with workgroup_id != session.workgroup_id → 403
- Backwards compat: sessions without explicit workgroup_id use "default"; tokens without
  workgroup_id claim skip the cross-workgroup check

All tests here are expected to FAIL until the implementation is written.
"""

import importlib
import os

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-workgroup-api-key"
JWT_SECRET = "workgroup-test-jwt-secret"

TENANT_A = "tenant-wg-alpha"
TENANT_B = "tenant-wg-beta"

WORKGROUP_A = "wg-alpha"
WORKGROUP_B = "wg-beta"
DEFAULT_WORKGROUP = "default"


# ===========================================================================
# SECTION 1: SessionManager.create_session with workgroup_id
# ===========================================================================


class TestCreateSessionWithWorkgroup:
    """create_session() must accept an optional workgroup_id kwarg."""

    @pytest.fixture
    def manager(self):
        from ponddb.engine.session_manager import SessionManager

        mgr = SessionManager()
        yield mgr
        for s in mgr.list_sessions():
            try:
                mgr.destroy_session(s["session_id"])
            except Exception:
                pass

    def test_create_session_accepts_workgroup_id_kwarg(self, manager) -> None:
        """create_session(workgroup_id=...) must not raise TypeError."""
        sid = manager.create_session(workgroup_id=WORKGROUP_A)
        assert isinstance(sid, str) and len(sid) > 0

    def test_create_session_workgroup_id_stored(self, manager) -> None:
        """get_session() must return the workgroup_id that was passed."""
        sid = manager.create_session(workgroup_id=WORKGROUP_A)
        info = manager.get_session(sid)
        assert info["workgroup_id"] == WORKGROUP_A

    def test_create_session_different_workgroup_ids(self, manager) -> None:
        """Two sessions can belong to different workgroups."""
        sid_a = manager.create_session(workgroup_id=WORKGROUP_A)
        sid_b = manager.create_session(workgroup_id=WORKGROUP_B)
        assert manager.get_session(sid_a)["workgroup_id"] == WORKGROUP_A
        assert manager.get_session(sid_b)["workgroup_id"] == WORKGROUP_B

    def test_create_session_workgroup_id_in_list(self, manager) -> None:
        """list_sessions() entries must include workgroup_id."""
        manager.create_session(workgroup_id=WORKGROUP_A)
        sessions = manager.list_sessions()
        assert len(sessions) == 1
        assert "workgroup_id" in sessions[0]
        assert sessions[0]["workgroup_id"] == WORKGROUP_A


# ===========================================================================
# SECTION 2: Backwards compatibility — default workgroup
# ===========================================================================


class TestDefaultWorkgroupBackwardsCompat:
    """Sessions created without workgroup_id must transparently use 'default'."""

    @pytest.fixture
    def manager(self):
        from ponddb.engine.session_manager import SessionManager

        mgr = SessionManager()
        yield mgr
        for s in mgr.list_sessions():
            try:
                mgr.destroy_session(s["session_id"])
            except Exception:
                pass

    def test_session_without_workgroup_id_has_default(self, manager) -> None:
        """Session created with no workgroup_id must return workgroup_id='default'."""
        sid = manager.create_session()
        info = manager.get_session(sid)
        assert "workgroup_id" in info
        assert info["workgroup_id"] == DEFAULT_WORKGROUP

    def test_session_with_namespace_still_gets_default_workgroup(self, manager) -> None:
        """Existing callers that pass only namespace must still get default workgroup_id."""
        sid = manager.create_session(namespace="my-ns")
        info = manager.get_session(sid)
        assert info["workgroup_id"] == DEFAULT_WORKGROUP

    def test_existing_api_shape_preserved(self, manager) -> None:
        """All pre-existing keys in get_session() must still be present."""
        sid = manager.create_session()
        info = manager.get_session(sid)
        for key in ("session_id", "status", "created_at", "namespace", "workgroup_id"):
            assert key in info, f"Expected key missing from get_session(): {key!r}"

    def test_list_sessions_all_have_workgroup_id(self, manager) -> None:
        """All sessions in list_sessions() — old-style or new — must have workgroup_id."""
        manager.create_session()
        manager.create_session(namespace="ns2")
        manager.create_session(workgroup_id=WORKGROUP_A)
        for s in manager.list_sessions():
            assert "workgroup_id" in s, f"Missing workgroup_id in {s}"


# ===========================================================================
# SECTION 3: list_sessions workgroup_id filter
# ===========================================================================


class TestListSessionsWorkgroupFilter:
    """list_sessions(workgroup_id=...) must filter by workgroup."""

    @pytest.fixture
    def manager(self):
        from ponddb.engine.session_manager import SessionManager

        mgr = SessionManager()
        yield mgr
        for s in mgr.list_sessions():
            try:
                mgr.destroy_session(s["session_id"])
            except Exception:
                pass

    def test_filter_by_workgroup_id_returns_only_matching(self, manager) -> None:
        manager.create_session(workgroup_id=WORKGROUP_A)
        manager.create_session(workgroup_id=WORKGROUP_A)
        manager.create_session(workgroup_id=WORKGROUP_B)
        result = manager.list_sessions(workgroup_id=WORKGROUP_A)
        assert len(result) == 2
        assert all(s["workgroup_id"] == WORKGROUP_A for s in result)

    def test_filter_by_workgroup_id_excludes_others(self, manager) -> None:
        manager.create_session(workgroup_id=WORKGROUP_A)
        manager.create_session(workgroup_id=WORKGROUP_B)
        result = manager.list_sessions(workgroup_id=WORKGROUP_B)
        assert len(result) == 1
        assert result[0]["workgroup_id"] == WORKGROUP_B

    def test_filter_by_nonexistent_workgroup_returns_empty(self, manager) -> None:
        manager.create_session(workgroup_id=WORKGROUP_A)
        result = manager.list_sessions(workgroup_id="nonexistent-wg")
        assert result == []

    def test_filter_none_returns_all(self, manager) -> None:
        manager.create_session(workgroup_id=WORKGROUP_A)
        manager.create_session(workgroup_id=WORKGROUP_B)
        manager.create_session()  # default workgroup
        result = manager.list_sessions()
        assert len(result) == 3

    def test_filter_default_workgroup(self, manager) -> None:
        """Filtering by 'default' must return sessions that used the default."""
        manager.create_session()
        manager.create_session(workgroup_id=WORKGROUP_A)
        result = manager.list_sessions(workgroup_id=DEFAULT_WORKGROUP)
        assert len(result) == 1
        assert result[0]["workgroup_id"] == DEFAULT_WORKGROUP


# ===========================================================================
# SECTION 4: WorkgroupAccessError — importable exception
# ===========================================================================


class TestWorkgroupAccessError:
    """WorkgroupAccessError must be importable from ponddb.engine.session_manager."""

    def test_workgroup_access_error_importable(self) -> None:
        from ponddb.engine.session_manager import WorkgroupAccessError  # noqa: F401

    def test_workgroup_access_error_is_exception(self) -> None:
        from ponddb.engine.session_manager import WorkgroupAccessError

        assert issubclass(WorkgroupAccessError, Exception)

    def test_workgroup_access_error_can_be_raised(self) -> None:
        from ponddb.engine.session_manager import WorkgroupAccessError

        with pytest.raises(WorkgroupAccessError):
            raise WorkgroupAccessError("access denied")


# ===========================================================================
# SECTION 5: check_workgroup_access — session manager method
# ===========================================================================


class TestCheckWorkgroupAccess:
    """SessionManager.check_workgroup_access(session_id, caller_workgroup_id) must
    raise WorkgroupAccessError when the caller's workgroup doesn't match the session's."""

    @pytest.fixture
    def manager(self):
        from ponddb.engine.session_manager import SessionManager

        mgr = SessionManager()
        yield mgr
        for s in mgr.list_sessions():
            try:
                mgr.destroy_session(s["session_id"])
            except Exception:
                pass

    def test_check_workgroup_access_method_exists(self, manager) -> None:
        assert hasattr(manager, "check_workgroup_access"), (
            "SessionManager must have check_workgroup_access method"
        )

    def test_same_workgroup_does_not_raise(self, manager) -> None:
        sid = manager.create_session(workgroup_id=WORKGROUP_A)
        # Must not raise
        manager.check_workgroup_access(sid, WORKGROUP_A)

    def test_different_workgroup_raises_workgroup_access_error(self, manager) -> None:
        from ponddb.engine.session_manager import WorkgroupAccessError

        sid = manager.create_session(workgroup_id=WORKGROUP_A)
        with pytest.raises(WorkgroupAccessError):
            manager.check_workgroup_access(sid, WORKGROUP_B)

    def test_unknown_session_raises_key_error(self, manager) -> None:
        with pytest.raises(KeyError):
            manager.check_workgroup_access("ghost-session", WORKGROUP_A)

    def test_default_session_accessible_without_workgroup_check(self, manager) -> None:
        """A session in the 'default' workgroup is accessible without workgroup enforcement.

        Callers that pass None as caller_workgroup_id skip the check entirely.
        """
        sid = manager.create_session()  # default workgroup
        # Passing None means "no workgroup claim" — must not raise
        manager.check_workgroup_access(sid, None)

    def test_default_session_accessible_from_default_caller(self, manager) -> None:
        """Explicit 'default' caller matches the session's default workgroup."""
        sid = manager.create_session()
        manager.check_workgroup_access(sid, DEFAULT_WORKGROUP)

    def test_default_session_from_non_default_caller_raises(self, manager) -> None:
        """Session in 'default' workgroup should raise if caller asserts a different workgroup."""
        from ponddb.engine.session_manager import WorkgroupAccessError

        sid = manager.create_session()  # workgroup_id = "default"
        with pytest.raises(WorkgroupAccessError):
            manager.check_workgroup_access(sid, WORKGROUP_A)

    def test_workgroup_b_session_from_workgroup_a_caller_raises(self, manager) -> None:
        from ponddb.engine.session_manager import WorkgroupAccessError

        sid = manager.create_session(workgroup_id=WORKGROUP_B)
        with pytest.raises(WorkgroupAccessError):
            manager.check_workgroup_access(sid, WORKGROUP_A)

    def test_error_message_identifies_workgroups(self, manager) -> None:
        """WorkgroupAccessError message must name the mismatched workgroups."""
        from ponddb.engine.session_manager import WorkgroupAccessError

        sid = manager.create_session(workgroup_id=WORKGROUP_A)
        with pytest.raises(WorkgroupAccessError) as exc_info:
            manager.check_workgroup_access(sid, WORKGROUP_B)
        msg = str(exc_info.value)
        assert WORKGROUP_A in msg or WORKGROUP_B in msg, (
            f"Error message should name the workgroups: {msg!r}"
        )


# ===========================================================================
# SECTION 6: HTTP API — POST /session returns workgroup_id
# ===========================================================================


@pytest.fixture(autouse=True)
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)


@pytest.fixture
def client(env_setup) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


def _admin_token() -> str:
    from ponddb.auth.jwt_auth import create_access_token

    return create_access_token(TENANT_A, role="admin")


def _workgroup_token(tenant_id: str, workgroup_id: str) -> str:
    """Create a JWT that carries workgroup_id in the payload."""
    import time

    from jose import jwt as jose_jwt

    secret = os.environ.get("POND_JWT_SECRET", JWT_SECRET)
    now = int(time.time())
    payload = {
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "workgroup_id": workgroup_id,
        "scopes": ["query", "read", "write"],
        "type": "access",
        "iat": now,
        "exp": now + 3600,
    }
    return jose_jwt.encode(payload, secret, algorithm="HS256")


def _no_workgroup_token(tenant_id: str) -> str:
    """Create a JWT that has NO workgroup_id claim."""
    from ponddb.auth.jwt_auth import create_access_token

    return create_access_token(tenant_id)


def _create_ns_wg(client: TestClient) -> tuple[str, str]:
    """Helper: create a namespace and workgroup; return (ns_id, wg_id)."""
    admin_h = {"Authorization": f"Bearer {_admin_token()}"}
    ns = client.post("/namespaces", json={"name": f"ns-{id(client)}"}, headers=admin_h).json()
    wg = client.post(
        "/workgroups",
        json={"name": f"wg-{id(client)}", "namespace_id": ns["id"]},
        headers=admin_h,
    ).json()
    return ns["id"], wg["id"]


class TestSessionCreationReturnsWorkgroupId:
    """POST /session with workgroup_id must echo workgroup_id in the response."""

    def test_session_with_workgroup_id_in_response(self, client: TestClient) -> None:
        _, wg_id = _create_ns_wg(client)
        resp = client.post("/session", json={"workgroup_id": wg_id})
        assert resp.status_code == 201
        data = resp.json()
        assert "workgroup_id" in data, f"Missing workgroup_id in response: {data}"
        assert data["workgroup_id"] == wg_id

    def test_session_without_workgroup_id_has_default_in_response(self, client: TestClient) -> None:
        """Backwards-compat: no workgroup_id → response still contains workgroup_id='default'
        (or omits it, but must not break)."""
        resp = client.post("/session")
        assert resp.status_code == 201
        # Either absent or explicitly "default" — must not be a different workgroup
        data = resp.json()
        if "workgroup_id" in data:
            assert data["workgroup_id"] == DEFAULT_WORKGROUP or data["workgroup_id"] is None


# ===========================================================================
# SECTION 7: HTTP API — cross-workgroup 403 on POST /query
# ===========================================================================


class TestCrossWorkgroupQueryForbidden:
    """POST /query must return 403 when the JWT's workgroup_id doesn't match
    the session's workgroup_id."""

    def test_same_workgroup_query_succeeds(self, client: TestClient) -> None:
        """Token with workgroup_id=A can query session belonging to workgroup A."""
        _, wg_id = _create_ns_wg(client)
        # Create session in wg_id
        sess_resp = client.post("/session", json={"workgroup_id": wg_id})
        assert sess_resp.status_code == 201
        sid = sess_resp.json()["session_id"]

        token = _workgroup_token(TENANT_A, wg_id)
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 1 AS n"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_different_workgroup_query_returns_403(self, client: TestClient) -> None:
        """Token with workgroup_id=B cannot query session belonging to workgroup A → 403."""
        admin_h = {"Authorization": f"Bearer {_admin_token()}"}
        ns = client.post("/namespaces", json={"name": "x-wg-ns-403"}, headers=admin_h).json()
        wg_a = client.post(
            "/workgroups",
            json={"name": "x-wg-a-403", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()
        wg_b = client.post(
            "/workgroups",
            json={"name": "x-wg-b-403", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()

        # Session belongs to workgroup A
        sess_resp = client.post("/session", json={"workgroup_id": wg_a["id"]})
        assert sess_resp.status_code == 201
        sid = sess_resp.json()["session_id"]

        # Token claims workgroup B
        token_b = _workgroup_token(TENANT_B, wg_b["id"])
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 1 AS n"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for cross-workgroup access, got {resp.status_code}: {resp.text}"
        )

    def test_cross_workgroup_403_response_has_detail(self, client: TestClient) -> None:
        """403 response must have a detail message."""
        admin_h = {"Authorization": f"Bearer {_admin_token()}"}
        ns = client.post("/namespaces", json={"name": "x-wg-ns-msg"}, headers=admin_h).json()
        wg_a = client.post(
            "/workgroups",
            json={"name": "x-wg-a-msg", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()
        wg_b = client.post(
            "/workgroups",
            json={"name": "x-wg-b-msg", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()

        sess_resp = client.post("/session", json={"workgroup_id": wg_a["id"]})
        sid = sess_resp.json()["session_id"]

        token_b = _workgroup_token(TENANT_B, wg_b["id"])
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 42"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert "detail" in body, f"403 response missing 'detail': {body}"

    def test_token_without_workgroup_claim_can_query_default_session(
        self, client: TestClient
    ) -> None:
        """Backwards compat: token with no workgroup_id claim can query default-workgroup session."""
        # Session created without workgroup_id (default)
        sess_resp = client.post("/session")
        assert sess_resp.status_code == 201
        sid = sess_resp.json()["session_id"]

        token = _no_workgroup_token(TENANT_A)
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (
            f"Backwards-compat token should query default session: {resp.status_code} {resp.text}"
        )

    def test_token_without_workgroup_claim_can_query_any_session(self, client: TestClient) -> None:
        """Tokens without workgroup_id claim skip the cross-workgroup check entirely."""
        _, wg_id = _create_ns_wg(client)
        sess_resp = client.post("/session", json={"workgroup_id": wg_id})
        assert sess_resp.status_code == 201
        sid = sess_resp.json()["session_id"]

        # Token with no workgroup_id — should not be blocked
        token = _no_workgroup_token(TENANT_A)
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (
            f"Token without workgroup_id claim should not be blocked: {resp.status_code} {resp.text}"
        )

    def test_token_with_matching_workgroup_can_query_workgroup_session(
        self, client: TestClient
    ) -> None:
        """Token with workgroup_id claim matching session workgroup gets 200."""
        _, wg_id = _create_ns_wg(client)
        sess_resp = client.post("/session", json={"workgroup_id": wg_id})
        assert sess_resp.status_code == 201
        sid = sess_resp.json()["session_id"]

        token = _workgroup_token(TENANT_A, wg_id)
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 99 AS v"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_api_key_auth_skips_workgroup_check(self, client: TestClient) -> None:
        """Requests authenticated via X-API-Key (not JWT) must bypass workgroup enforcement."""
        _, wg_id = _create_ns_wg(client)
        sess_resp = client.post("/session", json={"workgroup_id": wg_id})
        assert sess_resp.status_code == 201
        sid = sess_resp.json()["session_id"]

        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 1"},
            headers={"X-API-Key": VALID_API_KEY},
        )
        assert resp.status_code == 200, (
            f"API-key auth should bypass workgroup check: {resp.status_code} {resp.text}"
        )


# ===========================================================================
# SECTION 8: GET /sessions workgroup_id filter
# ===========================================================================


class TestListSessionsApiWorkgroupFilter:
    """GET /sessions?workgroup_id=... must filter by workgroup."""

    def test_list_sessions_accepts_workgroup_id_query_param(self, client: TestClient) -> None:
        """GET /sessions?workgroup_id=X must not return 422 (unknown param)."""
        _, wg_id = _create_ns_wg(client)
        resp = client.get(f"/sessions?workgroup_id={wg_id}")
        assert resp.status_code not in (422,), (
            f"workgroup_id query param should be accepted: {resp.status_code} {resp.text}"
        )

    def test_list_sessions_filters_by_workgroup(self, client: TestClient) -> None:
        admin_h = {"Authorization": f"Bearer {_admin_token()}"}
        ns = client.post("/namespaces", json={"name": "ls-wg-ns"}, headers=admin_h).json()
        wg_a = client.post(
            "/workgroups",
            json={"name": "ls-wg-a", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()
        wg_b = client.post(
            "/workgroups",
            json={"name": "ls-wg-b", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()

        client.post("/session", json={"workgroup_id": wg_a["id"]})
        client.post("/session", json={"workgroup_id": wg_a["id"]})
        client.post("/session", json={"workgroup_id": wg_b["id"]})

        result = client.get(f"/sessions?workgroup_id={wg_a['id']}").json()
        assert isinstance(result, list)
        assert all(s.get("workgroup_id") == wg_a["id"] for s in result), (
            f"Expected only wg_a sessions: {result}"
        )

    def test_list_sessions_without_filter_includes_all_workgroups(self, client: TestClient) -> None:
        admin_h = {"Authorization": f"Bearer {_admin_token()}"}
        ns = client.post("/namespaces", json={"name": "ls-all-ns"}, headers=admin_h).json()
        wg_a = client.post(
            "/workgroups",
            json={"name": "ls-all-a", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()
        wg_b = client.post(
            "/workgroups",
            json={"name": "ls-all-b", "namespace_id": ns["id"]},
            headers=admin_h,
        ).json()

        client.post("/session", json={"workgroup_id": wg_a["id"]})
        client.post("/session", json={"workgroup_id": wg_b["id"]})
        client.post("/session")  # default

        result = client.get("/sessions").json()
        assert len(result) >= 3, f"Expected at least 3 sessions: {result}"


# ===========================================================================
# SECTION 9: GET /sessions response includes workgroup_id
# ===========================================================================


class TestSessionListResponseShape:
    """GET /sessions response entries must include workgroup_id field."""

    def test_session_list_entry_has_workgroup_id(self, client: TestClient) -> None:
        client.post("/session")
        result = client.get("/sessions").json()
        assert len(result) > 0
        for entry in result:
            assert "workgroup_id" in entry, f"Session entry missing workgroup_id: {entry}"

    def test_session_list_entry_with_explicit_workgroup(self, client: TestClient) -> None:
        _, wg_id = _create_ns_wg(client)
        client.post("/session", json={"workgroup_id": wg_id})
        result = client.get("/sessions").json()
        wg_entries = [s for s in result if s.get("workgroup_id") == wg_id]
        assert len(wg_entries) >= 1, f"No session with workgroup_id={wg_id!r}: {result}"
