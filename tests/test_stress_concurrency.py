"""Stress tests: rapid sequential load, large results, multi-tenant isolation.

TestClient uses a single ASGI event loop so we test "concurrency" via rapid
sequential bursts — proving the server handles high-throughput request patterns.
Rate limit is bumped to 100 via POND_PONDAPI_RATE_LIMIT=100 in stress_helpers.
"""

import io

import pytest
from fastapi.testclient import TestClient

from tests.stress_helpers import (
    api_headers,
    create_session,
    execute_and_poll,
    jwt_headers,
    make_client,
)


@pytest.fixture
def client() -> TestClient:
    return make_client()


@pytest.fixture
def sid(client: TestClient) -> str:
    return create_session(client)


@pytest.fixture
def auth(client: TestClient) -> dict:
    return jwt_headers(client)


class TestBurstPondAPIExecutions:
    """10 rapid sequential PondAPI executions all complete successfully."""

    def test_burst_pondapi_executions(
        self, client: TestClient, sid: str, auth: dict
    ) -> None:
        results = []
        for i in range(10):
            result = execute_and_poll(
                client, sid, f"SELECT {i} AS val", auth, timeout=30
            )
            results.append(result)

        assert len(results) == 10
        for r in results:
            assert r["status"] == "complete"
            assert r["rowcount"] == 1

        # Verify all unique values returned
        vals = sorted(r["rows"][0][0] for r in results)
        assert vals == list(range(10))


class TestRapidSessionLifecycle:
    """Create and destroy many sessions — none leak."""

    def test_rapid_session_create_destroy(self, client: TestClient) -> None:
        sids = []
        for _ in range(20):
            sids.append(create_session(client))

        assert len(set(sids)) == 20  # all unique

        for sid in sids:
            resp = client.delete(f"/session/{sid}")
            assert resp.status_code == 200

        # Verify none remain
        resp = client.get("/sessions")
        remaining = resp.json()
        remaining_ids = {s["session_id"] for s in remaining}
        for sid in sids:
            assert sid not in remaining_ids


class TestLargeResultSet:
    """Large query returns all rows without truncation."""

    def test_large_result_set(
        self, client: TestClient, sid: str, auth: dict
    ) -> None:
        result = execute_and_poll(
            client,
            sid,
            "SELECT i FROM generate_series(1, 50000) t(i)",
            auth,
            timeout=60,
        )
        assert result["status"] == "complete"
        assert result["rowcount"] == 50000
        assert len(result["rows"]) == 50000


class TestMultiTenantIsolation:
    """Two tenants cannot see each other's query store entries."""

    def test_multi_tenant_isolation(self, client: TestClient) -> None:
        headers_a = jwt_headers(client, tenant_id="tenant-alpha")
        headers_b = jwt_headers(client, tenant_id="tenant-beta")

        # Tenant A saves a query
        resp_a = client.post(
            "/queries",
            json={"title": "Alpha Query", "sql": "SELECT 1", "visibility": "private"},
            headers=headers_a,
        )
        assert resp_a.status_code == 201

        # Tenant B saves a query
        resp_b = client.post(
            "/queries",
            json={"title": "Beta Query", "sql": "SELECT 2", "visibility": "private"},
            headers=headers_b,
        )
        assert resp_b.status_code == 201

        # Tenant A can only see their own
        list_a = client.get("/queries", headers=headers_a).json()
        titles_a = [q["title"] for q in list_a]
        assert "Alpha Query" in titles_a
        assert "Beta Query" not in titles_a

        # Tenant B can only see their own
        list_b = client.get("/queries", headers=headers_b).json()
        titles_b = [q["title"] for q in list_b]
        assert "Beta Query" in titles_b
        assert "Alpha Query" not in titles_b


class TestBurstDatasetUploads:
    """5 rapid CSV uploads all succeed and become queryable."""

    def test_burst_dataset_uploads(self, client: TestClient) -> None:
        hdrs = api_headers()

        for i in range(5):
            csv_data = f"id,name,value\n{i},item_{i},{i * 10}\n"
            files = {"file": (f"stress_ds_{i}.csv", io.BytesIO(csv_data.encode()), "text/csv")}
            resp = client.post("/datasets", files=files, headers=hdrs)
            assert resp.status_code == 201, f"Upload {i} failed: {resp.text}"

        # Verify all appear in dataset list
        ds_list = client.get("/datasets", headers=hdrs).json()
        ds_names = {d["name"] for d in ds_list}
        for i in range(5):
            assert f"stress_ds_{i}" in ds_names

        # Cleanup
        for i in range(5):
            client.delete(f"/datasets/stress_ds_{i}", headers=hdrs)


class TestBurstSchemaRequests:
    """10 rapid GET /schema calls all return 200."""

    def test_burst_schema_requests(
        self, client: TestClient, sid: str, auth: dict
    ) -> None:
        codes = []
        for _ in range(10):
            resp = client.get(f"/schema?session_id={sid}", headers=auth)
            codes.append(resp.status_code)

        assert all(c == 200 for c in codes), f"Some schema calls failed: {codes}"


class TestSessionOpsUnderLoad:
    """Create 5 sessions, suspend all, resume all, terminate all."""

    def test_session_ops_under_load(self, client: TestClient) -> None:
        auth = jwt_headers(client)
        sids = [create_session(client) for _ in range(5)]

        # Suspend all via HTMX endpoint
        for sid in sids:
            resp = client.post(
                f"/htmx/session/{sid}/suspend", headers=auth
            )
            assert resp.status_code == 200
            assert "suspended" in resp.text.lower()

        # Resume all
        for sid in sids:
            resp = client.post(
                f"/htmx/session/{sid}/resume", headers=auth
            )
            assert resp.status_code == 200
            assert "active" in resp.text.lower()

        # Terminate all
        for sid in sids:
            resp = client.delete(f"/session/{sid}")
            assert resp.status_code == 200

        # Verify all gone
        remaining = client.get("/sessions").json()
        remaining_ids = {s["session_id"] for s in remaining}
        for sid in sids:
            assert sid not in remaining_ids
