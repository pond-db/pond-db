"""Tests for JWT token revocation: jti claims, POST /auth/revoke, blocklist check.

Behaviors under test:
1. All issued tokens (access + refresh) contain a jti (JWT ID) claim
2. jti values are unique across token calls
3. POST /auth/revoke endpoint exists and accepts a token
4. After revocation, the token is rejected with 401 on protected endpoints
5. verify_access_token raises HTTPException(401) for a revoked token
6. Redis down → verify_access_token fails open (allow) and logs a warning
7. Revoking a refresh token causes /auth/refresh to return 401
8. Double-revoke is idempotent (no 5xx)
9. Revoke with malformed/missing token returns 400/422
10. Non-revoked tokens are unaffected by other revocations
11. Tokens obtained via /auth/refresh also carry a jti
"""

import time
import uuid
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-revoke-api-key-1234567890"
JWT_SECRET = "super-secret-for-revocation-tests-min16chars"
WRONG_SECRET = "not-the-right-secret-wrong123456"

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
def fresh_token(client: TestClient) -> dict:
    """Return a fresh {"access_token": ..., "refresh_token": ...} dict."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture
def access_token(fresh_token: dict) -> str:
    return fresh_token["access_token"]


@pytest.fixture
def refresh_token_str(fresh_token: dict) -> str:
    return fresh_token["refresh_token"]


# ---------------------------------------------------------------------------
# jti claim presence in issued tokens
# ---------------------------------------------------------------------------


def test_access_token_has_jti_claim(access_token: str) -> None:
    """Issued access tokens must contain a jti (JWT ID) claim."""
    claims = jose_jwt.decode(access_token, JWT_SECRET, algorithms=["HS256"])
    assert "jti" in claims, "access token is missing jti claim"
    assert isinstance(claims["jti"], str)
    assert len(claims["jti"]) > 0


def test_refresh_token_has_jti_claim(refresh_token_str: str) -> None:
    """Issued refresh tokens must contain a jti (JWT ID) claim."""
    claims = jose_jwt.decode(refresh_token_str, JWT_SECRET, algorithms=["HS256"])
    assert "jti" in claims, "refresh token is missing jti claim"
    assert isinstance(claims["jti"], str)
    assert len(claims["jti"]) > 0


def test_access_token_jti_unique_across_calls(client: TestClient) -> None:
    """Each /auth/token call must produce a distinct jti."""
    t1 = client.post("/auth/token", json={"api_key": VALID_API_KEY}).json()["access_token"]
    t2 = client.post("/auth/token", json={"api_key": VALID_API_KEY}).json()["access_token"]
    jti1 = jose_jwt.decode(t1, JWT_SECRET, algorithms=["HS256"])["jti"]
    jti2 = jose_jwt.decode(t2, JWT_SECRET, algorithms=["HS256"])["jti"]
    assert jti1 != jti2, "Two consecutive token calls must produce different jti values"


def test_access_and_refresh_token_have_different_jti(fresh_token: dict) -> None:
    """Access and refresh tokens from the same call must have different jti values."""
    access_jti = jose_jwt.decode(fresh_token["access_token"], JWT_SECRET, algorithms=["HS256"])[
        "jti"
    ]
    refresh_jti = jose_jwt.decode(fresh_token["refresh_token"], JWT_SECRET, algorithms=["HS256"])[
        "jti"
    ]
    assert access_jti != refresh_jti


# ---------------------------------------------------------------------------
# POST /auth/revoke endpoint — basic contract
# ---------------------------------------------------------------------------


def test_revoke_endpoint_exists(client: TestClient, access_token: str) -> None:
    """POST /auth/revoke must not return 404 or 405."""
    resp = client.post("/auth/revoke", json={"token": access_token})
    assert resp.status_code not in (404, 405), f"Endpoint not found or wrong method: {resp.text}"


def test_revoke_valid_access_token_returns_200(client: TestClient, access_token: str) -> None:
    resp = client.post("/auth/revoke", json={"token": access_token})
    assert resp.status_code == 200


def test_revoke_returns_json_response(client: TestClient, access_token: str) -> None:
    resp = client.post("/auth/revoke", json={"token": access_token})
    assert "application/json" in resp.headers.get("content-type", "")
    assert isinstance(resp.json(), dict)


# ---------------------------------------------------------------------------
# Revoke → verify → 401 (the core scenario)
# ---------------------------------------------------------------------------


def test_revoked_access_token_rejected_on_history_endpoint(client: TestClient) -> None:
    """After revocation the same token must be refused on /history."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]

    # Works before revocation
    before = client.get("/history", headers={"Authorization": f"Bearer {token}"})
    assert before.status_code == 200, f"Token should work before revoke: {before.text}"

    # Revoke
    revoke = client.post("/auth/revoke", json={"token": token})
    assert revoke.status_code == 200

    # Must be rejected after revocation
    after = client.get("/history", headers={"Authorization": f"Bearer {token}"})
    assert after.status_code == 401, "Revoked token should yield 401 on /history"


