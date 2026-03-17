"""Tests for GET /health/security endpoint — 8-control security health check.

Defines the expected behavior:
  - Returns JSON with 8 named security controls and their boolean status
  - Returns 200 when all P0 controls pass
  - Returns 503 when any P0 control fails
  - P1 controls (e.g. jwt_revocation_enabled) reflect degraded state but don't trigger 503
  - Endpoint requires no auth (it is a prerequisite check)
  - nginx should block this from public (tested via expected response format for internal use)
"""

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# The 8 control names and their priority classification
# ---------------------------------------------------------------------------

EXPECTED_CONTROLS = {
    "jwt_secret_configured",
    "sql_sandbox_enabled",
    "security_headers_enabled",
    "brute_force_protection_enabled",
    "rate_limiting_enabled",
    "audit_logging_enabled",
    "jwt_revocation_enabled",
    "cors_configured",
}

# P0: 503 if any of these are False
P0_CONTROLS = {
    "jwt_secret_configured",
    "sql_sandbox_enabled",
    "security_headers_enabled",
    "brute_force_protection_enabled",
    "rate_limiting_enabled",
    "audit_logging_enabled",
    "cors_configured",
}

# P1: shown in response, but False does NOT trigger 503
P1_CONTROLS = {
    "jwt_revocation_enabled",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """TestClient with a valid JWT secret so all controls can be active."""
    monkeypatch.setenv("POND_JWT_SECRET", "test-secret-for-testing-1234567890")
    monkeypatch.setenv("POND_API_KEY", "test-api-key")
    from ponddb.app import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Happy path: all controls active → 200
# ---------------------------------------------------------------------------


def test_security_health_returns_200_when_all_controls_active(client: TestClient) -> None:
    """All 8 controls active → HTTP 200."""
    resp = client.get("/health/security")
    assert resp.status_code == 200


def test_security_health_content_type_is_json(client: TestClient) -> None:
    resp = client.get("/health/security")
    assert "application/json" in resp.headers["content-type"]


def test_security_health_response_has_status_field(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert "status" in data


def test_security_health_response_has_controls_field(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert "controls" in data
    assert isinstance(data["controls"], dict)


def test_security_health_returns_exactly_8_controls(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert len(data["controls"]) == 8


def test_security_health_control_names_are_correct(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert set(data["controls"].keys()) == EXPECTED_CONTROLS


def test_security_health_all_controls_are_booleans(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    for name, value in data["controls"].items():
        assert isinstance(value, bool), f"Control {name!r} must be bool, got {type(value).__name__}"


def test_security_health_status_is_healthy_when_all_pass(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert data["status"] == "healthy"


def test_security_health_jwt_secret_true_when_configured(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert data["controls"]["jwt_secret_configured"] is True


def test_security_health_sql_sandbox_true_by_default(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert data["controls"]["sql_sandbox_enabled"] is True


def test_security_health_security_headers_true_by_default(client: TestClient) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert data["controls"]["security_headers_enabled"] is True


# ---------------------------------------------------------------------------
# P0 failure: sql_sandbox disabled → 503
# ---------------------------------------------------------------------------


def test_security_health_503_when_sql_sandbox_disabled(client: TestClient, monkeypatch) -> None:
    """Emptying BLOCKED_PATTERNS disables the sandbox — a P0 failure → 503."""
    from ponddb.security import sql_sandbox

    monkeypatch.setattr(sql_sandbox, "BLOCKED_PATTERNS", [])
    resp = client.get("/health/security")
    assert resp.status_code == 503


def test_security_health_sql_sandbox_false_when_patterns_empty(
    client: TestClient, monkeypatch
) -> None:
    from ponddb.security import sql_sandbox

    monkeypatch.setattr(sql_sandbox, "BLOCKED_PATTERNS", [])
    resp = client.get("/health/security")
    data = resp.json()
    assert data["controls"]["sql_sandbox_enabled"] is False


def test_security_health_status_degraded_when_p0_fails(client: TestClient, monkeypatch) -> None:
    from ponddb.security import sql_sandbox

    monkeypatch.setattr(sql_sandbox, "BLOCKED_PATTERNS", [])
    resp = client.get("/health/security")
    data = resp.json()
    assert data["status"] in ("degraded", "unhealthy")


def test_security_health_503_still_returns_json(client: TestClient, monkeypatch) -> None:
    """503 response must still have JSON body — monitoring tools parse it."""
    from ponddb.security import sql_sandbox

    monkeypatch.setattr(sql_sandbox, "BLOCKED_PATTERNS", [])
    resp = client.get("/health/security")
    assert resp.status_code == 503
    data = resp.json()
    assert "controls" in data
    assert "status" in data


# ---------------------------------------------------------------------------
# P0 failure: JWT secret missing → 503
# ---------------------------------------------------------------------------


def test_security_health_503_when_jwt_secret_missing(monkeypatch) -> None:
    """Missing JWT secret is a P0 failure → 503."""
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_V1", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_V2", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)
    from ponddb.app import app

    test_client = TestClient(app, raise_server_exceptions=False)
    resp = test_client.get("/health/security")
    assert resp.status_code == 503


def test_security_health_jwt_secret_false_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_V1", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_V2", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)
    from ponddb.app import app

    test_client = TestClient(app, raise_server_exceptions=False)
    resp = test_client.get("/health/security")
    data = resp.json()
    assert data["controls"]["jwt_secret_configured"] is False


# ---------------------------------------------------------------------------
# P1: Redis down → jwt_revocation_enabled false, NOT 503
#
# The health endpoint determines jwt_revocation_enabled by pinging Redis.
# When POND_REDIS_URL is unset or unreachable, the control reports False.
# Because it is P1, this must NOT cause a 503.
# ---------------------------------------------------------------------------


@pytest.fixture
def client_no_redis(monkeypatch):
    """Client with JWT secret set but no POND_REDIS_URL — simulates Redis unavailable."""
    monkeypatch.setenv("POND_JWT_SECRET", "test-secret-for-testing-1234567890")
    monkeypatch.setenv("POND_API_KEY", "test-api-key")
    monkeypatch.delenv("POND_REDIS_URL", raising=False)
    from ponddb.app import app

    return TestClient(app, raise_server_exceptions=False)


def test_security_health_jwt_revocation_false_when_redis_down(client_no_redis: TestClient) -> None:
    """When POND_REDIS_URL is unset (Redis unavailable), jwt_revocation_enabled is False."""
    resp = client_no_redis.get("/health/security")
    data = resp.json()
    assert data["controls"]["jwt_revocation_enabled"] is False


def test_security_health_no_503_when_redis_down(client_no_redis: TestClient) -> None:
    """Redis down is P1: response is still 200 even with jwt_revocation_enabled=False."""
    resp = client_no_redis.get("/health/security")
    assert resp.status_code == 200


def test_security_health_status_healthy_when_only_p1_fails(client_no_redis: TestClient) -> None:
    """P1-only failures keep status=healthy."""
    resp = client_no_redis.get("/health/security")
    data = resp.json()
    assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# Endpoint routing and method constraints
# ---------------------------------------------------------------------------


def test_security_health_get_is_allowed(client: TestClient) -> None:
    resp = client.get("/health/security")
    assert resp.status_code in (200, 503)  # not 404, not 405


def test_security_health_post_returns_405(client: TestClient) -> None:
    resp = client.post("/health/security")
    assert resp.status_code == 405


def test_security_health_delete_returns_405(client: TestClient) -> None:
    resp = client.delete("/health/security")
    assert resp.status_code == 405


def test_security_health_requires_no_auth(client: TestClient) -> None:
    """Endpoint must be accessible without Authorization header (it's a prereq check)."""
    resp = client.get("/health/security")
    assert resp.status_code != 401
    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# All P0 and P1 controls appear in response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("control", sorted(P0_CONTROLS))
def test_security_health_p0_control_present(client: TestClient, control: str) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert control in data["controls"], f"P0 control {control!r} missing from response"


@pytest.mark.parametrize("control", sorted(P1_CONTROLS))
def test_security_health_p1_control_present(client: TestClient, control: str) -> None:
    resp = client.get("/health/security")
    data = resp.json()
    assert control in data["controls"], f"P1 control {control!r} missing from response"


# ---------------------------------------------------------------------------
# nginx internal-only: verify X-Robots-Tag or similar marker (advisory)
# ---------------------------------------------------------------------------


def test_security_health_response_is_machine_parseable(client: TestClient) -> None:
    """Response structure is stable and parseable by monitoring tools."""
    resp = client.get("/health/security")
    data = resp.json()
    assert isinstance(data, dict)
    assert "status" in data
    assert "controls" in data
    assert isinstance(data["controls"], dict)


def test_security_health_returns_priority_metadata(client: TestClient) -> None:
    """Response includes p0_controls list so callers know which ones trigger 503."""
    resp = client.get("/health/security")
    data = resp.json()
    assert "p0_controls" in data
    assert isinstance(data["p0_controls"], list)
    assert set(data["p0_controls"]) == P0_CONTROLS
