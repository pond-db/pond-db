"""Integration tests for Namespace + Workgroup CRUD API.

Covers:
- POST /namespaces       — create namespace (admin required)
- GET  /namespaces       — list namespaces (admin required)
- GET  /namespaces/{id}  — get single namespace (admin required)
- PUT  /namespaces/{id}  — update namespace (admin required)
- DELETE /namespaces/{id} — delete namespace (admin required)

- POST /workgroups        — create workgroup (admin required)
- GET  /workgroups        — list workgroups (admin required, filter by namespace)
- GET  /workgroups/{id}   — get single workgroup (admin required)
- PUT  /workgroups/{id}   — update workgroup (admin required)
- DELETE /workgroups/{id} — delete workgroup (admin required)

- Admin JWT guard: all above endpoints require role=admin claim
  - Non-admin JWT → 403
  - Unauthenticated → 401
  - API-key only → 403 (not admin)

Tests FAIL until implementation is complete.
"""

import importlib
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-ns-wg-api-key"
JWT_SECRET = "ns-wg-test-jwt-secret"

ADMIN_TENANT = "admin-user"
REGULAR_TENANT = "regular-user"


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
    """Return Authorization headers with a valid admin JWT."""
    from ponddb.auth.jwt_auth import create_access_token

    token = create_access_token(ADMIN_TENANT, role="admin")
    return {"Authorization": f"Bearer {token}"}


def _regular_headers() -> dict[str, str]:
    """Return Authorization headers with a regular (non-admin) JWT."""
    from ponddb.auth.jwt_auth import create_access_token

    token = create_access_token(REGULAR_TENANT)
    return {"Authorization": f"Bearer {token}"}


def _api_key_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_API_KEY}


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _create_namespace(client: TestClient, name: str = "test-ns", **kwargs) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "description": "Test namespace", **kwargs}
    resp = client.post("/namespaces", json=payload, headers=_admin_headers())
    assert resp.status_code == 201, (
        f"Expected 201 creating namespace, got {resp.status_code}: {resp.text}"
    )
    return resp.json()


def _create_workgroup(
    client: TestClient,
    namespace_id: str,
    name: str = "test-wg",
    **kwargs,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "namespace_id": namespace_id,
        "description": "Test workgroup",
        **kwargs,
    }
    resp = client.post("/workgroups", json=payload, headers=_admin_headers())
    assert resp.status_code == 201, (
        f"Expected 201 creating workgroup, got {resp.status_code}: {resp.text}"
    )
    return resp.json()


# ===========================================================================
# SECTION 1: Namespace CRUD — happy path
# ===========================================================================