def test_revoked_access_token_rejected_on_query_endpoint(client: TestClient) -> None:
    """After revocation the token must not execute queries."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]

    session_resp = client.post("/session")
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    client.post("/auth/revoke", json={"token": token})

    query_resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert query_resp.status_code == 401, "Revoked token should yield 401 on /query"


def test_verify_access_token_raises_401_for_revoked_jti(env_setup) -> None:
    """verify_access_token must raise HTTPException(401) when jti is blocklisted."""
    from fastapi import HTTPException
    from ponddb.auth.jwt_auth import create_access_token, verify_access_token
    from ponddb.auth import token_blocklist

    token = create_access_token("revoke-test-tenant")
    claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    jti = claims["jti"]

    # Block the jti
    token_blocklist.add_to_blocklist(jti)

    try:
        with pytest.raises(HTTPException) as exc_info:
            verify_access_token(token)
        assert exc_info.value.status_code == 401
    finally:
        # Clean up so we don't pollute other tests
        token_blocklist.remove_from_blocklist(jti)


# ---------------------------------------------------------------------------
# Redis down → fail open with log
# ---------------------------------------------------------------------------


def test_verify_access_token_allows_when_redis_unavailable(env_setup) -> None:
    """verify_access_token must fail open when Redis/blocklist is unreachable."""
    from ponddb.auth.jwt_auth import create_access_token, verify_access_token
    from ponddb.auth import token_blocklist

    token = create_access_token("failopen-tenant")

    # Simulate Redis being down by making is_revoked raise
    with mock.patch.object(
        token_blocklist,
        "is_revoked",
        side_effect=Exception("Redis connection refused"),
    ):
        with mock.patch.object(token_blocklist, "logger") as mock_logger:
            result = verify_access_token(token)
            # Must not raise — fails open
            assert result is not None
            assert result.get("tenant_id") == "failopen-tenant"
            # Warning must be logged
            assert mock_logger.warning.called or mock_logger.error.called, (
                "Redis failure should produce a log warning/error"
            )


def test_protected_endpoint_allows_when_redis_unavailable(client: TestClient) -> None:
    """Protected endpoints must succeed when Redis blocklist check raises."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    token = resp.json()["access_token"]

    from ponddb.auth import token_blocklist

    with mock.patch.object(
        token_blocklist,
        "is_revoked",
        side_effect=Exception("Redis connection refused"),
    ):
        history_resp = client.get("/history", headers={"Authorization": f"Bearer {token}"})
        assert history_resp.status_code == 200, (
            "Endpoint must be accessible when Redis is unavailable (fail open)"
        )


# ---------------------------------------------------------------------------
# Revoking a refresh token
# ---------------------------------------------------------------------------


def test_revoke_refresh_token_returns_200(client: TestClient, refresh_token_str: str) -> None:
    resp = client.post("/auth/revoke", json={"token": refresh_token_str})
    assert resp.status_code == 200


