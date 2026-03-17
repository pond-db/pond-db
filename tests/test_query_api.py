"""Tests for DuckDB query execution HTTP API.

Defines expected behavior for:
  POST   /session          — create a DuckDB session
  DELETE /session/{id}     — destroy a session
  POST   /query            — execute SQL, return JSON results
  GET    /sessions         — list active sessions

Tests import from ponddb.app and will fail until routes are implemented.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-query-api-key-xyz"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure POND_API_KEY is set for every test in this module."""
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)


@pytest.fixture
def client(_set_api_key) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Standard auth headers for /query requests."""
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def session_id(client: TestClient) -> str:
    """Create a fresh session and return its ID."""
    resp = client.post("/session")
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# POST /session — create session
# ---------------------------------------------------------------------------


def test_create_session_returns_201(client: TestClient) -> None:
    resp = client.post("/session")
    assert resp.status_code == 201


def test_create_session_returns_session_id(client: TestClient) -> None:
    resp = client.post("/session")
    data = resp.json()
    assert "session_id" in data
    assert isinstance(data["session_id"], str)
    assert len(data["session_id"]) > 0


def test_create_session_id_is_unique(client: TestClient) -> None:
    id1 = client.post("/session").json()["session_id"]
    id2 = client.post("/session").json()["session_id"]
    assert id1 != id2


def test_create_session_response_has_status_active(client: TestClient) -> None:
    data = client.post("/session").json()
    assert data.get("status") == "ACTIVE"


def test_create_session_content_type_json(client: TestClient) -> None:
    resp = client.post("/session")
    assert "application/json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# DELETE /session/{id} — destroy session
# ---------------------------------------------------------------------------


def test_destroy_session_returns_200(client: TestClient, session_id: str) -> None:
    resp = client.delete(f"/session/{session_id}")
    assert resp.status_code == 200


def test_destroy_session_unknown_id_returns_404(client: TestClient) -> None:
    resp = client.delete("/session/does-not-exist-abc123")
    assert resp.status_code == 404


def test_destroy_session_response_has_detail(client: TestClient) -> None:
    # Deleting a non-existent session: structured error
    resp = client.delete("/session/ghost-session")
    assert resp.status_code == 404
    body = resp.json()
    assert "detail" in body


def test_destroy_session_twice_returns_404_second_time(client: TestClient, session_id: str) -> None:
    client.delete(f"/session/{session_id}")
    resp = client.delete(f"/session/{session_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /sessions — list sessions
# ---------------------------------------------------------------------------


def test_list_sessions_returns_200(client: TestClient) -> None:
    resp = client.get("/sessions")
    assert resp.status_code == 200


def test_list_sessions_returns_list(client: TestClient) -> None:
    data = client.get("/sessions").json()
    assert isinstance(data, list)


def test_list_sessions_includes_created_session(client: TestClient, session_id: str) -> None:
    sessions = client.get("/sessions").json()
    ids = [s["session_id"] for s in sessions]
    assert session_id in ids


def test_list_sessions_excludes_destroyed_session(client: TestClient, session_id: str) -> None:
    client.delete(f"/session/{session_id}")
    sessions = client.get("/sessions").json()
    ids = [s["session_id"] for s in sessions]
    assert session_id not in ids


def test_list_sessions_each_item_has_session_id_and_status(
    client: TestClient, session_id: str
) -> None:
    sessions = client.get("/sessions").json()
    for s in sessions:
        assert "session_id" in s
        assert "status" in s


# ---------------------------------------------------------------------------
# POST /query — happy path
# ---------------------------------------------------------------------------


def test_query_simple_select_returns_200(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1 AS n"}, headers=auth_headers
    )
    assert resp.status_code == 200


def test_query_response_has_columns(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1 AS n"}, headers=auth_headers
    )
    data = resp.json()
    assert "columns" in data
    assert isinstance(data["columns"], list)
    assert "n" in data["columns"]


def test_query_response_has_rows(client: TestClient, session_id: str, auth_headers: dict) -> None:
    resp = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1 AS n"}, headers=auth_headers
    )
    data = resp.json()
    assert "rows" in data
    assert isinstance(data["rows"], list)


def test_query_response_has_rowcount(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1 AS n"}, headers=auth_headers
    )
    data = resp.json()
    assert "rowcount" in data
    assert isinstance(data["rowcount"], int)
    assert data["rowcount"] == 1


def test_query_response_has_elapsed_ms(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1 AS n"}, headers=auth_headers
    )
    data = resp.json()
    assert "elapsed_ms" in data
    assert isinstance(data["elapsed_ms"], (int, float))
    assert data["elapsed_ms"] >= 0


def test_query_simple_select_row_values(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 42 AS answer"},
        headers=auth_headers,
    )
    data = resp.json()
    assert data["rows"] == [[42]]


def test_query_multirow_select(client: TestClient, session_id: str, auth_headers: dict) -> None:
    sql = "SELECT unnest([1, 2, 3]) AS n"
    resp = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    data = resp.json()
    assert data["rowcount"] == 3
    assert len(data["rows"]) == 3


def test_query_multicolumn_select(client: TestClient, session_id: str, auth_headers: dict) -> None:
    sql = "SELECT 'alice' AS name, 30 AS age"
    resp = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    data = resp.json()
    assert set(data["columns"]) == {"name", "age"}
    assert data["rows"] == [["alice", 30]]


def test_query_empty_result_set(client: TestClient, session_id: str, auth_headers: dict) -> None:
    # WHERE FALSE always yields zero rows
    sql = "SELECT 1 AS n WHERE 1 = 0"
    resp = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    data = resp.json()
    assert data["rowcount"] == 0
    assert data["rows"] == []


def test_query_create_table_returns_200(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    sql = "CREATE TABLE t1 (id INTEGER, val TEXT)"
    resp = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    assert resp.status_code == 200


def test_query_create_table_then_insert_then_select(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE kv (k TEXT, v INTEGER)"},
        headers=auth_headers,
    )
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "INSERT INTO kv VALUES ('a', 1), ('b', 2)"},
        headers=auth_headers,
    )
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT * FROM kv ORDER BY k"},
        headers=auth_headers,
    )
    data = resp.json()
    assert data["rowcount"] == 2
    assert data["rows"][0] == ["a", 1]
    assert data["rows"][1] == ["b", 2]


def test_query_non_select_ddl_has_zero_rows(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    sql = "CREATE TABLE empty_ddl (id INTEGER)"
    resp = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    data = resp.json()
    assert data["rows"] == []


# ---------------------------------------------------------------------------
# POST /query — error cases
# ---------------------------------------------------------------------------


def test_query_invalid_sql_returns_400(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT FROM WHERE INVALID"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_query_invalid_sql_response_has_detail(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "NOT VALID SQL AT ALL !!!"},
        headers=auth_headers,
    )
    body = resp.json()
    assert "detail" in body
    assert isinstance(body["detail"], str)
    assert len(body["detail"]) > 0


def test_query_unknown_session_returns_404(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/query", json={"session_id": "ghost-session-xyz", "sql": "SELECT 1"}, headers=auth_headers
    )
    assert resp.status_code == 404


def test_query_missing_session_id_returns_422(client: TestClient, auth_headers: dict) -> None:
    resp = client.post("/query", json={"sql": "SELECT 1"}, headers=auth_headers)
    assert resp.status_code == 422


def test_query_missing_sql_returns_422(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post("/query", json={"session_id": session_id}, headers=auth_headers)
    assert resp.status_code == 422


def test_query_empty_sql_returns_400(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post("/query", json={"session_id": session_id, "sql": ""}, headers=auth_headers)
    assert resp.status_code == 400


def test_query_after_session_destroyed_returns_404(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    client.delete(f"/session/{session_id}")
    resp = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers
    )
    assert resp.status_code == 404


def test_query_table_not_found_returns_400(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT * FROM nonexistent_table"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /query — session isolation
# ---------------------------------------------------------------------------


def test_sessions_are_isolated(client: TestClient, auth_headers: dict) -> None:
    """Tables created in session A are not visible from session B."""
    sid_a = client.post("/session").json()["session_id"]
    sid_b = client.post("/session").json()["session_id"]

    client.post(
        "/query",
        json={"session_id": sid_a, "sql": "CREATE TABLE secret (x INTEGER)"},
        headers=auth_headers,
    )

    resp = client.post(
        "/query",
        json={"session_id": sid_b, "sql": "SELECT * FROM secret"},
        headers=auth_headers,
    )
    assert resp.status_code == 400  # table does not exist in session B


def test_data_persists_within_same_session(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """INSERT in one query is visible in the next within the same session."""
    client.post(
        "/query",
        json={
            "session_id": session_id,
            "sql": "CREATE TABLE counter (n INTEGER)",
        },
        headers=auth_headers,
    )
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "INSERT INTO counter VALUES (99)"},
        headers=auth_headers,
    )
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT n FROM counter"},
        headers=auth_headers,
    )
    data = resp.json()
    assert data["rows"] == [[99]]


# ---------------------------------------------------------------------------
# POST /query — format parameter
# ---------------------------------------------------------------------------


def test_query_default_format_is_json(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1 AS n"}, headers=auth_headers
    )
    assert resp.status_code == 200
    # response body must be parseable as JSON (TestClient already does this)
    data = resp.json()
    assert "rows" in data


def test_query_explicit_json_format(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1 AS n", "format": "json"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "rows" in data


def test_query_unsupported_format_returns_400(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1", "format": "xml"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
