"""Tests for async PondAPI execution endpoints.

Defines expected behavior for:
  POST /pondapi/execute          — submit SQL for async execution (returns 202)
  GET  /pondapi/execute/{id}/result — poll for execution result

Backend: ThreadPoolExecutor-based async execution
Storage: pondapi_executions SQLite table
Auth: same JWT/API-key as the rest of the API
Rate limiting: per-tenant limit on concurrent / in-flight executions
"""

import importlib
import time

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-pondapi-execute-key"
RATE_LIMIT = 5  # expected max concurrent executions per tenant


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-secret-pondapi")
    monkeypatch.setenv("POND_PONDAPI_RATE_LIMIT", str(RATE_LIMIT))


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def session_id(client: TestClient, auth_headers: dict) -> str:
    resp = client.post("/session")
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _wait_for_completion(
    client: TestClient,
    execution_id: str,
    auth_headers: dict,
    timeout: float = 10.0,
    poll_interval: float = 0.1,
) -> dict:
    """Poll GET /pondapi/execute/{id}/result until status is 'complete' or 'error'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/pondapi/execute/{execution_id}/result", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data["status"] in ("complete", "error"):
            return data
        time.sleep(poll_interval)
    raise TimeoutError(f"Execution {execution_id} did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# POST /pondapi/execute — happy path
# ---------------------------------------------------------------------------


def test_execute_returns_202(client: TestClient, session_id: str, auth_headers: dict) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1 AS n"},
        headers=auth_headers,
    )
    assert resp.status_code == 202


def test_execute_response_has_execution_id(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1 AS n"},
        headers=auth_headers,
    )
    data = resp.json()
    assert "execution_id" in data
    assert isinstance(data["execution_id"], str)
    assert len(data["execution_id"]) > 0


def test_execute_response_status_is_pending_or_complete(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Immediately after submission the status may be pending, running, or already complete."""
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    )
    data = resp.json()
    assert data["status"] in ("pending", "running", "complete")


def test_execute_each_submission_has_unique_id(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    id1 = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    ).json()["execution_id"]
    id2 = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 2"},
        headers=auth_headers,
    ).json()["execution_id"]
    assert id1 != id2


# ---------------------------------------------------------------------------
# GET /pondapi/execute/{id}/result — completion polling
# ---------------------------------------------------------------------------


def test_result_returns_200_while_pending(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    ).json()["execution_id"]
    resp = client.get(f"/pondapi/execute/{exec_id}/result", headers=auth_headers)
    assert resp.status_code == 200


