"""Integration test: complete customer journey from health check to cleanup.

Exercises the full user workflow in sequence, proving that PondDB
works end-to-end for a real customer scenario.
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
def auth(client: TestClient) -> dict:
    return jwt_headers(client)


@pytest.fixture
def hdrs() -> dict:
    return api_headers()


class TestCompleteCustomerJourney:
    """End-to-end journey: health → session → SQL → datasets → queries → share → cleanup."""

    def test_step_01_health_check(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok" or "status" in data

    def test_step_02_create_session(self, client: TestClient) -> None:
        sid = create_session(client)
        assert len(sid) > 0

    def test_step_03_execute_inline_sql(
        self, client: TestClient, auth: dict
    ) -> None:
        sid = create_session(client)
        result = execute_and_poll(client, sid, "SELECT 42 AS answer", auth)
        assert result["status"] == "complete"
        assert result["columns"] == ["answer"]
        assert result["rows"] == [[42]]
        assert result["rowcount"] == 1

    def test_step_04_upload_csv_dataset(
        self, client: TestClient, hdrs: dict
    ) -> None:
        csv_data = "id,product,revenue\n1,Widget,100.50\n2,Gadget,250.75\n3,Doohickey,50.25\n"
        files = {"file": ("journey_sales.csv", io.BytesIO(csv_data.encode()), "text/csv")}
        resp = client.post("/datasets", files=files, headers=hdrs)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "journey_sales"
        assert data["row_count"] == 3
        assert "revenue" in data["columns"]
        # Cleanup
        client.delete("/datasets/journey_sales", headers=hdrs)

    def test_step_05_query_with_tables(
        self, client: TestClient, auth: dict
    ) -> None:
        """Create table, insert data, query — proving full SQL lifecycle in session."""
        sid = create_session(client)
        # Use /query (sync) to create and populate a table
        client.post(
            "/query",
            json={"session_id": sid, "sql": "CREATE TABLE cities (id INT, city VARCHAR, pop BIGINT)"},
            headers=auth,
        )
        client.post(
            "/query",
            json={
                "session_id": sid,
                "sql": "INSERT INTO cities VALUES (1, 'NYC', 8000000), (2, 'LA', 4000000), (3, 'CHI', 2700000)",
            },
            headers=auth,
        )
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT city, pop FROM cities ORDER BY pop DESC"},
            headers=auth,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rowcount"] == 3
        assert data["rows"][0][0] == "NYC"

    def test_step_06_save_query(self, client: TestClient, auth: dict) -> None:
        resp = client.post(
            "/queries",
            json={
                "title": "Journey Test Query",
                "sql": "SELECT 'hello' AS greeting",
                "visibility": "public",
            },
            headers=auth,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "journey-test-query"
        assert data["visibility"] == "public"

    def test_step_07_list_saved_queries(
        self, client: TestClient, auth: dict
    ) -> None:
        # Save a query first
        client.post(
            "/queries",
            json={"title": "Journey List Test", "sql": "SELECT 1"},
            headers=auth,
        )
        resp = client.get("/queries", headers=auth)
        assert resp.status_code == 200
        queries = resp.json()
        assert isinstance(queries, list)
        titles = [q["title"] for q in queries]
        assert "Journey List Test" in titles

    def test_step_08_create_and_access_share_link(
        self, client: TestClient, auth: dict, hdrs: dict
    ) -> None:
        # Save a public query
        client.post(
            "/queries",
            json={
                "title": "Journey Share Test",
                "sql": "SELECT 'shared' AS result",
                "visibility": "public",
            },
            headers=auth,
        )
        # Access via share slug (public — no auth needed)
        resp = client.get("/q/journey-share-test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slug"] == "journey-share-test"
        assert data["rows"][0][0] == "shared"

    def test_step_09_view_schema(
        self, client: TestClient, auth: dict
    ) -> None:
        sid = create_session(client)
        # Create a table so schema has something
        execute_and_poll(
            client, sid, "CREATE TABLE journey_test (id INT, name VARCHAR)", auth
        )
        resp = client.get(f"/schema?session_id={sid}", headers=auth)
        assert resp.status_code == 200
        schema = resp.json()
        table_names = [t["table_name"] for t in schema]
        assert "journey_test" in table_names
        # Verify columns
        test_table = next(t for t in schema if t["table_name"] == "journey_test")
        col_names = [c["name"] for c in test_table["columns"]]
        assert "id" in col_names
        assert "name" in col_names

    def test_step_10_check_query_history(
        self, client: TestClient, auth: dict
    ) -> None:
        sid = create_session(client)
        # Execute via /query (sync) — this records history
        client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 'history_test'"},
            headers=auth,
        )
        # Check history
        resp = client.get("/history", headers=auth)
        assert resp.status_code == 200
        history = resp.json()
        assert isinstance(history, list)
        # At least our query should be in history
        assert len(history) > 0

    def test_step_11_terminate_session(self, client: TestClient) -> None:
        sid = create_session(client)
        resp = client.delete(f"/session/{sid}")
        assert resp.status_code == 200

    def test_step_12_verify_cleanup(self, client: TestClient) -> None:
        sid = create_session(client)
        client.delete(f"/session/{sid}")
        # Session should no longer appear in list
        resp = client.get("/sessions")
        sessions = resp.json()
        session_ids = [s["session_id"] for s in sessions]
        assert sid not in session_ids

    def test_full_journey_sequential(
        self, client: TestClient, auth: dict, hdrs: dict
    ) -> None:
        """Run the complete journey as a single sequential test."""
        # 1. Health check
        assert client.get("/health").status_code == 200

        # 2. Create session
        sid = create_session(client)

        # 3. Execute SQL via PondAPI (async)
        r = execute_and_poll(client, sid, "SELECT 42 AS answer", auth)
        assert r["status"] == "complete"

        # 4. Upload CSV dataset
        csv = "id,val\n1,100\n2,200\n"
        files = {"file": ("journey_full.csv", io.BytesIO(csv.encode()), "text/csv")}
        resp = client.post("/datasets", files=files, headers=hdrs)
        assert resp.status_code == 201

        # 5. Query via /query (sync) with in-session table
        client.post(
            "/query",
            json={"session_id": sid, "sql": "CREATE TABLE jf (id INT, val INT)"},
            headers=auth,
        )
        client.post(
            "/query",
            json={"session_id": sid, "sql": "INSERT INTO jf VALUES (1, 100), (2, 200)"},
            headers=auth,
        )
        r2 = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT SUM(val) AS total FROM jf"},
            headers=auth,
        )
        assert r2.status_code == 200
        assert r2.json()["rows"][0][0] == 300

        # 6. Save query
        resp = client.post(
            "/queries",
            json={
                "title": "Journey Full Test",
                "sql": "SELECT 42 AS answer",
                "visibility": "public",
            },
            headers=auth,
        )
        assert resp.status_code == 201

        # 7. List queries
        queries = client.get("/queries", headers=auth).json()
        assert any(q["title"] == "Journey Full Test" for q in queries)

        # 8. Access share link
        resp = client.get("/q/journey-full-test")
        assert resp.status_code == 200

        # 9. Schema
        schema = client.get(f"/schema?session_id={sid}", headers=auth).json()
        assert any(t["table_name"] == "jf" for t in schema)

        # 10. History (populated by /query calls)
        history = client.get("/history", headers=auth).json()
        assert len(history) > 0

        # 11. Terminate session
        client.delete(f"/session/{sid}")

        # 12. Verify cleanup
        remaining = client.get("/sessions").json()
        remaining_ids = {s["session_id"] for s in remaining}
        assert sid not in remaining_ids

        # Cleanup dataset
        client.delete("/datasets/journey_full", headers=hdrs)
