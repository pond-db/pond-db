"""Tests for workgroup quota fields, check_and_reserve_session_slot,
GET /workgroups/{id}/usage, and reconciliation task.

Tests FAIL until implementation is complete.
"""

import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-quota-api-key"
JWT_SECRET = "quota-test-jwt-secret"

ADMIN_TENANT = "quota-admin"
REGULAR_TENANT = "quota-user"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _admin_headers() -> dict[str, str]:
    from ponddb.auth.jwt_auth import create_access_token
    token = create_access_token(ADMIN_TENANT, role="admin")
    return {"Authorization": f"Bearer {token}"}


def _user_headers() -> dict[str, str]:
    from ponddb.auth.jwt_auth import create_access_token
    token = create_access_token(REGULAR_TENANT)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _create_namespace(client: TestClient, name: str = "quota-ns", **kwargs) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, **kwargs}
    resp = client.post("/namespaces", json=payload, headers=_admin_headers())
    assert resp.status_code == 201, f"namespace create failed: {resp.status_code} {resp.text}"
    return resp.json()


def _create_workgroup(
    client: TestClient,
    namespace_id: str,
    name: str = "quota-wg",
    **kwargs,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "namespace_id": namespace_id, **kwargs}
    resp = client.post("/workgroups", json=payload, headers=_admin_headers())
    assert resp.status_code == 201, f"workgroup create failed: {resp.status_code} {resp.text}"
    return resp.json()


