"""Integration tests for JWT authentication.

Covers:
- POST /auth/token  — API key → JWT access + refresh tokens
- POST /auth/refresh — refresh token → new access token
- JWT middleware on protected endpoints (/query, /history)
- API key backward-compat (still accepted alongside JWT)
- Token structure: tenant_id, exp, scopes
- Token expiry: 1-hour default, configurable
- Edge cases: expired, malformed, wrong-secret tokens
"""

import os
import time
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt, JWTError

# ---------------------------------------------------------------------------
# Constants used throughout
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-api-key-jwt-suite"
JWT_SECRET = "super-secret-for-testing-only"
WRONG_SECRET = "not-the-right-secret"
DEFAULT_TENANT_ID = "default"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars before every test in this module."""
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)


@pytest.fixture
def client(env_setup) -> TestClient:
    import importlib
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def session_id(client: TestClient) -> str:
    resp = client.post("/session")
    assert resp.status_code == 201
    return resp.json()["session_id"]


@pytest.fixture
def access_token(client: TestClient) -> str:
    """Obtain a valid JWT access token via /auth/token."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code == 200, f"Expected 200 from /auth/token, got {resp.status_code}: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture
def refresh_token(client: TestClient) -> str:
    """Obtain a valid JWT refresh token via /auth/token."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code == 200
    return resp.json()["refresh_token"]


# ---------------------------------------------------------------------------
# POST /auth/token — happy path
# ---------------------------------------------------------------------------


def test_token_endpoint_exists(client: TestClient) -> None:
    """POST /auth/token must not return 404/405."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code not in (404, 405), f"Endpoint missing: {resp.text}"


