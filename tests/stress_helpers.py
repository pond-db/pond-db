"""Shared utilities for stress and integration tests.

Provides reusable helpers for creating TestClients, JWT tokens,
sessions, and common request patterns used across stress test files.
"""

import importlib
import os
import time

from fastapi.testclient import TestClient

VALID_KEY = "stress-test-api-key"
SESSION_SECRET = "stress-test-session-secret"
JWT_SECRET = "stress-test-jwt-secret"


def setup_env() -> None:
    """Set required env vars for stress tests."""
    os.environ["POND_API_KEY"] = VALID_KEY
    os.environ["POND_JWT_SECRET"] = JWT_SECRET
    os.environ["POND_WEBSITE_SESSION_SECRET"] = SESSION_SECRET
    os.environ["POND_PONDAPI_RATE_LIMIT"] = "100"
    os.environ["POND_PONDAPI_RATE_WINDOW"] = "60"


def make_client() -> TestClient:
    """Create a fresh TestClient with clean app state."""
    setup_env()
    import ponddb.app as m

    importlib.reload(m)
    return TestClient(m.app, follow_redirects=False)


def get_jwt(client: TestClient, tenant_id: str = "default") -> str:
    """Exchange API key for JWT access token."""
    resp = client.post(
        "/auth/token",
        json={"api_key": VALID_KEY, "tenant_id": tenant_id},
    )
    assert resp.status_code == 200, f"Auth failed: {resp.text}"
    return resp.json()["access_token"]


def jwt_headers(client: TestClient, tenant_id: str = "default") -> dict:
    """Return Authorization headers with a valid JWT."""
    token = get_jwt(client, tenant_id)
    return {"Authorization": f"Bearer {token}"}


def admin_jwt_headers(client: TestClient) -> dict:
    """Return admin JWT headers."""
    from ponddb.auth.jwt_auth import create_access_token

    token = create_access_token("default", role="admin")
    return {"Authorization": f"Bearer {token}"}


def api_headers() -> dict:
    """Return API key headers."""
    return {"X-API-Key": VALID_KEY}


def create_session(client: TestClient, workgroup_id: str | None = None) -> str:
    """Create a session and return its ID."""
    body: dict = {}
    if workgroup_id:
        body["workgroup_id"] = workgroup_id
    resp = client.post("/session", json=body if body else None)
    assert resp.status_code == 201, f"Session create failed: {resp.text}"
    return resp.json()["session_id"]


def execute_and_poll(
    client: TestClient,
    session_id: str,
    sql: str,
    headers: dict,
    timeout: float = 30.0,
) -> dict:
    """Submit SQL via PondAPI and poll until complete. Returns result dict."""
    resp = client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": sql},
        headers=headers,
    )
    assert resp.status_code == 202, f"Execute failed ({resp.status_code}): {resp.text}"
    exec_id = resp.json()["execution_id"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/pondapi/execute/{exec_id}/result", headers=headers)
        assert r.status_code == 200
        data = r.json()
        if data["status"] in ("complete", "error"):
            return data
        time.sleep(0.1)
    raise TimeoutError(f"Execution {exec_id} did not complete within {timeout}s")