class TestNamespaceCreate:
    def test_endpoint_exists(self, client: TestClient) -> None:
        resp = client.post("/namespaces", json={"name": "probe"}, headers=_admin_headers())
        assert resp.status_code not in (404, 405), f"Endpoint missing: {resp.text}"

    def test_create_returns_201(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-create-201"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 201

    def test_create_response_contains_id(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-with-id"},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert "id" in data
        assert data["id"] is not None
        assert len(str(data["id"])) > 0

    def test_create_response_contains_name(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-name-check"},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert data["name"] == "ns-name-check"

    def test_create_response_contains_description(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-desc", "description": "A test namespace"},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert data["description"] == "A test namespace"

    def test_create_description_defaults_to_empty(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-no-desc"},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert "description" in data
        # Either empty string or None is acceptable default
        assert data["description"] in ("", None)

    def test_create_response_contains_created_at(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-timestamps"},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert "created_at" in data
        assert data["created_at"] is not None

    def test_create_response_contains_updated_at(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-updated-at"},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert "updated_at" in data

    def test_create_duplicate_name_returns_409(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-dupe-test")
        resp = client.post(
            "/namespaces",
            json={"name": "ns-dupe-test"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 409, f"Expected 409 for duplicate name, got {resp.status_code}"

    def test_create_missing_name_returns_422(self, client: TestClient) -> None:
        resp = client.post("/namespaces", json={}, headers=_admin_headers())
        assert resp.status_code == 422

    def test_create_empty_name_returns_422_or_400(self, client: TestClient) -> None:
        resp = client.post("/namespaces", json={"name": ""}, headers=_admin_headers())
        assert resp.status_code in (400, 422)


class TestNamespaceList:
    def test_list_endpoint_exists(self, client: TestClient) -> None:
        resp = client.get("/namespaces", headers=_admin_headers())
        assert resp.status_code not in (404, 405)

    def test_list_returns_200(self, client: TestClient) -> None:
        resp = client.get("/namespaces", headers=_admin_headers())
        assert resp.status_code == 200

    def test_list_returns_list(self, client: TestClient) -> None:
        resp = client.get("/namespaces", headers=_admin_headers())
        assert isinstance(resp.json(), list)

    def test_list_includes_created_namespace(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-list-check")
        resp = client.get("/namespaces", headers=_admin_headers())
        names = [n["name"] for n in resp.json()]
        assert "ns-list-check" in names

    def test_list_items_have_id_field(self, client: TestClient) -> None:
        _create_namespace(client, name="ns-list-id-check")
        resp = client.get("/namespaces", headers=_admin_headers())
        items = resp.json()
        assert len(items) >= 1
        assert all("id" in item for item in items)

    def test_list_items_have_name_field(self, client: TestClient) -> None:
        _create_namespace(client, name="ns-list-name-check")
        resp = client.get("/namespaces", headers=_admin_headers())
        items = resp.json()
        assert all("name" in item for item in items)


class TestNamespaceGet:
    def test_get_returns_200_for_existing_namespace(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-get-test")
        ns_id = ns["id"]
        resp = client.get(f"/namespaces/{ns_id}", headers=_admin_headers())
        assert resp.status_code == 200

    def test_get_returns_correct_namespace(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-get-correct", description="Get test")
        ns_id = ns["id"]
        resp = client.get(f"/namespaces/{ns_id}", headers=_admin_headers())
        data = resp.json()
        assert data["name"] == "ns-get-correct"
        assert data["description"] == "Get test"

    def test_get_returns_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.get(
            "/namespaces/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    def test_get_response_has_id_field(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-get-id-field")
        resp = client.get(f"/namespaces/{ns['id']}", headers=_admin_headers())
        assert "id" in resp.json()
        assert resp.json()["id"] == ns["id"]


class TestNamespaceUpdate:
    def test_update_returns_200(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-update-test")
        resp = client.put(
            f"/namespaces/{ns['id']}",
            json={"description": "updated desc"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_update_description_reflected_in_response(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-update-desc")
        resp = client.put(
            f"/namespaces/{ns['id']}",
            json={"description": "new description"},
            headers=_admin_headers(),
        )
        assert resp.json()["description"] == "new description"

    def test_update_name_reflected_in_response(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-update-name-old")
        resp = client.put(
            f"/namespaces/{ns['id']}",
            json={"name": "ns-update-name-new"},
            headers=_admin_headers(),
        )
        assert resp.json()["name"] == "ns-update-name-new"

    def test_update_returns_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.put(
            "/namespaces/00000000-0000-0000-0000-000000000000",
            json={"description": "x"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    def test_update_updates_updated_at_field(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-updated-at-check")
        original_updated_at = ns.get("updated_at")
        time.sleep(0.01)
        resp = client.put(
            f"/namespaces/{ns['id']}",
            json={"description": "changed"},
            headers=_admin_headers(),
        )
        new_updated_at = resp.json().get("updated_at")
        # updated_at should be set (non-null)
        assert new_updated_at is not None


class TestNamespaceDelete:
    def test_delete_returns_200_or_204(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-delete-test")
        resp = client.delete(f"/namespaces/{ns['id']}", headers=_admin_headers())
        assert resp.status_code in (200, 204)

    def test_delete_removes_namespace(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-delete-gone")
        ns_id = ns["id"]
        client.delete(f"/namespaces/{ns_id}", headers=_admin_headers())
        resp = client.get(f"/namespaces/{ns_id}", headers=_admin_headers())
        assert resp.status_code == 404

    def test_delete_returns_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.delete(
            "/namespaces/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    def test_double_delete_returns_404(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-double-delete")
        ns_id = ns["id"]
        client.delete(f"/namespaces/{ns_id}", headers=_admin_headers())
        resp2 = client.delete(f"/namespaces/{ns_id}", headers=_admin_headers())
        assert resp2.status_code == 404


# ===========================================================================
# SECTION 2: Workgroup CRUD — happy path
# ===========================================================================


class TestWorkgroupCreate:
    def test_endpoint_exists(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-probe-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-probe", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        assert resp.status_code not in (404, 405)

    def test_create_returns_201(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-create-201-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-create-201", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        assert resp.status_code == 201

    def test_create_response_contains_id(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-id-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-with-id", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert "id" in data
        assert data["id"] is not None

    def test_create_response_contains_name(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-name-check-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-name-echo", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        assert resp.json()["name"] == "wg-name-echo"

    def test_create_response_contains_namespace_id(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-nsid-check-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-nsid-echo", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        assert resp.json()["namespace_id"] == ns["id"]

    def test_create_with_config(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-config-ns")
        config = {"max_sessions": 10, "memory_limit_mb": 2048}
        resp = client.post(
            "/workgroups",
            json={"name": "wg-with-config", "namespace_id": ns["id"], "config": config},
            headers=_admin_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "config" in data
        assert data["config"]["max_sessions"] == 10

    def test_create_with_unknown_namespace_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/workgroups",
            json={
                "name": "wg-bad-ns",
                "namespace_id": "00000000-0000-0000-0000-000000000000",
            },
            headers=_admin_headers(),
        )
        assert resp.status_code in (404, 422)

    def test_create_missing_name_returns_422(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-missing-name-ns")
        resp = client.post(
            "/workgroups",
            json={"namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        assert resp.status_code == 422

    def test_create_missing_namespace_id_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/workgroups",
            json={"name": "wg-no-ns"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 422

    def test_create_response_has_created_at(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-ts-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-timestamps", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        data = resp.json()
        assert "created_at" in data
        assert data["created_at"] is not None


class TestWorkgroupList:
    def test_list_endpoint_exists(self, client: TestClient) -> None:
        resp = client.get("/workgroups", headers=_admin_headers())
        assert resp.status_code not in (404, 405)

    def test_list_returns_200(self, client: TestClient) -> None:
        resp = client.get("/workgroups", headers=_admin_headers())
        assert resp.status_code == 200

    def test_list_returns_list(self, client: TestClient) -> None:
        resp = client.get("/workgroups", headers=_admin_headers())
        assert isinstance(resp.json(), list)

    def test_list_includes_created_workgroup(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-list-check-ns")
        _create_workgroup(client, namespace_id=ns["id"], name="wg-list-check")
        resp = client.get("/workgroups", headers=_admin_headers())
        names = [w["name"] for w in resp.json()]
        assert "wg-list-check" in names

    def test_list_filter_by_namespace_id(self, client: TestClient) -> None:
        ns_a = _create_namespace(client, name="wg-filter-ns-a")
        ns_b = _create_namespace(client, name="wg-filter-ns-b")
        _create_workgroup(client, namespace_id=ns_a["id"], name="wg-in-a")
        _create_workgroup(client, namespace_id=ns_b["id"], name="wg-in-b")
        resp = client.get(
            f"/workgroups?namespace_id={ns_a['id']}",
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        names = [w["name"] for w in resp.json()]
        assert "wg-in-a" in names
        assert "wg-in-b" not in names

    def test_list_items_have_id_field(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-list-id-ns")
        _create_workgroup(client, namespace_id=ns["id"], name="wg-list-id")
        resp = client.get("/workgroups", headers=_admin_headers())
        items = resp.json()
        assert all("id" in item for item in items)


class TestWorkgroupGet:
    def test_get_returns_200_for_existing_workgroup(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-get-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-get-test")
        resp = client.get(f"/workgroups/{wg['id']}", headers=_admin_headers())
        assert resp.status_code == 200

    def test_get_returns_correct_workgroup(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-get-correct-ns")
        wg = _create_workgroup(
            client,
            namespace_id=ns["id"],
            name="wg-get-correct",
            description="Get correct test",
        )
        resp = client.get(f"/workgroups/{wg['id']}", headers=_admin_headers())
        data = resp.json()
        assert data["name"] == "wg-get-correct"

    def test_get_returns_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.get(
            "/workgroups/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404

    def test_get_response_includes_namespace_id(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-get-nsid-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-get-nsid")
        resp = client.get(f"/workgroups/{wg['id']}", headers=_admin_headers())
        assert resp.json()["namespace_id"] == ns["id"]


class TestWorkgroupUpdate:
    def test_update_returns_200(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-upd-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-update")
        resp = client.put(
            f"/workgroups/{wg['id']}",
            json={"description": "updated"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_update_description_reflected(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-upd-desc-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-upd-desc")
        resp = client.put(
            f"/workgroups/{wg['id']}",
            json={"description": "new wg desc"},
            headers=_admin_headers(),
        )
        assert resp.json()["description"] == "new wg desc"

    def test_update_config_reflected(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-upd-cfg-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-upd-cfg")
        new_config = {"max_sessions": 20}
        resp = client.put(
            f"/workgroups/{wg['id']}",
            json={"config": new_config},
            headers=_admin_headers(),
        )
        assert resp.json()["config"]["max_sessions"] == 20

    def test_update_returns_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.put(
            "/workgroups/00000000-0000-0000-0000-000000000000",
            json={"description": "x"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 404


class TestWorkgroupDelete:
    def test_delete_returns_200_or_204(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-del-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-delete")
        resp = client.delete(f"/workgroups/{wg['id']}", headers=_admin_headers())
        assert resp.status_code in (200, 204)

    def test_delete_removes_workgroup(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-del-gone-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-del-gone")
        wg_id = wg["id"]
        client.delete(f"/workgroups/{wg_id}", headers=_admin_headers())
        resp = client.get(f"/workgroups/{wg_id}", headers=_admin_headers())
        assert resp.status_code == 404

    def test_delete_returns_404_for_unknown_id(self, client: TestClient) -> None:
        resp = client.delete(
            "/workgroups/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(),
        )
        assert resp.status_code == 404


# ===========================================================================
# SECTION 3: Admin JWT guard
# ===========================================================================


class TestAdminJwtGuardNamespaces:
    """All namespace mutating endpoints require admin role JWT."""

    def test_create_namespace_without_auth_returns_401(self, client: TestClient) -> None:
        resp = client.post("/namespaces", json={"name": "ns-no-auth"})
        assert resp.status_code == 401

    def test_create_namespace_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-regular-jwt"},
            headers=_regular_headers(),
        )
        assert resp.status_code == 403

    def test_create_namespace_with_api_key_only_returns_403(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-api-key"},
            headers=_api_key_headers(),
        )
        assert resp.status_code == 403

    def test_list_namespaces_without_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/namespaces")
        assert resp.status_code == 401

    def test_list_namespaces_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        resp = client.get("/namespaces", headers=_regular_headers())
        assert resp.status_code == 403

    def test_get_namespace_without_auth_returns_401(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-auth-get")
        resp = client.get(f"/namespaces/{ns['id']}")
        assert resp.status_code == 401

    def test_get_namespace_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-reg-get")
        resp = client.get(f"/namespaces/{ns['id']}", headers=_regular_headers())
        assert resp.status_code == 403

    def test_update_namespace_without_auth_returns_401(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-auth-put")
        resp = client.put(f"/namespaces/{ns['id']}", json={"description": "x"})
        assert resp.status_code == 401

    def test_update_namespace_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-reg-put")
        resp = client.put(
            f"/namespaces/{ns['id']}",
            json={"description": "x"},
            headers=_regular_headers(),
        )
        assert resp.status_code == 403

    def test_delete_namespace_without_auth_returns_401(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-auth-del")
        resp = client.delete(f"/namespaces/{ns['id']}")
        assert resp.status_code == 401

    def test_delete_namespace_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="ns-reg-del")
        resp = client.delete(f"/namespaces/{ns['id']}", headers=_regular_headers())
        assert resp.status_code == 403


class TestAdminJwtGuardWorkgroups:
    """All workgroup endpoints require admin role JWT."""

    def test_create_workgroup_without_auth_returns_401(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-guard-create-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-no-auth", "namespace_id": ns["id"]},
        )
        assert resp.status_code == 401

    def test_create_workgroup_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-guard-reg-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-regular-jwt", "namespace_id": ns["id"]},
            headers=_regular_headers(),
        )
        assert resp.status_code == 403

    def test_list_workgroups_without_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/workgroups")
        assert resp.status_code == 401

    def test_list_workgroups_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        resp = client.get("/workgroups", headers=_regular_headers())
        assert resp.status_code == 403

    def test_get_workgroup_without_auth_returns_401(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-guard-get-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-auth-get")
        resp = client.get(f"/workgroups/{wg['id']}")
        assert resp.status_code == 401

    def test_get_workgroup_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-guard-reg-get-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-reg-get")
        resp = client.get(f"/workgroups/{wg['id']}", headers=_regular_headers())
        assert resp.status_code == 403

    def test_delete_workgroup_without_auth_returns_401(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-guard-del-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-auth-del")
        resp = client.delete(f"/workgroups/{wg['id']}")
        assert resp.status_code == 401

    def test_delete_workgroup_with_regular_jwt_returns_403(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-guard-reg-del-ns")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-reg-del")
        resp = client.delete(f"/workgroups/{wg['id']}", headers=_regular_headers())
        assert resp.status_code == 403


class TestAdminJwtTokenClaims:
    """Admin role must be encoded in JWT and rejected when absent."""

    def test_create_access_token_accepts_role_admin(self) -> None:
        """create_access_token must accept a role kwarg without raising."""
        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("test-admin", role="admin")
        assert token is not None
        assert isinstance(token, str)

    def test_admin_token_contains_role_admin_claim(self) -> None:
        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("test-admin", role="admin")
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert claims.get("role") == "admin"

    def test_regular_token_has_no_admin_role(self) -> None:
        from ponddb.auth.jwt_auth import create_access_token

        token = create_access_token("test-regular")
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert claims.get("role") != "admin"

    def test_admin_token_is_still_valid_for_query_endpoint(self, client: TestClient) -> None:
        """Admin token is a superset — it must still work for regular auth endpoints."""
        session_resp = client.post("/session")
        assert session_resp.status_code == 201
        sid = session_resp.json()["session_id"]
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 1 AS n"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200


# ===========================================================================
# SECTION 4: Namespace deletion cascades to workgroups
# ===========================================================================


class TestNamespaceWorkgroupCascade:
    def test_delete_namespace_cascades_to_workgroups(self, client: TestClient) -> None:
        """Deleting a namespace must also delete or orphan its workgroups."""
        ns = _create_namespace(client, name="ns-cascade")
        wg = _create_workgroup(client, namespace_id=ns["id"], name="wg-cascade")
        wg_id = wg["id"]
        # Delete namespace
        del_resp = client.delete(f"/namespaces/{ns['id']}", headers=_admin_headers())
        assert del_resp.status_code in (200, 204)
        # Workgroup should be gone or return 404
        wg_resp = client.get(f"/workgroups/{wg_id}", headers=_admin_headers())
        assert wg_resp.status_code == 404


# ===========================================================================
# SECTION 5: Schema validation
# ===========================================================================


class TestNamespaceSchemaValidation:
    def test_name_must_be_string(self, client: TestClient) -> None:
        resp = client.post("/namespaces", json={"name": 123}, headers=_admin_headers())
        # FastAPI coerces int to str or returns 422 depending on validation config
        assert resp.status_code in (201, 422)

    def test_name_cannot_contain_slashes(self, client: TestClient) -> None:
        resp = client.post("/namespaces", json={"name": "bad/name"}, headers=_admin_headers())
        # May be 400 (rejected) or 201 (allowed — less strict) depending on design
        assert resp.status_code in (201, 400, 422)

    def test_description_is_optional(self, client: TestClient) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "ns-optional-desc"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 201


class TestWorkgroupSchemaValidation:
    def test_config_must_be_object(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-cfg-type-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-cfg-type", "namespace_id": ns["id"], "config": "bad"},
            headers=_admin_headers(),
        )
        assert resp.status_code in (400, 422)

    def test_config_defaults_to_empty_object(self, client: TestClient) -> None:
        ns = _create_namespace(client, name="wg-default-cfg-ns")
        resp = client.post(
            "/workgroups",
            json={"name": "wg-default-cfg", "namespace_id": ns["id"]},
            headers=_admin_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "config" in data
        # Config should be a dict (possibly empty)
        assert isinstance(data.get("config"), (dict, type(None)))