def test_token_endpoint_returns_200_for_valid_api_key(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code == 200


def test_token_response_contains_access_token(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 0


def test_token_response_contains_refresh_token(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert "refresh_token" in data
    assert isinstance(data["refresh_token"], str)
    assert len(data["refresh_token"]) > 0


def test_token_response_contains_token_type(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    data = resp.json()
    assert "token_type" in data
    assert data["token_type"].lower() == "bearer"


def test_token_response_contains_expires_in(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    data = resp.json()
    assert "expires_in" in data
    assert isinstance(data["expires_in"], int)
    assert data["expires_in"] > 0


# ---------------------------------------------------------------------------
# POST /auth/token — access token claims
# ---------------------------------------------------------------------------


def test_access_token_contains_tenant_id_claim(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert "tenant_id" in claims


def test_access_token_tenant_id_matches_expected(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    # Should include some non-empty tenant_id
    assert claims["tenant_id"] is not None
    assert len(str(claims["tenant_id"])) > 0


def test_access_token_contains_exp_claim(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert "exp" in claims
    assert isinstance(claims["exp"], int)


def test_access_token_expires_in_approximately_one_hour(client: TestClient) -> None:
    before = int(time.time())
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    after = int(time.time())
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    exp = claims["exp"]
    # Token should expire between 55 and 70 minutes from now
    assert exp >= before + 55 * 60, f"exp {exp} < before+55m {before + 55*60}"
    assert exp <= after + 70 * 60, f"exp {exp} > after+70m {after + 70*60}"


def test_access_token_contains_scopes_claim(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert "scopes" in claims
    # scopes should be a list or a space-separated string
    scopes = claims["scopes"]
    assert scopes is not None


def test_access_token_scopes_include_query(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    scopes = claims["scopes"]
    if isinstance(scopes, list):
        scope_set = set(scopes)
    else:
        scope_set = set(str(scopes).split())
    assert "query" in scope_set or "read" in scope_set or "write" in scope_set, \
        f"Expected a query/read/write scope, got: {scopes}"


def test_access_token_signed_with_hs256(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    header = jose_jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"


def test_access_token_verifiable_with_jwt_secret(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    # Should not raise
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert claims is not None


def test_access_token_not_verifiable_with_wrong_secret(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    with pytest.raises(JWTError):
        jose_jwt.decode(token, WRONG_SECRET, algorithms=["HS256"])


# ---------------------------------------------------------------------------
# POST /auth/token — refresh token claims
# ---------------------------------------------------------------------------


def test_refresh_token_contains_type_claim(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["refresh_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    # Refresh tokens must be distinguishable from access tokens
    token_type = claims.get("type") or claims.get("token_type") or claims.get("scope", "")
    assert "refresh" in str(token_type).lower(), \
        f"Refresh token should have type=refresh in claims, got: {claims}"


def test_refresh_token_has_longer_expiry_than_access_token(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    data = resp.json()
    access_claims = jose_jwt.decode(data["access_token"], JWT_SECRET, algorithms=["HS256"])
    refresh_claims = jose_jwt.decode(data["refresh_token"], JWT_SECRET, algorithms=["HS256"])
    assert refresh_claims["exp"] > access_claims["exp"], \
        "Refresh token should expire later than access token"


# ---------------------------------------------------------------------------
# POST /auth/token — error cases
# ---------------------------------------------------------------------------


def test_token_wrong_api_key_returns_401(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": "completely-wrong-key"})
    assert resp.status_code == 401


def test_token_empty_api_key_returns_401(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": ""})
    assert resp.status_code == 401


def test_token_missing_api_key_field_returns_422(client: TestClient) -> None:
    resp = client.post("/auth/token", json={})
    assert resp.status_code == 422


def test_token_wrong_api_key_has_detail_field(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": "wrong"})
    body = resp.json()
    assert "detail" in body


# ---------------------------------------------------------------------------
# POST /auth/token — configurable expiry
# ---------------------------------------------------------------------------


def test_token_expiry_respects_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """POND_JWT_EXPIRY_SECONDS overrides the 1-hour default."""
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_JWT_EXPIRY_SECONDS", "120")  # 2 minutes

    import importlib
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app

    c = TestClient(app)
    before = int(time.time())
    resp = c.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    # Should expire in ~2 minutes, not 1 hour
    assert claims["exp"] <= before + 180, "Expiry should be ~2 minutes when POND_JWT_EXPIRY_SECONDS=120"
    assert claims["exp"] >= before + 90, "Expiry should be at least 90s when POND_JWT_EXPIRY_SECONDS=120"


# ---------------------------------------------------------------------------
# POST /auth/refresh — happy path
# ---------------------------------------------------------------------------


def test_refresh_endpoint_exists(client: TestClient, refresh_token: str) -> None:
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code not in (404, 405), f"Endpoint missing: {resp.text}"


def test_refresh_returns_200_for_valid_refresh_token(
    client: TestClient, refresh_token: str
) -> None:
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200


def test_refresh_response_contains_new_access_token(
    client: TestClient, refresh_token: str
) -> None:
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 0


def test_refresh_new_access_token_is_valid_jwt(
    client: TestClient, refresh_token: str
) -> None:
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    new_token = resp.json()["access_token"]
    claims = jose_jwt.decode(new_token, JWT_SECRET, algorithms=["HS256"])
    assert "exp" in claims
    assert "tenant_id" in claims


def test_refresh_new_access_token_has_fresh_expiry(
    client: TestClient, refresh_token: str
) -> None:
    before = int(time.time())
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    after = int(time.time())
    new_token = resp.json()["access_token"]
    claims = jose_jwt.decode(new_token, JWT_SECRET, algorithms=["HS256"])
    # Fresh token should expire ~1 hour from now
    assert claims["exp"] >= before + 55 * 60


def test_refresh_response_may_include_new_refresh_token(
    client: TestClient, refresh_token: str
) -> None:
    """Server MAY return a new refresh token (token rotation). If it does, it must be valid."""
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    data = resp.json()
    if "refresh_token" in data:
        new_rt = data["refresh_token"]
        claims = jose_jwt.decode(new_rt, JWT_SECRET, algorithms=["HS256"])
        token_type = claims.get("type") or claims.get("token_type") or ""
        assert "refresh" in str(token_type).lower()


# ---------------------------------------------------------------------------
# POST /auth/refresh — error cases
# ---------------------------------------------------------------------------


def test_refresh_with_invalid_token_returns_401(client: TestClient) -> None:
    resp = client.post("/auth/refresh", json={"refresh_token": "not.a.jwt"})
    assert resp.status_code == 401


def test_refresh_with_access_token_as_refresh_returns_401(
    client: TestClient, access_token: str
) -> None:
    """Access tokens must not be accepted as refresh tokens."""
    resp = client.post("/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


def test_refresh_with_wrong_secret_token_returns_401(client: TestClient) -> None:
    # Craft a token signed with the wrong secret
    fake_token = jose_jwt.encode(
        {"sub": "user", "type": "refresh", "exp": int(time.time()) + 86400},
        WRONG_SECRET,
        algorithm="HS256",
    )
    resp = client.post("/auth/refresh", json={"refresh_token": fake_token})
    assert resp.status_code == 401


def test_refresh_with_expired_refresh_token_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)

    import importlib
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    c = TestClient(app)

    # Craft an already-expired refresh token
    expired_token = jose_jwt.encode(
        {
            "sub": "default",
            "tenant_id": "default",
            "type": "refresh",
            "exp": int(time.time()) - 10,  # expired 10 seconds ago
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    resp = c.post("/auth/refresh", json={"refresh_token": expired_token})
    assert resp.status_code == 401


def test_refresh_missing_refresh_token_field_returns_422(client: TestClient) -> None:
    resp = client.post("/auth/refresh", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# JWT middleware on /query
# ---------------------------------------------------------------------------


def test_query_with_valid_jwt_returns_200(
    client: TestClient, session_id: str, access_token: str
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1 AS n"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200


def test_query_without_auth_returns_401(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
    )
    assert resp.status_code == 401


def test_query_with_expired_jwt_returns_401(
    client: TestClient, session_id: str
) -> None:
    expired_token = jose_jwt.encode(
        {
            "sub": "default",
            "tenant_id": "default",
            "scopes": ["query"],
            "type": "access",
            "exp": int(time.time()) - 10,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401


def test_query_with_malformed_jwt_returns_401(
    client: TestClient, session_id: str
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"Authorization": "Bearer this.is.garbage"},
    )
    assert resp.status_code == 401


def test_query_with_wrong_secret_jwt_returns_401(
    client: TestClient, session_id: str
) -> None:
    bad_token = jose_jwt.encode(
        {"sub": "default", "tenant_id": "default", "scopes": ["query"],
         "type": "access", "exp": int(time.time()) + 3600},
        WRONG_SECRET,
        algorithm="HS256",
    )
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert resp.status_code == 401


def test_query_with_refresh_token_as_bearer_returns_401(
    client: TestClient, session_id: str, refresh_token: str
) -> None:
    """Refresh tokens must not be accepted as Bearer tokens for /query."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Backward compatibility: API key still works on /query
# ---------------------------------------------------------------------------


def test_query_with_api_key_still_returns_200(
    client: TestClient, session_id: str
) -> None:
    """X-API-Key header must still be accepted for backward compatibility."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 42 AS n"},
        headers={"X-API-Key": VALID_API_KEY},
    )
    assert resp.status_code == 200


def test_query_with_wrong_api_key_returns_401(
    client: TestClient, session_id: str
) -> None:
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# JWT middleware on /history
# ---------------------------------------------------------------------------


def test_history_with_valid_jwt_returns_200(
    client: TestClient, access_token: str
) -> None:
    resp = client.get(
        "/history",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200


def test_history_without_auth_returns_401(client: TestClient) -> None:
    resp = client.get("/history")
    assert resp.status_code == 401


def test_history_with_api_key_still_works(client: TestClient) -> None:
    resp = client.get("/history", headers={"X-API-Key": VALID_API_KEY})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Public endpoints remain unauthenticated
# ---------------------------------------------------------------------------


def test_health_requires_no_auth(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200


def test_create_session_requires_no_auth(client: TestClient) -> None:
    resp = client.post("/session")
    assert resp.status_code == 201


def test_list_sessions_requires_no_auth(client: TestClient) -> None:
    resp = client.get("/sessions")
    assert resp.status_code == 200


def test_metrics_requires_no_auth(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Token tenant_id isolation
# ---------------------------------------------------------------------------


def test_access_token_tenant_id_is_present_in_claims(client: TestClient) -> None:
    """JWT must carry tenant_id so middleware can scope resources."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    tenant_id = claims.get("tenant_id")
    assert tenant_id is not None
    assert isinstance(tenant_id, str)
    assert len(tenant_id) > 0


def test_custom_tenant_id_in_token_request(client: TestClient) -> None:
    """If tenant_id is supplied in the token request, it should appear in the JWT claims."""
    resp = client.post(
        "/auth/token",
        json={"api_key": VALID_API_KEY, "tenant_id": "acme-corp"},
    )
    if resp.status_code == 200:
        token = resp.json()["access_token"]
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert claims.get("tenant_id") == "acme-corp"
    else:
        # Server may not support custom tenant_id in token request — that's ok
        assert resp.status_code in (400, 422), \
            f"Expected 200 or 400/422 for custom tenant_id, got {resp.status_code}"


# ---------------------------------------------------------------------------
# /auth/token content-type
# ---------------------------------------------------------------------------


def test_token_response_content_type_is_json(client: TestClient) -> None:
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert "application/json" in resp.headers.get("content-type", "")


def test_refresh_response_content_type_is_json(
    client: TestClient, refresh_token: str
) -> None:
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert "application/json" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# No POND_JWT_SECRET → /auth/token should fail or raise config error (not 200)
# ---------------------------------------------------------------------------


def test_token_endpoint_fails_when_no_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)

    import importlib
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app

    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/auth/token", json={"api_key": VALID_API_KEY})
    # Must not return 200 — either 500 (misconfigured) or 503 or similar
    assert resp.status_code != 200, \
        "Should not issue tokens when POND_JWT_SECRET is unset"