def _create_session(client: TestClient, workgroup_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if workgroup_id is not None:
        body["workgroup_id"] = workgroup_id
    resp = client.post("/session", json=body)
    return resp


# ===========================================================================
# SECTION 1: Workgroup quota fields
# ===========================================================================


class TestWorkgroupQuotaFields:
    """Workgroups must support a quota sub-object with capacity limits."""

    def test_create_with_quota_returns_201(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-quota-create-ns")
        resp = client.post(
            "/workgroups",
            json={
                "name": "wq-quota-create",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 5},
            },
            headers=_admin_headers(),
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_create_with_quota_response_contains_quota(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-quota-field-ns")
        resp = client.post(
            "/workgroups",
            json={
                "name": "wq-quota-field",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 3},
            },
            headers=_admin_headers(),
        )
        data = resp.json()
        assert "quota" in data, f"Response missing 'quota' field: {data}"

    def test_quota_max_sessions_stored(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-max-sess-ns")
        resp = client.post(
            "/workgroups",
            json={
                "name": "wq-max-sess",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 7},
            },
            headers=_admin_headers(),
        )
        data = resp.json()
        assert data["quota"]["max_sessions"] == 7, f"max_sessions not stored: {data}"

    def test_quota_max_query_duration_ms_stored(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-dur-ns")
        resp = client.post(
            "/workgroups",
            json={
                "name": "wq-duration",
                "namespace_id": ns["id"],
                "quota": {"max_query_duration_ms": 30000},
            },
            headers=_admin_headers(),
        )
        data = resp.json()
        assert data["quota"]["max_query_duration_ms"] == 30000, f"max_query_duration_ms not stored: {data}"

    def test_quota_max_result_mb_stored(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-result-mb-ns")
        resp = client.post(
            "/workgroups",
            json={
                "name": "wq-result-mb",
                "namespace_id": ns["id"],
                "quota": {"max_result_mb": 50},
            },
            headers=_admin_headers(),
        )
        data = resp.json()
        assert data["quota"]["max_result_mb"] == 50, f"max_result_mb not stored: {data}"

    def test_quota_all_fields_together(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-all-quota-ns")
        quota = {"max_sessions": 10, "max_query_duration_ms": 60000, "max_result_mb": 100}
        resp = client.post(
            "/workgroups",
            json={"name": "wq-all-quota", "namespace_id": ns["id"], "quota": quota},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert data["quota"]["max_sessions"] == 10
        assert data["quota"]["max_query_duration_ms"] == 60000
        assert data["quota"]["max_result_mb"] == 100

    def test_quota_defaults_when_not_specified(self, client: TestClient) -> None:
        """Workgroup created without quota should still return a quota field."""
        ns = _create_namespace(client, name="wq-no-quota-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wq-no-quota", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        data = resp.json()
        # Either quota is absent (None) or contains sensible defaults — must be present as a key
        assert "quota" in data, f"Response missing 'quota' field: {data}"

    def test_quota_update_via_put(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-upd-quota-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-upd-quota")
        resp = client.put(
            f"/workgroups/{wg['id']}",
            json={"quota": {"max_sessions": 15}},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quota"]["max_sessions"] == 15, f"quota not updated: {data}"

    def test_quota_max_sessions_must_be_positive(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-neg-quota-ns")
        resp = client.post(
            "/workgroups",
            json={
                "name": "wq-neg-quota",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": -1},
            },
            headers=_admin_headers(),
        )
        assert resp.status_code in (400, 422), f"Expected validation error for negative quota, got {resp.status_code}"

    def test_quota_max_sessions_zero_rejected(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-zero-quota-ns")
        resp = client.post(
            "/workgroups",
            json={
                "name": "wq-zero-quota",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 0},
            },
            headers=_admin_headers(),
        )
        assert resp.status_code in (400, 422), f"Expected validation error for zero max_sessions, got {resp.status_code}"

    def test_get_workgroup_includes_quota(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-get-quota-ns")
        wg = _create_workgroup(
            client,
            namespace_id=ns["id"],
            name="wq-get-quota",
            quota={"max_sessions": 4},
        )
        resp = client.get(f"/workgroups/{wg['id']}", headers=_admin_headers())
        data = resp.json()
        assert "quota" in data
        assert data["quota"]["max_sessions"] == 4


# ===========================================================================
# SECTION 2: GET /workgroups/{id}/usage endpoint
# ===========================================================================


class TestWorkgroupUsageEndpoint:
    """GET /workgroups/{id}/usage — real-time quota utilization."""

    def test_usage_endpoint_exists(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-probe-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-usage-probe")
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        assert resp.status_code not in (404, 405), f"Endpoint missing: {resp.status_code} {resp.text}"

    def test_usage_returns_200_for_existing_workgroup(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-200-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-usage-200",
            quota={"max_sessions": 5},
        )
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_usage_returns_404_for_unknown_workgroup(self, client: TestClient) -> None:
        resp = client.get(
            "/workgroups/00000000-0000-0000-0000-000000000000/usage",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    def test_usage_contains_workgroup_id(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-wgid-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-usage-wgid")
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        data = resp.json()
        assert "workgroup_id" in data, f"Missing workgroup_id: {data}"
        assert data["workgroup_id"] == wg["id"]

    def test_usage_contains_quota_block(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-quota-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-usage-quota",
            quota={"max_sessions": 5},
        )
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        data = resp.json()
        assert "quota" in data, f"Missing quota in usage response: {data}"
        assert data["quota"]["max_sessions"] == 5

    def test_usage_contains_active_sessions_count(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-active-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-usage-active",
            quota={"max_sessions": 5},
        )
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        data = resp.json()
        assert "usage" in data, f"Missing usage block: {data}"
        assert "active_sessions" in data["usage"], f"Missing active_sessions: {data}"

    def test_usage_initial_active_sessions_is_zero(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-zero-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-usage-zero",
            quota={"max_sessions": 5},
        )
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        data = resp.json()
        assert data["usage"]["active_sessions"] == 0, f"Expected 0 active sessions: {data}"

    def test_usage_contains_available_slots(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-avail-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-usage-avail",
            quota={"max_sessions": 5},
        )
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        data = resp.json()
        assert "available_slots" in data, f"Missing available_slots: {data}"
        assert data["available_slots"] == 5

    def test_usage_contains_utilization_pct(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-util-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-usage-util",
            quota={"max_sessions": 4},
        )
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers())
        data = resp.json()
        assert "utilization_pct" in data, f"Missing utilization_pct: {data}"
        assert data["utilization_pct"] == 0.0

    def test_usage_requires_admin_auth(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-auth-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-usage-auth")
        resp = client.get(f"/workgroups/{wg['id']}/usage")
        assert resp.status_code == 401

    def test_usage_regular_jwt_returns_403(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-usage-reg-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-usage-reg")
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=_user_headers())
        assert resp.status_code == 403


# ===========================================================================
# SECTION 3: check_and_reserve_session_slot (via session creation API)
# ===========================================================================


class TestCheckAndReserveSessionSlot:
    """Session creation with a workgroup_id enforces quota via
    check_and_reserve_session_slot internally."""

    def test_session_creation_accepts_workgroup_id(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-sess-wgid-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-sess-wgid",
            quota={"max_sessions": 5},
        )
        resp = client.post("/session", json={"workgroup_id": wg["id"]})
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_session_response_includes_workgroup_id(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-sess-resp-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-sess-resp",
            quota={"max_sessions": 5},
        )
        resp = client.post("/session", json={"workgroup_id": wg["id"]})
        data = resp.json()
        assert "workgroup_id" in data, f"Missing workgroup_id in session response: {data}"
        assert data["workgroup_id"] == wg["id"]

    def test_session_increments_workgroup_active_sessions(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-sess-incr-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-sess-incr",
            quota={"max_sessions": 5},
        )
        client.post("/session", json={"workgroup_id": wg["id"]})
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage["usage"]["active_sessions"] == 1, f"Expected 1 active session: {usage}"

    def test_two_sessions_increment_by_two(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-sess-two-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-sess-two",
            quota={"max_sessions": 5},
        )
        client.post("/session", json={"workgroup_id": wg["id"]})
        client.post("/session", json={"workgroup_id": wg["id"]})
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage["usage"]["active_sessions"] == 2, f"Expected 2 active sessions: {usage}"

    def test_session_creation_returns_429_when_quota_exceeded(self, client: TestClient) -> None:
        """Creating sessions beyond max_sessions must return 429 Too Many Requests."""
        ns = _create_namespace(client, name="wq-quota-limit-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-quota-limit",
            quota={"max_sessions": 2},
        )
        # Fill quota
        r1 = client.post("/session", json={"workgroup_id": wg["id"]})
        r2 = client.post("/session", json={"workgroup_id": wg["id"]})
        assert r1.status_code == 201
        assert r2.status_code == 201
        # Exceed quota
        r3 = client.post("/session", json={"workgroup_id": wg["id"]})
        assert r3.status_code == 429, f"Expected 429 quota exceeded, got {r3.status_code}: {r3.text}"

    def test_429_response_has_detail_message(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-429-msg-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-429-msg",
            quota={"max_sessions": 1},
        )
        client.post("/session", json={"workgroup_id": wg["id"]})
        resp = client.post("/session", json={"workgroup_id": wg["id"]})
        assert resp.status_code == 429
        body = resp.json()
        assert "detail" in body, f"Missing detail in 429 response: {body}"
        detail = body["detail"].lower()
        assert any(kw in detail for kw in ("quota", "limit", "session", "exceed", "full")), \
            f"detail message not descriptive: {body['detail']}"

    def test_available_slots_decrements_when_session_created(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-avail-decr-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-avail-decr",
            quota={"max_sessions": 3},
        )
        client.post("/session", json={"workgroup_id": wg["id"]})
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage["available_slots"] == 2, f"Expected 2 available: {usage}"

    def test_utilization_pct_updates_after_session_creation(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-util-pct-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-util-pct",
            quota={"max_sessions": 4},
        )
        client.post("/session", json={"workgroup_id": wg["id"]})
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        # 1/4 = 25%
        assert abs(usage["utilization_pct"] - 25.0) < 0.01, \
            f"Expected utilization 25%, got {usage['utilization_pct']}"

    def test_session_without_workgroup_id_still_works(self, client: TestClient) -> None:
        """Sessions without a workgroup_id should continue to work normally."""
        resp = client.post("/session")
        assert resp.status_code == 201

    def test_session_with_unknown_workgroup_id_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/session",
            json={"workgroup_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code in (404, 422), \
            f"Expected 404/422 for unknown workgroup, got {resp.status_code}"

    def test_workgroup_without_quota_has_unlimited_sessions(self, client: TestClient) -> None:
        """Workgroup with no quota (or None) should not block session creation."""
        ns = _create_namespace(client, name="wq-unlimited-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-unlimited")
        # Create several sessions — should all succeed
        for _ in range(5):
            resp = client.post("/session", json={"workgroup_id": wg["id"]})
            assert resp.status_code == 201, f"Unexpected 429 for unlimited workgroup: {resp.text}"


# ===========================================================================
# SECTION 4: Session lifecycle + quota tracking
# ===========================================================================


class TestQuotaTrackingLifecycle:
    """Quota tracking must update as sessions are created and destroyed."""

    def test_destroy_session_decrements_active_sessions(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-destroy-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-destroy",
            quota={"max_sessions": 3},
        )
        sess_resp = client.post("/session", json={"workgroup_id": wg["id"]})
        sid = sess_resp.json()["session_id"]
        # Verify 1 active
        usage_before = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage_before["usage"]["active_sessions"] == 1

        # Destroy session
        client.delete(f"/session/{sid}")

        # Verify 0 active
        usage_after = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage_after["usage"]["active_sessions"] == 0, \
            f"Expected 0 after destroy: {usage_after}"

    def test_slot_freed_after_destroy_allows_new_session(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-freed-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-freed",
            quota={"max_sessions": 1},
        )
        sess1 = client.post("/session", json={"workgroup_id": wg["id"]})
        sid = sess1.json()["session_id"]
        # Quota full
        over = client.post("/session", json={"workgroup_id": wg["id"]})
        assert over.status_code == 429

        # Destroy first session → frees the slot
        client.delete(f"/session/{sid}")

        # Now a new session should succeed
        new_sess = client.post("/session", json={"workgroup_id": wg["id"]})
        assert new_sess.status_code == 201, \
            f"Expected 201 after freeing slot, got {new_sess.status_code}: {new_sess.text}"

    def test_available_slots_restores_after_destroy(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-restore-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-restore",
            quota={"max_sessions": 2},
        )
        sess = client.post("/session", json={"workgroup_id": wg["id"]})
        sid = sess.json()["session_id"]
        client.delete(f"/session/{sid}")
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage["available_slots"] == 2, f"Slots not restored: {usage}"

    def test_utilization_zero_after_all_sessions_destroyed(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-util-zero-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-util-zero",
            quota={"max_sessions": 3},
        )
        sess = client.post("/session", json={"workgroup_id": wg["id"]})
        sid = sess.json()["session_id"]
        client.delete(f"/session/{sid}")
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage["utilization_pct"] == 0.0, f"Expected 0% utilization: {usage}"


# ===========================================================================
# SECTION 5: check_and_reserve_session_slot as callable
# ===========================================================================


class TestCheckAndReserveCallable:
    """check_and_reserve_session_slot must be importable and callable."""

    def test_function_is_importable(self) -> None:
        import ponddb.api.namespace_routes as nr
        assert hasattr(nr, "check_and_reserve_session_slot"), \
            "check_and_reserve_session_slot not found in ponddb.namespace_routes"

    def test_function_is_callable(self) -> None:
        import ponddb.api.namespace_routes as nr
        fn = getattr(nr, "check_and_reserve_session_slot", None)
        assert fn is not None and callable(fn), \
            "check_and_reserve_session_slot must be callable"

    def test_raises_on_quota_exceeded(self) -> None:
        """Direct call with a quota-exceeded workgroup raises an appropriate exception."""
        import ponddb.api.namespace_routes as nr
        fn = getattr(nr, "check_and_reserve_session_slot", None)
        assert fn is not None, "check_and_reserve_session_slot not found"

        # Build a synthetic workgroup with 0 available slots
        workgroup = {
            "id": "test-wg-id",
            "quota": {"max_sessions": 2},
            "active_sessions": 2,  # already at limit
        }

        with pytest.raises(Exception) as exc_info:
            fn(workgroup)
        # The exception message should indicate quota exceeded
        msg = str(exc_info.value).lower()
        assert any(kw in msg for kw in ("quota", "limit", "exceed", "full", "session")), \
            f"Exception message not descriptive: {exc_info.value}"

    def test_succeeds_when_slot_available(self) -> None:
        """Direct call with available slots should return without raising."""
        import ponddb.api.namespace_routes as nr
        fn = getattr(nr, "check_and_reserve_session_slot", None)
        assert fn is not None, "check_and_reserve_session_slot not found"

        workgroup = {
            "id": "test-wg-id",
            "quota": {"max_sessions": 5},
            "active_sessions": 2,
        }
        # Must not raise
        try:
            fn(workgroup)
        except Exception as exc:
            pytest.fail(f"check_and_reserve_session_slot raised unexpectedly: {exc}")

    def test_returns_reservation_info(self) -> None:
        """Direct call should return some confirmation (dict or truthy value)."""
        import ponddb.api.namespace_routes as nr
        fn = getattr(nr, "check_and_reserve_session_slot", None)
        assert fn is not None, "check_and_reserve_session_slot not found"

        workgroup = {
            "id": "test-wg-id",
            "quota": {"max_sessions": 5},
            "active_sessions": 1,
        }
        result = fn(workgroup)
        assert result is not None, "check_and_reserve_session_slot should return a truthy value"

    def test_unlimited_workgroup_never_raises(self) -> None:
        """Workgroup with no quota (None max_sessions) should never block."""
        import ponddb.api.namespace_routes as nr
        fn = getattr(nr, "check_and_reserve_session_slot", None)
        assert fn is not None

        workgroup = {
            "id": "test-wg-id",
            "quota": None,
            "active_sessions": 9999,
        }
        try:
            fn(workgroup)
        except Exception as exc:
            pytest.fail(f"Unlimited workgroup should not raise: {exc}")


# ===========================================================================
# SECTION 6: Reconciliation task
# ===========================================================================


class TestReconciliationTask:
    """reconcile_workgroup_usage must be importable and correct stale counts."""

    def test_reconcile_function_is_importable(self) -> None:
        import ponddb.api.namespace_routes as nr
        assert hasattr(nr, "reconcile_workgroup_usage"), \
            "reconcile_workgroup_usage not found in ponddb.namespace_routes"

    def test_reconcile_function_is_callable(self) -> None:
        import ponddb.api.namespace_routes as nr
        fn = getattr(nr, "reconcile_workgroup_usage", None)
        assert callable(fn), "reconcile_workgroup_usage must be callable"

    def test_reconcile_sets_active_sessions_from_actual_sessions(
        self, client: TestClient
    ) -> None:
        """After reconciliation, active_sessions in usage must match real session count."""
        ns = _create_namespace(client, name="wq-recon-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-recon",
            quota={"max_sessions": 5},
        )
        # Create 2 real sessions
        s1 = client.post("/session", json={"workgroup_id": wg["id"]})
        s2 = client.post("/session", json={"workgroup_id": wg["id"]})
        assert s1.status_code == 201
        assert s2.status_code == 201

        # Trigger reconciliation
        resp = client.post(
            f"/workgroups/{wg['id']}/reconcile",
            headers=_admin_headers(),
        )
        assert resp.status_code in (200, 202, 204), \
            f"Reconcile endpoint not found or failed: {resp.status_code} {resp.text}"

        # Usage must now be accurate
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage["usage"]["active_sessions"] == 2, \
            f"Reconcile did not fix count: {usage}"

    def test_reconcile_endpoint_exists(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-recon-ep-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-recon-ep")
        resp = client.post(
            f"/workgroups/{wg['id']}/reconcile",
            headers=_admin_headers(),
        )
        assert resp.status_code not in (404, 405), \
            f"POST /workgroups/{{id}}/reconcile endpoint missing: {resp.status_code}"

    def test_reconcile_returns_404_for_unknown_workgroup(self, client: TestClient) -> None:
        resp = client.post(
            "/workgroups/00000000-0000-0000-0000-000000000000/reconcile",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    def test_reconcile_requires_admin(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-recon-auth-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wq-recon-auth")
        resp = client.post(f"/workgroups/{wg['id']}/reconcile")
        assert resp.status_code == 401

    def test_reconcile_fixes_stale_count_after_external_destroy(
        self, client: TestClient
    ) -> None:
        """If active_sessions count is stale (e.g. above real count), reconcile corrects it."""
        ns = _create_namespace(client, name="wq-stale-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-stale",
            quota={"max_sessions": 5},
        )
        # Create then destroy a session
        sess = client.post("/session", json={"workgroup_id": wg["id"]})
        sid = sess.json()["session_id"]
        client.delete(f"/session/{sid}")

        # Force-reconcile
        client.post(f"/workgroups/{wg['id']}/reconcile", headers=_admin_headers())

        # Usage should be 0
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=_admin_headers()).json()
        assert usage["usage"]["active_sessions"] == 0, \
            f"Reconcile did not clear stale session: {usage}"

    def test_reconcile_response_contains_reconciled_count(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wq-recon-resp-ns")
        wg = _create_workgroup(
            client, namespace_id=ns["id"], name="wq-recon-resp",
            quota={"max_sessions": 5},
        )
        client.post("/session", json={"workgroup_id": wg["id"]})
        resp = client.post(
            f"/workgroups/{wg['id']}/reconcile",
            headers=_admin_headers(),
        )
        if resp.status_code in (200, 202):
            data = resp.json()
            # Should include some indication of current state
            assert isinstance(data, dict), "Reconcile response should be a JSON object"
            # Must have at least one useful field
            useful_keys = {"active_sessions", "reconciled", "workgroup_id", "detail"}
            assert any(k in data for k in useful_keys), \
                f"Reconcile response has no useful fields: {data}"