def test_revoked_refresh_token_rejected_at_refresh_endpoint(
    client: TestClient,
) -> None:
    """After revoking a refresh token, POST /auth/refresh must return 401."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    refresh_token = resp.json()["refresh_token"]

    # Works before revocation
    before = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert before.status_code == 200, f"Refresh token should work before revoke: {before.text}"

    # Revoke it
    client.post("/auth/revoke", json={"token": refresh_token})

    # Must fail after revocation
    after = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert after.status_code == 401, "Revoked refresh token should yield 401"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_double_revoke_is_idempotent(client: TestClient, access_token: str) -> None:
    """Revoking the same token twice must not raise an error."""
    r1 = client.post("/auth/revoke", json={"token": access_token})
    assert r1.status_code == 200

    r2 = client.post("/auth/revoke", json={"token": access_token})
    assert r2.status_code == 200, "Second revoke must be idempotent (no 4xx/5xx)"


# ---------------------------------------------------------------------------
# Error cases for /auth/revoke
# ---------------------------------------------------------------------------


def test_revoke_missing_token_field_returns_422(client: TestClient) -> None:
    resp = client.post("/auth/revoke", json={})
    assert resp.status_code == 422


def test_revoke_malformed_token_returns_400_or_422(client: TestClient) -> None:
    """A non-JWT string should return 400 or 422 — not 500."""
    resp = client.post("/auth/revoke", json={"token": "not.a.valid.jwt.at.all"})
    assert resp.status_code in (400, 422), (
        f"Expected 400/422 for malformed token, got {resp.status_code}: {resp.text}"
    )


def test_revoke_wrong_secret_token_returns_400_or_401(client: TestClient) -> None:
    """Token signed with a different secret cannot be verified — should return 400 or 401."""
    fake_token = jose_jwt.encode(
        {
            "sub": "test",
            "type": "access",
            "jti": str(uuid.uuid4()),
            "exp": int(time.time()) + 3600,
        },
        WRONG_SECRET,
        algorithm="HS256",
    )
    resp = client.post("/auth/revoke", json={"token": fake_token})
    assert resp.status_code in (400, 401), (
        f"Expected 400/401 for wrong-secret token, got {resp.status_code}"
    )


def test_revoke_expired_token_succeeds_or_returns_400(client: TestClient) -> None:
    """Revoking an already-expired token should either succeed (200) or return 400 — not 500."""
    expired = jose_jwt.encode(
        {
            "sub": "default",
            "tenant_id": "default",
            "type": "access",
            "jti": str(uuid.uuid4()),
            "exp": int(time.time()) - 60,  # already expired
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    resp = client.post("/auth/revoke", json={"token": expired})
    assert resp.status_code in (200, 400), (
        f"Revoking expired token should be 200 or 400, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Non-revoked tokens remain valid
# ---------------------------------------------------------------------------


def test_non_revoked_token_still_works_after_other_revocation(client: TestClient) -> None:
    """Revoking one token must not invalidate other valid tokens."""
    t1 = client.post("/auth/token", json={"api_key": VALID_API_KEY}).json()["access_token"]
    t2 = client.post("/auth/token", json={"api_key": VALID_API_KEY}).json()["access_token"]

    # Revoke only t1
    client.post("/auth/revoke", json={"token": t1})

    # t2 must still work
    resp = client.get("/history", headers={"Authorization": f"Bearer {t2}"})
    assert resp.status_code == 200, "Non-revoked token should remain valid"


# ---------------------------------------------------------------------------
# jti in tokens obtained via /auth/refresh
# ---------------------------------------------------------------------------


def test_refreshed_access_token_contains_jti(client: TestClient, refresh_token_str: str) -> None:
    """Access tokens issued via /auth/refresh must also carry jti."""
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token_str})
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]
    claims = jose_jwt.decode(new_token, JWT_SECRET, algorithms=["HS256"])
    assert "jti" in claims, "Token from /auth/refresh is missing jti"
    assert len(claims["jti"]) > 0


def test_refreshed_token_jti_differs_from_original(client: TestClient) -> None:
    """Each token issuance must produce a unique jti, including via refresh."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    original_jti = jose_jwt.decode(resp.json()["access_token"], JWT_SECRET, algorithms=["HS256"])[
        "jti"
    ]
    refresh_tok = resp.json()["refresh_token"]

    refresh_resp = client.post("/auth/refresh", json={"refresh_token": refresh_tok})
    new_jti = jose_jwt.decode(
        refresh_resp.json()["access_token"], JWT_SECRET, algorithms=["HS256"]
    )["jti"]

    assert original_jti != new_jti, "Refreshed token must have a new unique jti"


