"""Tests for API key authentication on POST /query.

Defines expected behavior for M3 auth layer:
  - POST /query requires a valid X-API-Key header
  - Missing key  → 401 Unauthorized
  - Invalid key  → 401 Unauthorized
  - Valid key    → request proceeds normally (200 / 400 / 404 per query logic)
  - /health remains public (no auth required)
  - POND_API_KEY env var configures the accepted key
"""

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-secret-key-abc123"
WRONG_KEY = "not-the-right-key"


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure POND_API_KEY is set for every test in this module."""
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)


@pytest.fixture
def client(set_api_key) -> TestClient:
    # Re-import app inside fixture so the env var is already set when the
    # module-level auth dependency is evaluated.
    import importlib
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def session_id(client: TestClient) -> str:
    resp = client.post("/session")
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# Auth header mechanics
# ---------------------------------------------------------------------------


def test_query_missing_api_key_returns_401(client: TestClient, session_id: str) -> None:
    resp = client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"})
    assert resp.status_code == 401


def test_query_wrong_api_key_returns_401(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"X-API-Key": WRONG_KEY},
    )
    assert resp.status_code == 401


def test_query_valid_api_key_returns_200(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1 AS n"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert resp.status_code == 200


def test_query_401_response_has_detail(client: TestClient, session_id: str) -> None:
    resp = client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"})
    body = resp.json()
    assert "detail" in body
    assert isinstance(body["detail"], str)
    assert len(body["detail"]) > 0


def test_query_401_content_type_is_json(client: TestClient, session_id: str) -> None:
    resp = client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"})
    assert "application/json" in resp.headers["content-type"]


def test_query_empty_api_key_header_returns_401(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"X-API-Key": ""},
    )
    assert resp.status_code == 401


def test_query_whitespace_api_key_returns_401(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"X-API-Key": "   "},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth does not interfere with downstream query errors
# ---------------------------------------------------------------------------


def test_query_with_auth_invalid_sql_still_returns_400(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "NOT VALID SQL"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert resp.status_code == 400


def test_query_with_auth_unknown_session_still_returns_404(
    client: TestClient,
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": "ghost-xyz", "sql": "SELECT 1"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert resp.status_code == 404


def test_query_with_auth_missing_sql_still_returns_422(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id},
        headers={"X-API-Key": VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Query results are correct when authenticated
# ---------------------------------------------------------------------------


def test_query_with_auth_returns_columns_and_rows(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 42 AS answer"},
        headers={"X-API-Key": VALID_KEY},
    )
    data = resp.json()
    assert data["columns"] == ["answer"]
    assert data["rows"] == [[42]]
    assert data["rowcount"] == 1
    assert data["elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# Other endpoints remain public (no auth required)
# ---------------------------------------------------------------------------


def test_health_does_not_require_api_key(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200


def test_create_session_does_not_require_api_key(client: TestClient) -> None:
    resp = client.post("/session")
    assert resp.status_code == 201


def test_list_sessions_does_not_require_api_key(client: TestClient) -> None:
    resp = client.get("/sessions")
    assert resp.status_code == 200


def test_destroy_session_does_not_require_api_key(client: TestClient, session_id: str) -> None:
    resp = client.delete(f"/session/{session_id}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POND_API_KEY configuration
# ---------------------------------------------------------------------------


def test_query_respects_pond_api_key_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing POND_API_KEY changes what key is accepted."""
    custom_key = "my-custom-key-xyz"
    monkeypatch.setenv("POND_API_KEY", custom_key)

    import importlib
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    c = TestClient(app)
    sid = c.post("/session").json()["session_id"]

    # Old key is rejected
    resp_old = c.post(
        "/query",
        json={"session_id": sid, "sql": "SELECT 1"},
        headers={"X-API-Key": VALID_KEY},
    )
    assert resp_old.status_code == 401

    # New custom key is accepted
    resp_new = c.post(
        "/query",
        json={"session_id": sid, "sql": "SELECT 1 AS n"},
        headers={"X-API-Key": custom_key},
    )
    assert resp_new.status_code == 200


def test_query_no_pond_api_key_env_var_rejects_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If POND_API_KEY is not set, all /query requests should be rejected (or server raises config error)."""
    monkeypatch.delenv("POND_API_KEY", raising=False)

    import importlib
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    c = TestClient(app)
    sid = c.post("/session").json()["session_id"]

    # No key → 401 (or 500 if server misconfigured — either is acceptable,
    # but must NOT return 200)
    resp = c.post(
        "/query",
        json={"session_id": sid, "sql": "SELECT 1"},
        headers={"X-API-Key": "anything"},
    )
    assert resp.status_code != 200
