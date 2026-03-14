"""Tests for the GET /sessions API endpoint — namespace filtering, full field set,
and persistence-aware behavior.

Expected behavior for the updated GET /sessions endpoint:
  - Returns only ACTIVE and SUSPENDED sessions (not DESTROYED)
  - Each session object includes: session_id, namespace, status, created_at, last_active
  - Optional ?namespace= query parameter filters by namespace
  - Endpoint remains unauthenticated (public)
  - Sessions created in different namespaces are correctly separated

Tests FAIL until:
  - GET /sessions is updated to return full fields + namespace filtering
  - (Prior tests in test_query_auth.py already cover the 200 status code)
"""

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Fresh TestClient with a reloaded app (clean session state)."""
    import importlib
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def ns_client():
    """Client with POND_API_KEY set (needed for /query in some tests)."""
    import importlib
    import os

    os.environ["POND_API_KEY"] = "test-key-sessions"
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------


def test_list_sessions_returns_200(client: TestClient) -> None:
    resp = client.get("/sessions")
    assert resp.status_code == 200


def test_list_sessions_returns_json_list(client: TestClient) -> None:
    resp = client.get("/sessions")
    assert isinstance(resp.json(), list)


def test_list_sessions_empty_initially(client: TestClient) -> None:
    resp = client.get("/sessions")
    assert resp.json() == []


def test_list_sessions_no_auth_required(client: TestClient) -> None:
    """GET /sessions must not require an API key."""
    resp = client.get("/sessions")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Full field set in response
# ---------------------------------------------------------------------------


def test_list_sessions_includes_session_id(client: TestClient) -> None:
    client.post("/session")
    resp = client.get("/sessions")
    sessions = resp.json()
    assert len(sessions) >= 1
    assert "session_id" in sessions[0]


def test_list_sessions_includes_status(client: TestClient) -> None:
    client.post("/session")
    resp = client.get("/sessions")
    sessions = resp.json()
    assert "status" in sessions[0]


def test_list_sessions_includes_namespace(client: TestClient) -> None:
    """GET /sessions must include namespace in each session object."""
    client.post("/session")
    resp = client.get("/sessions")
    sessions = resp.json()
    assert "namespace" in sessions[0]


def test_list_sessions_includes_created_at(client: TestClient) -> None:
    """GET /sessions must include created_at in each session object."""
    client.post("/session")
    resp = client.get("/sessions")
    sessions = resp.json()
    assert "created_at" in sessions[0]
    assert sessions[0]["created_at"] is not None


def test_list_sessions_includes_last_active(client: TestClient) -> None:
    """GET /sessions must include last_active in each session object."""
    client.post("/session")
    resp = client.get("/sessions")
    sessions = resp.json()
    assert "last_active" in sessions[0]
    assert sessions[0]["last_active"] is not None


def test_list_sessions_status_is_string(client: TestClient) -> None:
    client.post("/session")
    sessions = client.get("/sessions").json()
    assert isinstance(sessions[0]["status"], str)


def test_list_sessions_new_session_status_is_active(client: TestClient) -> None:
    client.post("/session")
    sessions = client.get("/sessions").json()
    assert sessions[0]["status"] == "ACTIVE"


def test_list_sessions_session_id_matches_created(client: TestClient) -> None:
    resp = client.post("/session")
    created_sid = resp.json()["session_id"]
    sessions = client.get("/sessions").json()
    ids = [s["session_id"] for s in sessions]
    assert created_sid in ids


# ---------------------------------------------------------------------------
# Namespace support in POST /session and GET /sessions
# ---------------------------------------------------------------------------


def test_create_session_with_namespace_param(client: TestClient) -> None:
    """POST /session should accept an optional namespace body param."""
    resp = client.post("/session", json={"namespace": "team-alpha"})
    assert resp.status_code == 201


def test_create_session_namespace_reflected_in_list(client: TestClient) -> None:
    """Namespace provided at creation must appear in GET /sessions."""
    client.post("/session", json={"namespace": "team-beta"})
    sessions = client.get("/sessions").json()
    namespaces = [s["namespace"] for s in sessions]
    assert "team-beta" in namespaces


def test_create_session_default_namespace_not_none(client: TestClient) -> None:
    """Sessions created without namespace must still have a non-null namespace."""
    client.post("/session")
    sessions = client.get("/sessions").json()
    assert sessions[0]["namespace"] is not None


# ---------------------------------------------------------------------------
# Namespace filtering via query parameter
# ---------------------------------------------------------------------------


def test_list_sessions_namespace_filter_returns_only_matching(
    client: TestClient,
) -> None:
    """GET /sessions?namespace=X returns only sessions in namespace X."""
    client.post("/session", json={"namespace": "filter-ns"})
    client.post("/session", json={"namespace": "other-ns"})

    resp = client.get("/sessions", params={"namespace": "filter-ns"})
    sessions = resp.json()
    assert all(s["namespace"] == "filter-ns" for s in sessions)
    assert len(sessions) == 1


def test_list_sessions_namespace_filter_excludes_other_namespaces(
    client: TestClient,
) -> None:
    client.post("/session", json={"namespace": "ns-a"})
    client.post("/session", json={"namespace": "ns-b"})

    resp_a = client.get("/sessions", params={"namespace": "ns-a"})
    for s in resp_a.json():
        assert s["namespace"] != "ns-b"


def test_list_sessions_namespace_filter_no_match_returns_empty(
    client: TestClient,
) -> None:
    client.post("/session", json={"namespace": "existing-ns"})
    resp = client.get("/sessions", params={"namespace": "nonexistent-ns"})
    assert resp.json() == []


def test_list_sessions_no_namespace_filter_returns_all(client: TestClient) -> None:
    client.post("/session", json={"namespace": "ns-1"})
    client.post("/session", json={"namespace": "ns-2"})
    client.post("/session", json={"namespace": "ns-3"})
    sessions = client.get("/sessions").json()
    assert len(sessions) == 3


# ---------------------------------------------------------------------------
# Only ACTIVE and SUSPENDED sessions returned (not DESTROYED)
# ---------------------------------------------------------------------------


def test_list_sessions_excludes_destroyed(client: TestClient) -> None:
    resp = client.post("/session")
    sid = resp.json()["session_id"]
    client.delete(f"/session/{sid}")

    sessions = client.get("/sessions").json()
    ids = [s["session_id"] for s in sessions]
    assert sid not in ids


def test_list_sessions_count_after_destroy(client: TestClient) -> None:
    sid1 = client.post("/session").json()["session_id"]
    sid2 = client.post("/session").json()["session_id"]
    client.delete(f"/session/{sid1}")

    sessions = client.get("/sessions").json()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == sid2


# ---------------------------------------------------------------------------
# Content-type
# ---------------------------------------------------------------------------


def test_list_sessions_content_type_is_json(client: TestClient) -> None:
    resp = client.get("/sessions")
    assert "application/json" in resp.headers["content-type"]