def test_revoked_access_token_cannot_be_used_after_refresh_issued_new_one(
    client: TestClient,
) -> None:
    """Even after a refresh, the original revoked access token must stay rejected."""
    resp = client.post("/auth/token", json={"api_key": VALID_API_KEY})
    original_access = resp.json()["access_token"]
    refresh_tok = resp.json()["refresh_token"]

    # Revoke original access token
    client.post("/auth/revoke", json={"token": original_access})

    # Get a new access token via refresh
    new_resp = client.post("/auth/refresh", json={"refresh_token": refresh_tok})
    assert new_resp.status_code == 200
    new_access = new_resp.json()["access_token"]

    # New token should work
    new_ok = client.get("/history", headers={"Authorization": f"Bearer {new_access}"})
    assert new_ok.status_code == 200, "Newly refreshed token should be valid"

    # Original revoked token must still be rejected
    old_fail = client.get("/history", headers={"Authorization": f"Bearer {original_access}"})
    assert old_fail.status_code == 401, "Original revoked token must stay rejected"


# ---------------------------------------------------------------------------
# token_blocklist module contract
# ---------------------------------------------------------------------------


def test_token_blocklist_module_is_importable() -> None:
    """The ponddb.token_blocklist module must exist and be importable."""
    from ponddb.auth import token_blocklist  # noqa: F401


def test_token_blocklist_has_add_to_blocklist() -> None:
    """token_blocklist must expose an add_to_blocklist(jti) function."""
    from ponddb.auth import token_blocklist

    assert callable(getattr(token_blocklist, "add_to_blocklist", None)), (
        "token_blocklist must have add_to_blocklist()"
    )


def test_token_blocklist_has_is_revoked() -> None:
    """token_blocklist must expose an is_revoked(jti) function."""
    from ponddb.auth import token_blocklist

    assert callable(getattr(token_blocklist, "is_revoked", None)), (
        "token_blocklist must have is_revoked()"
    )


def test_token_blocklist_has_logger() -> None:
    """token_blocklist must expose a logger for Redis-failure logging."""
    from ponddb.auth import token_blocklist
    import logging

    assert isinstance(getattr(token_blocklist, "logger", None), logging.Logger), (
        "token_blocklist must have a logging.Logger named 'logger'"
    )


def test_is_revoked_returns_false_for_unknown_jti(env_setup) -> None:
    """is_revoked must return False for a jti that was never revoked."""
    from ponddb.auth import token_blocklist

    result = token_blocklist.is_revoked("completely-unknown-jti-" + str(uuid.uuid4()))
    assert result is False


def test_add_then_is_revoked_returns_true(env_setup) -> None:
    """add_to_blocklist followed by is_revoked must return True."""
    from ponddb.auth import token_blocklist

    jti = "test-jti-" + str(uuid.uuid4())
    token_blocklist.add_to_blocklist(jti)
    try:
        assert token_blocklist.is_revoked(jti) is True
    finally:
        token_blocklist.remove_from_blocklist(jti)


def test_token_blocklist_has_remove_from_blocklist() -> None:
    """token_blocklist must expose a remove_from_blocklist(jti) for test cleanup."""
    from ponddb.auth import token_blocklist

    assert callable(getattr(token_blocklist, "remove_from_blocklist", None)), (
        "token_blocklist must have remove_from_blocklist() for test cleanup"
    )
