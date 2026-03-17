"""Tests for endpoint-level auth guards and Pydantic input validation.

Expected behaviour (TDD — tests define the target state):
  - POST /session accepts unauthenticated requests (creates session for any caller)
  - GET  /schema  requires auth → 401 when unauthenticated
  - GET  /metrics is public (Prometheus convention — no auth required)
  - POST /query with SQL > 50 000 chars → 422 (Pydantic max_length)
  - GET  /queries/{slug} with an invalid slug (spaces, path traversal, etc.) → 422
  - Valid requests with auth still work (happy path)
"""

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-secret-key-abc123"
VALID_HEADERS = {"X-API-Key": VALID_KEY}


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-jwt-secret")


@pytest.fixture
def client(_set_env) -> TestClient:
    import importlib
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /session — open endpoint (no auth required)
# ---------------------------------------------------------------------------


def test_create_session_without_auth_returns_201(client: TestClient) -> None:
    """POST /session is open — any caller can create a session."""
    resp = client.post("/session")
    assert resp.status_code == 201
    assert "session_id" in resp.json()


def test_create_session_with_valid_api_key_returns_201(client: TestClient) -> None:
    resp = client.post("/session", headers=VALID_HEADERS)
    assert resp.status_code == 201
    data = resp.json()
    assert "session_id" in data


def test_create_session_with_bearer_jwt_returns_201(client: TestClient) -> None:
    """Bearer JWT (issued via /auth/token) is also accepted."""
    # Get a token first
    token_resp = client.post("/auth/token", json={"api_key": VALID_KEY})
    assert token_resp.status_code == 200
    access_token = token_resp.json()["access_token"]

    resp = client.post(
        "/session",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /schema — must require auth
# ---------------------------------------------------------------------------


def test_get_schema_without_auth_returns_401(client: TestClient) -> None:
    resp = client.get("/schema?session_id=any-id")
    assert resp.status_code == 401


def test_get_schema_with_wrong_key_returns_401(client: TestClient) -> None:
    resp = client.get("/schema?session_id=any-id", headers={"X-API-Key": "bad"})
    assert resp.status_code == 401


def test_get_schema_with_valid_key_and_known_session(client: TestClient) -> None:
    """Authenticated call with a real session returns 200 (empty schema list)."""
    sid = client.post("/session", headers=VALID_HEADERS).json()["session_id"]
    resp = client.get(f"/schema?session_id={sid}", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_schema_with_valid_key_unknown_session_returns_404(
    client: TestClient,
) -> None:
    resp = client.get("/schema?session_id=ghost-999", headers=VALID_HEADERS)
    assert resp.status_code == 404


def test_get_schema_401_response_has_detail(client: TestClient) -> None:
    resp = client.get("/schema?session_id=x")
    assert resp.status_code == 401
    assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# GET /metrics — public (Prometheus convention, no auth)
# ---------------------------------------------------------------------------


def test_get_metrics_without_auth_returns_200(client: TestClient) -> None:
    """Prometheus scrape endpoints are unauthenticated by convention."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "ponddb_sessions_active" in resp.text


def test_get_metrics_content_type(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# POST /query — SQL max_length = 50 000 chars
# ---------------------------------------------------------------------------


def test_query_oversized_sql_returns_422(client: TestClient) -> None:
    """SQL body > 50 000 characters must be rejected by Pydantic with 422."""
    sid = client.post("/session", headers=VALID_HEADERS).json()["session_id"]
    oversized_sql = "SELECT 1; " * 5001  # 50 010 chars
    assert len(oversized_sql) > 50_000
    resp = client.post(
        "/query",
        json={"session_id": sid, "sql": oversized_sql},
        headers=VALID_HEADERS,
    )
    assert resp.status_code == 422


def test_query_exactly_50000_chars_is_accepted(client: TestClient) -> None:
    """SQL body of exactly 50 000 characters must pass validation."""
    sid = client.post("/session", headers=VALID_HEADERS).json()["session_id"]
    # Build exactly 50 000 chars: "SELECT 1" padded with spaces
    boundary_sql = "SELECT 1" + " " * (50_000 - len("SELECT 1"))
    assert len(boundary_sql) == 50_000
    resp = client.post(
        "/query",
        json={"session_id": sid, "sql": boundary_sql},
        headers=VALID_HEADERS,
    )
    # Should not be 422 — may be 200 or 400 (DuckDB might reject the padded SQL)
    assert resp.status_code != 422


def test_query_50001_chars_returns_422(client: TestClient) -> None:
    """One char over the limit → 422."""
    sid = client.post("/session", headers=VALID_HEADERS).json()["session_id"]
    too_long = "x" * 50_001
    resp = client.post(
        "/query",
        json={"session_id": sid, "sql": too_long},
        headers=VALID_HEADERS,
    )
    assert resp.status_code == 422


def test_query_422_has_pydantic_validation_error_detail(client: TestClient) -> None:
    """422 body must include Pydantic-style detail with loc/msg/type."""
    sid = client.post("/session", headers=VALID_HEADERS).json()["session_id"]
    resp = client.post(
        "/query",
        json={"session_id": sid, "sql": "x" * 50_001},
        headers=VALID_HEADERS,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    # Pydantic v2 format: list of error dicts with loc/msg
    errors = body["detail"]
    assert isinstance(errors, list)
    assert len(errors) > 0
    assert "loc" in errors[0]
    assert "msg" in errors[0]


# ---------------------------------------------------------------------------
# GET /queries/{slug} — slug regex validation
# ---------------------------------------------------------------------------


def test_get_query_invalid_slug_with_spaces_returns_422(client: TestClient) -> None:
    """Slug with spaces is not a valid slug → 422."""
    resp = client.get("/queries/invalid slug here", headers=VALID_HEADERS)
    assert resp.status_code == 422


def test_get_query_invalid_slug_path_traversal_returns_422(client: TestClient) -> None:
    """Path-traversal-style slug must be rejected → 422."""
    resp = client.get("/queries/../../../etc", headers=VALID_HEADERS)
    # Either 404 (route not matched) or 422 (validation error) — must not be 200
    assert resp.status_code in (404, 422)


def test_get_query_invalid_slug_special_chars_returns_422(client: TestClient) -> None:
    """Slug with disallowed special characters → 422."""
    resp = client.get("/queries/slug@with!special#chars", headers=VALID_HEADERS)
    assert resp.status_code == 422


def test_get_query_valid_slug_format_returns_404_not_422(client: TestClient) -> None:
    """A well-formed slug that doesn't exist should give 404, not 422."""
    resp = client.get("/queries/valid-slug-123", headers=VALID_HEADERS)
    # Valid format but non-existent → 404 (not a validation error)
    assert resp.status_code == 404


def test_get_query_valid_slug_with_uppercase_returns_422(client: TestClient) -> None:
    """Slugs should be lowercase — uppercase letters are invalid → 422."""
    resp = client.get("/queries/Invalid-Slug", headers=VALID_HEADERS)
    assert resp.status_code == 422


def test_get_query_slug_too_long_returns_422(client: TestClient) -> None:
    """Slug exceeding max length → 422."""
    long_slug = "a" * 256
    resp = client.get(f"/queries/{long_slug}", headers=VALID_HEADERS)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Health endpoint remains public (regression guard)
# ---------------------------------------------------------------------------


def test_health_remains_public(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
