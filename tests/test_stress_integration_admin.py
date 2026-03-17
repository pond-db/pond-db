"""Integration test: admin operations — namespaces, workgroups, invites, quotas.

Exercises the full admin workflow: namespace CRUD, workgroup setup,
invite lifecycle, quota enforcement, and usage monitoring.
"""

import pytest
from fastapi.testclient import TestClient

from tests.stress_helpers import (
    admin_jwt_headers,
    jwt_headers,
    make_client,
)


@pytest.fixture
def client() -> TestClient:
    return make_client()


@pytest.fixture
def admin(client: TestClient) -> dict:
    return admin_jwt_headers(client)


class TestAdminOperationsJourney:
    """Full admin workflow: namespace → workgroup → invite → quota → usage."""

    def test_step_01_create_namespace(
        self, client: TestClient, admin: dict
    ) -> None:
        resp = client.post(
            "/namespaces",
            json={"name": "test-org", "description": "Integration test org"},
            headers=admin,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-org"
        assert "id" in data

    def test_step_02_create_workgroup(
        self, client: TestClient, admin: dict
    ) -> None:
        # Create namespace first
        ns = client.post(
            "/namespaces",
            json={"name": "wg-test-ns"},
            headers=admin,
        ).json()

        resp = client.post(
            "/workgroups",
            json={
                "name": "analytics-wg",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 10},
            },
            headers=admin,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "analytics-wg"
        assert data["quota"]["max_sessions"] == 10

    def test_step_03_create_invite(
        self, client: TestClient, admin: dict
    ) -> None:
        resp = client.post(
            "/invites",
            json={
                "email": "admin-test@example.com",
                "role": "member",
                "expires_in_hours": 168,
            },
            headers=admin,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "admin-test@example.com"
        assert data["status"] == "pending"
        assert "token" in data

    def test_step_04_accept_invite_issues_jwt(
        self, client: TestClient, admin: dict
    ) -> None:
        # Create invite
        invite = client.post(
            "/invites",
            json={"email": "accept-test@example.com", "role": "member"},
            headers=admin,
        ).json()
        token = invite["token"]

        # Accept invite (public endpoint — no auth)
        resp = client.post(
            f"/invites/{token}/accept",
            json={"email": "accept-test@example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["status"] == "accepted"
        assert data["email"] == "accept-test@example.com"

    def test_step_05_new_user_jwt_works(
        self, client: TestClient, admin: dict
    ) -> None:
        # Create and accept invite
        invite = client.post(
            "/invites",
            json={"email": "new-user@example.com", "role": "member"},
            headers=admin,
        ).json()
        accept_resp = client.post(
            f"/invites/{invite['token']}/accept",
            json={"email": "new-user@example.com"},
        ).json()
        new_user_token = accept_resp["access_token"]
        new_user_headers = {"Authorization": f"Bearer {new_user_token}"}

        # New user can access queries endpoint
        resp = client.get("/queries", headers=new_user_headers)
        assert resp.status_code == 200

    def test_step_06_tenant_isolation_between_users(
        self, client: TestClient, admin: dict
    ) -> None:
        # Admin saves a query
        admin_auth = jwt_headers(client, tenant_id="default")
        client.post(
            "/queries",
            json={"title": "Admin Secret Query", "sql": "SELECT 'admin'", "visibility": "private"},
            headers=admin_auth,
        )

        # Create a different tenant user
        other_auth = jwt_headers(client, tenant_id="other-tenant")
        other_queries = client.get("/queries", headers=other_auth).json()
        other_titles = [q["title"] for q in other_queries]
        assert "Admin Secret Query" not in other_titles

    def test_step_07_update_workgroup_quota(
        self, client: TestClient, admin: dict
    ) -> None:
        # Create namespace + workgroup
        ns = client.post(
            "/namespaces", json={"name": "quota-test-ns"}, headers=admin
        ).json()
        wg = client.post(
            "/workgroups",
            json={
                "name": "quota-test-wg",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 5},
            },
            headers=admin,
        ).json()

        # Update quota
        resp = client.put(
            f"/workgroups/{wg['id']}",
            json={"quota": {"max_sessions": 20}},
            headers=admin,
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["quota"]["max_sessions"] == 20

    def test_step_08_revoke_invite(
        self, client: TestClient, admin: dict
    ) -> None:
        invite = client.post(
            "/invites",
            json={"email": "revoke-me@example.com", "role": "member"},
            headers=admin,
        ).json()
        token = invite["token"]

        resp = client.delete(f"/invites/{token}", headers=admin)
        assert resp.status_code == 200
        assert resp.json()["detail"] == "revoked"

        # Trying to accept revoked invite should fail
        resp = client.post(
            f"/invites/{token}/accept",
            json={"email": "revoke-me@example.com"},
        )
        assert resp.status_code in (400, 410)

    def test_step_09_workgroup_usage(
        self, client: TestClient, admin: dict
    ) -> None:
        # Create namespace + workgroup
        ns = client.post(
            "/namespaces", json={"name": "usage-test-ns"}, headers=admin
        ).json()
        wg = client.post(
            "/workgroups",
            json={
                "name": "usage-test-wg",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 10},
            },
            headers=admin,
        ).json()

        # Check usage
        resp = client.get(f"/workgroups/{wg['id']}/usage", headers=admin)
        assert resp.status_code == 200
        usage = resp.json()
        assert "usage" in usage
        assert "quota" in usage

    def test_step_10_list_namespaces_and_workgroups(
        self, client: TestClient, admin: dict
    ) -> None:
        # Create a namespace
        ns = client.post(
            "/namespaces", json={"name": "list-test-ns"}, headers=admin
        ).json()

        # Create workgroups
        client.post(
            "/workgroups",
            json={"name": "list-wg-1", "namespace_id": ns["id"]},
            headers=admin,
        )
        client.post(
            "/workgroups",
            json={"name": "list-wg-2", "namespace_id": ns["id"]},
            headers=admin,
        )

        # List namespaces
        ns_list = client.get("/namespaces", headers=admin).json()
        ns_names = [n["name"] for n in ns_list]
        assert "list-test-ns" in ns_names

        # List workgroups
        wg_list = client.get("/workgroups", headers=admin).json()
        wg_names = [w["name"] for w in wg_list]
        assert "list-wg-1" in wg_names
        assert "list-wg-2" in wg_names

    def test_full_admin_journey_sequential(
        self, client: TestClient, admin: dict
    ) -> None:
        """Run the complete admin journey as a single sequential test."""
        # 1. Create namespace
        ns = client.post(
            "/namespaces",
            json={"name": "full-admin-ns", "description": "Full journey"},
            headers=admin,
        ).json()
        assert "id" in ns

        # 2. Create workgroup
        wg = client.post(
            "/workgroups",
            json={
                "name": "full-admin-wg",
                "namespace_id": ns["id"],
                "quota": {"max_sessions": 5},
            },
            headers=admin,
        ).json()
        assert wg["name"] == "full-admin-wg"

        # 3. Create invite
        invite = client.post(
            "/invites",
            json={"email": "full-journey@example.com", "role": "member"},
            headers=admin,
        ).json()
        token = invite["token"]

        # 4. Accept invite
        accept = client.post(
            f"/invites/{token}/accept",
            json={"email": "full-journey@example.com"},
        ).json()
        user_jwt = accept["access_token"]
        user_headers = {"Authorization": f"Bearer {user_jwt}"}

        # 5. New user can list queries
        resp = client.get("/queries", headers=user_headers)
        assert resp.status_code == 200

        # 6. Update quota
        resp = client.put(
            f"/workgroups/{wg['id']}",
            json={"quota": {"max_sessions": 15}},
            headers=admin,
        )
        assert resp.status_code == 200

        # 7. Check usage
        usage = client.get(f"/workgroups/{wg['id']}/usage", headers=admin).json()
        assert usage["quota"]["max_sessions"] == 15

        # 8. Create second invite and revoke
        inv2 = client.post(
            "/invites",
            json={"email": "revoke-journey@example.com", "role": "member"},
            headers=admin,
        ).json()
        client.delete(f"/invites/{inv2['token']}", headers=admin)

        # Verify revoked
        inv_list = client.get("/invites", headers=admin).json()
        revoked = [i for i in inv_list if i["token"] == inv2["token"]]
        assert len(revoked) == 1
        assert revoked[0]["status"] == "revoked"

        # 9. Cleanup — delete workgroup and namespace
        client.delete(f"/workgroups/{wg['id']}", headers=admin)
        client.delete(f"/namespaces/{ns['id']}", headers=admin)

        # Verify cleanup
        ns_list = client.get("/namespaces", headers=admin).json()
        assert not any(n["name"] == "full-admin-ns" for n in ns_list)