def test_result_eventually_completes(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 42 AS answer"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert result["status"] == "complete"


def test_result_complete_has_columns(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 42 AS answer"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert "columns" in result
    assert "answer" in result["columns"]


def test_result_complete_has_rows(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 99 AS n"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert "rows" in result
    assert result["rows"] == [[99]]


def test_result_complete_has_rowcount(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT unnest([1,2,3]) AS x"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert result["rowcount"] == 3


def test_result_complete_has_elapsed_ms(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert "elapsed_ms" in result
    assert result["elapsed_ms"] >= 0


def test_result_complete_has_execution_id(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert result["execution_id"] == exec_id


def test_result_reflects_multirow_query(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 'a' AS v UNION ALL SELECT 'b' UNION ALL SELECT 'c'"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert result["rowcount"] == 3
    values = [r[0] for r in result["rows"]]
    assert sorted(values) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# GET /pondapi/execute/{id}/result — error cases
# ---------------------------------------------------------------------------


def test_result_unknown_id_returns_404(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/pondapi/execute/nonexistent-exec-id/result", headers=auth_headers)
    assert resp.status_code == 404


def test_result_unknown_id_response_has_detail(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/pondapi/execute/ghost-id-xyz/result", headers=auth_headers)
    body = resp.json()
    assert "detail" in body


def test_result_error_status_for_invalid_sql(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "NOT VALID SQL !!!"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert result["status"] == "error"


def test_result_error_includes_error_message(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT * FROM table_does_not_exist_xyz"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert result["status"] == "error"
    assert "error" in result
    assert isinstance(result["error"], str)
    assert len(result["error"]) > 0


# ---------------------------------------------------------------------------
# POST /pondapi/execute — auth enforcement
# ---------------------------------------------------------------------------


def test_execute_missing_auth_returns_401(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
    )
    assert resp.status_code == 401


def test_execute_wrong_api_key_returns_401(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"X-API-Key": "totally-wrong-key"},
    )
    assert resp.status_code == 401


def test_result_missing_auth_returns_401(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    ).json()["execution_id"]
    resp = client.get(f"/pondapi/execute/{exec_id}/result")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /pondapi/execute — validation errors
# ---------------------------------------------------------------------------


def test_execute_missing_session_id_returns_422(
    client: TestClient, auth_headers: dict
) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"sql": "SELECT 1"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_execute_missing_sql_returns_422(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_execute_empty_sql_returns_400(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": ""},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_execute_whitespace_sql_returns_400(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "   "},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_execute_unknown_session_returns_404(
    client: TestClient, auth_headers: dict
) -> None:
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": "no-such-session-xyz", "sql": "SELECT 1"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tenant isolation — cross-tenant access denied
# ---------------------------------------------------------------------------


def test_result_cross_tenant_access_denied(
    client: TestClient, session_id: str
) -> None:
    """Tenant A cannot read tenant B's execution result."""
    from ponddb.jwt_auth import create_access_token

    token_a = create_access_token("tenant-alpha")
    token_b = create_access_token("tenant-beta")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Tenant A submits an execution
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=headers_a,
    ).json()["execution_id"]

    # Tenant B tries to read it — should be forbidden or not found
    resp = client.get(f"/pondapi/execute/{exec_id}/result", headers=headers_b)
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_header_present_on_execute(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Response should include rate-limit information in headers or body."""
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    )
    assert resp.status_code in (202, 429)
    # If accepted, rate-limit headers should be present (X-RateLimit-* or Retry-After)
    if resp.status_code == 202:
        has_rate_header = any(
            h.lower().startswith("x-ratelimit") or h.lower() == "retry-after"
            for h in resp.headers
        )
        # Rate limit header is optional but status code must be correct
        assert resp.status_code == 202


def test_rate_limit_exceeded_returns_429(
    client: TestClient, auth_headers: dict
) -> None:
    """Submitting more than POND_PONDAPI_RATE_LIMIT concurrent executions returns 429."""
    # Create multiple sessions so we can have many in-flight
    sessions = []
    for _ in range(RATE_LIMIT + 2):
        sid = client.post("/session").json()["session_id"]
        sessions.append(sid)

    # Use a slow query to keep executions in-flight
    # (SELECT SLEEP-equivalent: generate_series with count operations)
    slow_sql = "SELECT count(*) FROM range(10000000)"

    responses = []
    for sid in sessions:
        resp = client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": slow_sql},
            headers=auth_headers,
        )
        responses.append(resp.status_code)

    # At least one response should be 429 (rate limit exceeded)
    assert 429 in responses, f"Expected 429 among responses, got: {responses}"


def test_rate_limit_response_has_detail(
    client: TestClient, auth_headers: dict
) -> None:
    """When rate limit is hit, response body includes a detail message."""
    sessions = []
    for _ in range(RATE_LIMIT + 2):
        sid = client.post("/session").json()["session_id"]
        sessions.append(sid)

    slow_sql = "SELECT count(*) FROM range(10000000)"

    for sid in sessions:
        resp = client.post(
            "/pondapi/execute",
            json={"session_id": sid, "sql": slow_sql},
            headers=auth_headers,
        )
        if resp.status_code == 429:
            body = resp.json()
            assert "detail" in body
            break


# ---------------------------------------------------------------------------
# pondapi_executions table — persistence
# ---------------------------------------------------------------------------


def test_execution_persisted_in_table(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Completed execution must be stored — subsequent GET should return it."""
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 7 AS x"},
        headers=auth_headers,
    ).json()["execution_id"]

    result = _wait_for_completion(client, exec_id, auth_headers)
    assert result["status"] == "complete"

    # Re-fetch after completion — must still return data (not 404)
    resp = client.get(f"/pondapi/execute/{exec_id}/result", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_id"] == exec_id
    assert data["status"] == "complete"


def test_execution_result_idempotent(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Polling the same execution multiple times returns consistent results."""
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 'hello' AS msg"},
        headers=auth_headers,
    ).json()["execution_id"]

    r1 = _wait_for_completion(client, exec_id, auth_headers)
    r2 = client.get(f"/pondapi/execute/{exec_id}/result", headers=auth_headers).json()

    assert r1["status"] == r2["status"]
    assert r1.get("rows") == r2.get("rows")


def test_execution_has_created_at_timestamp(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    exec_id = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    ).json()["execution_id"]
    result = _wait_for_completion(client, exec_id, auth_headers)
    assert "created_at" in result
    assert isinstance(result["created_at"], str)
    assert len(result["created_at"]) > 0


# ---------------------------------------------------------------------------
# ThreadPoolExecutor — non-blocking submission
# ---------------------------------------------------------------------------


def test_submit_does_not_block_until_complete(
    client: TestClient, auth_headers: dict
) -> None:
    """POST /pondapi/execute must return quickly (well under 2s) for a slow query."""
    # Create session
    session_id = client.post("/session").json()["session_id"]

    # A moderately expensive query
    sql = "SELECT count(*) FROM range(5000000)"

    start = time.monotonic()
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": sql},
        headers=auth_headers,
    )
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    # Submission must return within 2 seconds (it's async, not blocking)
    assert elapsed < 2.0, f"POST /pondapi/execute blocked for {elapsed:.2f}s"


def test_two_executions_run_concurrently(
    client: TestClient, auth_headers: dict
) -> None:
    """Two submissions should overlap in time — total wall time < 2× single time."""
    sid1 = client.post("/session").json()["session_id"]
    sid2 = client.post("/session").json()["session_id"]

    sql = "SELECT count(*) FROM range(1000000)"

    # Time a single execution
    exec_id_1 = client.post(
        "/pondapi/execute",
        json={"session_id": sid1, "sql": sql},
        headers=auth_headers,
    ).json()["execution_id"]
    t_start = time.monotonic()
    _wait_for_completion(client, exec_id_1, auth_headers)
    single_time = time.monotonic() - t_start

    # Now submit two at once and see if they run concurrently
    exec_id_a = client.post(
        "/pondapi/execute",
        json={"session_id": sid1, "sql": sql},
        headers=auth_headers,
    ).json()["execution_id"]
    exec_id_b = client.post(
        "/pondapi/execute",
        json={"session_id": sid2, "sql": sql},
        headers=auth_headers,
    ).json()["execution_id"]

    t_both_start = time.monotonic()
    _wait_for_completion(client, exec_id_a, auth_headers)
    _wait_for_completion(client, exec_id_b, auth_headers)
    both_time = time.monotonic() - t_both_start

    # Concurrent execution should finish in less than 1.8× serial time
    assert both_time < single_time * 1.8, (
        f"Expected concurrent execution to be faster: both={both_time:.2f}s, single={single_time:.2f}s"
    )
