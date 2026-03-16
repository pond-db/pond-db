"""Tests for refresh token device fingerprinting (fp claim).

Feature spec:
- fp claim added to refresh tokens: HMAC(SALT, ip + "|" + ua)
- POST /auth/refresh verifies fp claim on incoming request
- Same device (same IP + UA) → 200
- Different IP → 401
- POND_FINGERPRINT_IP=false → IP ignored, only UA checked

New API expected in ponddb.jwt_auth:
  compute_fingerprint(ip: str, user_agent: str, salt: str, include_ip: bool = True) -> str
  create_refresh_token(tenant_id, ip=None, user_agent=None) -> str  [fp added when ip/ua provided]
  verify_refresh_token(token, ip=None, user_agent=None) -> dict      [raises 401 on fp mismatch]
"""

import hashlib
import hmac
import importlib
import os

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "fp-test-api-key-16chars-min"
JWT_SECRET = "fp-test-jwt-secret-16chars-min"
FP_SALT = "fp-test-salt-for-hmac-16chars"

DEVICE_IP = "192.168.1.100"
DEVICE_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) TestBrowser/1.0"
DIFFERENT_IP = "10.0.0.99"
DIFFERENT_UA = "curl/7.88.1"

TENANT_ID = "test-tenant"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_FINGERPRINT_SALT", FP_SALT)
    # Default: IP checking enabled
    monkeypatch.delenv("POND_FINGERPRINT_IP", raising=False)


@pytest.fixture
def client(env_setup) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def refresh_token_with_fp(env_setup) -> str:
    """Create a refresh token that includes the fp claim for DEVICE_IP + DEVICE_UA."""
    import importlib
    import ponddb.jwt_auth as jwt_module
    importlib.reload(jwt_module)
    from ponddb.jwt_auth import create_refresh_token
    return create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)


@pytest.fixture
def refresh_token_no_fp(env_setup) -> str:
    """Create a refresh token without fp claim (no ip/ua provided)."""
    import importlib
    import ponddb.jwt_auth as jwt_module
    importlib.reload(jwt_module)
    from ponddb.jwt_auth import create_refresh_token
    return create_refresh_token(TENANT_ID)


# ---------------------------------------------------------------------------
# Unit tests: compute_fingerprint
# ---------------------------------------------------------------------------


class TestComputeFingerprint:
    def test_compute_fingerprint_is_importable(self):
        """compute_fingerprint must be importable from ponddb.jwt_auth."""
        from ponddb.jwt_auth import compute_fingerprint  # noqa: F401

    def test_compute_fingerprint_deterministic(self):
        """Same inputs → same output."""
        from ponddb.jwt_auth import compute_fingerprint

        fp1 = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT)
        fp2 = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT)
        assert fp1 == fp2

    def test_compute_fingerprint_different_ip_yields_different_fp(self):
        """Different IP → different fingerprint."""
        from ponddb.jwt_auth import compute_fingerprint

        fp1 = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT)
        fp2 = compute_fingerprint(DIFFERENT_IP, DEVICE_UA, FP_SALT)
        assert fp1 != fp2

    def test_compute_fingerprint_different_ua_yields_different_fp(self):
        """Different User-Agent → different fingerprint."""
        from ponddb.jwt_auth import compute_fingerprint

        fp1 = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT)
        fp2 = compute_fingerprint(DEVICE_IP, DIFFERENT_UA, FP_SALT)
        assert fp1 != fp2

    def test_compute_fingerprint_is_hmac_sha256(self):
        """fp must be HMAC-SHA256 of (ip + "|" + ua) keyed with salt."""
        from ponddb.jwt_auth import compute_fingerprint

        message = (DEVICE_IP + "|" + DEVICE_UA).encode()
        expected = hmac.new(FP_SALT.encode(), message, hashlib.sha256).hexdigest()
        assert compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT) == expected

    def test_compute_fingerprint_without_ip(self):
        """When include_ip=False, only UA is used in HMAC message."""
        from ponddb.jwt_auth import compute_fingerprint

        message = DEVICE_UA.encode()
        expected = hmac.new(FP_SALT.encode(), message, hashlib.sha256).hexdigest()
        result = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT, include_ip=False)
        assert result == expected

    def test_compute_fingerprint_without_ip_ignores_ip_value(self):
        """include_ip=False: fingerprint is the same regardless of IP."""
        from ponddb.jwt_auth import compute_fingerprint

        fp_ip1 = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT, include_ip=False)
        fp_ip2 = compute_fingerprint(DIFFERENT_IP, DEVICE_UA, FP_SALT, include_ip=False)
        assert fp_ip1 == fp_ip2

    def test_compute_fingerprint_returns_string(self):
        from ponddb.jwt_auth import compute_fingerprint

        result = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Unit tests: create_refresh_token with fp claim
# ---------------------------------------------------------------------------


class TestCreateRefreshTokenFingerprint:
    def test_refresh_token_includes_fp_when_ip_and_ua_provided(self):
        """Token created with ip+ua must contain an fp claim."""
        from ponddb.jwt_auth import create_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert "fp" in claims

    def test_refresh_token_fp_matches_expected_hmac(self):
        """fp claim value must equal compute_fingerprint(ip, ua, salt)."""
        from ponddb.jwt_auth import compute_fingerprint, create_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        expected_fp = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT)
        assert claims["fp"] == expected_fp

    def test_refresh_token_no_fp_when_no_ip_ua(self):
        """Token created without ip/ua must NOT contain an fp claim."""
        from ponddb.jwt_auth import create_refresh_token

        token = create_refresh_token(TENANT_ID)
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert "fp" not in claims

    def test_refresh_token_fp_only_when_both_ip_and_ua(self):
        """fp is only added when both ip AND ua are provided; partial → no fp."""
        from ponddb.jwt_auth import create_refresh_token

        # ip only
        token_ip = create_refresh_token(TENANT_ID, ip=DEVICE_IP)
        claims_ip = jose_jwt.decode(token_ip, JWT_SECRET, algorithms=["HS256"])
        assert "fp" not in claims_ip

        # ua only
        token_ua = create_refresh_token(TENANT_ID, user_agent=DEVICE_UA)
        claims_ua = jose_jwt.decode(token_ua, JWT_SECRET, algorithms=["HS256"])
        assert "fp" not in claims_ua

    def test_refresh_token_still_has_required_claims(self):
        """Adding fp must not drop any existing required claims."""
        from ponddb.jwt_auth import create_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        for key in ("sub", "tenant_id", "type", "jti", "iat", "exp"):
            assert key in claims, f"Missing required claim: {key}"
        assert claims["type"] == "refresh"

    def test_refresh_token_fp_uses_configured_salt(self, monkeypatch):
        """fp must change when salt changes."""
        from ponddb.jwt_auth import create_refresh_token

        monkeypatch.setenv("POND_FINGERPRINT_SALT", "salt-one-xxxxxxxxxxxxxxxxxx")
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token as crt
        token1 = crt(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims1 = jose_jwt.decode(token1, JWT_SECRET, algorithms=["HS256"])

        monkeypatch.setenv("POND_FINGERPRINT_SALT", "salt-two-xxxxxxxxxxxxxxxxxx")
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token as crt2
        token2 = crt2(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims2 = jose_jwt.decode(token2, JWT_SECRET, algorithms=["HS256"])

        assert claims1["fp"] != claims2["fp"]


# ---------------------------------------------------------------------------
# Unit tests: verify_refresh_token with fp claim
# ---------------------------------------------------------------------------


class TestVerifyRefreshTokenFingerprint:
    def test_verify_same_ip_and_ua_succeeds(self):
        """verify_refresh_token must succeed when IP and UA match the fp claim."""
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims = verify_refresh_token(token, ip=DEVICE_IP, user_agent=DEVICE_UA)
        assert claims["tenant_id"] == TENANT_ID

    def test_verify_different_ip_raises_401(self):
        """verify_refresh_token must raise HTTPException(401) when IP differs."""
        from fastapi import HTTPException
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        with pytest.raises(HTTPException) as exc_info:
            verify_refresh_token(token, ip=DIFFERENT_IP, user_agent=DEVICE_UA)
        assert exc_info.value.status_code == 401

    def test_verify_different_ua_raises_401(self):
        """verify_refresh_token must raise HTTPException(401) when UA differs."""
        from fastapi import HTTPException
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        with pytest.raises(HTTPException) as exc_info:
            verify_refresh_token(token, ip=DEVICE_IP, user_agent=DIFFERENT_UA)
        assert exc_info.value.status_code == 401

    def test_verify_both_different_raises_401(self):
        """verify_refresh_token raises 401 when both IP and UA differ."""
        from fastapi import HTTPException
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        with pytest.raises(HTTPException) as exc_info:
            verify_refresh_token(token, ip=DIFFERENT_IP, user_agent=DIFFERENT_UA)
        assert exc_info.value.status_code == 401

    def test_verify_token_without_fp_succeeds_when_no_fingerprint_provided(self):
        """Old tokens without fp claim pass when no ip/ua is given (backward compat)."""
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID)
        claims = verify_refresh_token(token)
        assert claims["tenant_id"] == TENANT_ID

    def test_verify_token_without_fp_also_passes_when_fingerprint_provided(self):
        """Old tokens (no fp claim) skip fingerprint check even if caller provides ip/ua.

        This ensures backward compatibility: tokens issued before this feature
        was added are not suddenly invalidated.
        """
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID)  # no fp
        claims = verify_refresh_token(token, ip=DEVICE_IP, user_agent=DEVICE_UA)
        assert claims["tenant_id"] == TENANT_ID

    def test_verify_returns_full_claims_on_success(self):
        """Successful verify must return the full JWT claims dict."""
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims = verify_refresh_token(token, ip=DEVICE_IP, user_agent=DEVICE_UA)
        assert claims.get("type") == "refresh"
        assert "jti" in claims
        assert "exp" in claims


# ---------------------------------------------------------------------------
# Unit tests: POND_FINGERPRINT_IP=false
# ---------------------------------------------------------------------------


class TestFingerprintIPDisabled:
    def test_create_token_with_ip_disabled_omits_ip_from_fp(self, monkeypatch):
        """When POND_FINGERPRINT_IP=false, fp is HMAC of UA only (IP excluded)."""
        monkeypatch.setenv("POND_FINGERPRINT_IP", "false")
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import compute_fingerprint, create_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        claims = jose_jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        expected_fp = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT, include_ip=False)
        assert claims["fp"] == expected_fp

    def test_verify_different_ip_succeeds_when_ip_disabled(self, monkeypatch):
        """POND_FINGERPRINT_IP=false: different IP is accepted at verify time."""
        monkeypatch.setenv("POND_FINGERPRINT_IP", "false")
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        # Now verify with a different IP — should succeed
        claims = verify_refresh_token(token, ip=DIFFERENT_IP, user_agent=DEVICE_UA)
        assert claims["tenant_id"] == TENANT_ID

    def test_verify_different_ua_still_rejected_when_ip_disabled(self, monkeypatch):
        """POND_FINGERPRINT_IP=false: UA mismatch still raises 401."""
        monkeypatch.setenv("POND_FINGERPRINT_IP", "false")
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from fastapi import HTTPException
        from ponddb.jwt_auth import create_refresh_token, verify_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)
        with pytest.raises(HTTPException) as exc_info:
            verify_refresh_token(token, ip=DEVICE_IP, user_agent=DIFFERENT_UA)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Integration tests: POST /auth/refresh endpoint
# ---------------------------------------------------------------------------


class TestRefreshEndpointFingerprint:
    def _issue_refresh_token_with_fp(self, client: TestClient) -> str:
        """Issue a token pair via /auth/token then extract the refresh token fp-signed."""
        # We need to issue a token directly via jwt_auth to have the fp claim;
        # /auth/token doesn't yet accept IP/UA — it will once the endpoint is updated.
        # For now, create the token directly via jwt_auth.
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token

        return create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)

    def test_refresh_same_device_returns_200(self, client: TestClient):
        """POST /auth/refresh with matching IP+UA returns a new access token (200)."""
        refresh_tok = self._issue_refresh_token_with_fp(client)
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_tok},
            headers={
                "User-Agent": DEVICE_UA,
                "X-Forwarded-For": DEVICE_IP,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body

    def test_refresh_different_ip_returns_401(self, client: TestClient):
        """POST /auth/refresh from a different IP must return 401."""
        refresh_tok = self._issue_refresh_token_with_fp(client)
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_tok},
            headers={
                "User-Agent": DEVICE_UA,
                "X-Forwarded-For": DIFFERENT_IP,
            },
        )
        assert resp.status_code == 401

    def test_refresh_different_ua_returns_401(self, client: TestClient):
        """POST /auth/refresh from a different User-Agent must return 401."""
        refresh_tok = self._issue_refresh_token_with_fp(client)
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_tok},
            headers={
                "User-Agent": DIFFERENT_UA,
                "X-Forwarded-For": DEVICE_IP,
            },
        )
        assert resp.status_code == 401

    def test_refresh_no_fp_token_still_works(self, client: TestClient):
        """POST /auth/refresh with a token that has no fp claim succeeds (backward compat)."""
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token

        token = create_refresh_token(TENANT_ID)  # no fp
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": token},
            headers={"User-Agent": DEVICE_UA, "X-Forwarded-For": DEVICE_IP},
        )
        assert resp.status_code == 200

    def test_refresh_ip_disabled_different_ip_returns_200(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """POND_FINGERPRINT_IP=false: /auth/refresh accepts different IP."""
        monkeypatch.setenv("POND_FINGERPRINT_IP", "false")
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token

        # Token issued with POND_FINGERPRINT_IP=false (fp = HMAC(UA only))
        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)

        # Reload app so it picks up the env change
        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        new_client = TestClient(app, raise_server_exceptions=True)

        resp = new_client.post(
            "/auth/refresh",
            json={"refresh_token": token},
            headers={
                "User-Agent": DEVICE_UA,
                "X-Forwarded-For": DIFFERENT_IP,
            },
        )
        assert resp.status_code == 200

    def test_refresh_ip_disabled_different_ua_returns_401(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """POND_FINGERPRINT_IP=false: /auth/refresh still rejects different UA."""
        monkeypatch.setenv("POND_FINGERPRINT_IP", "false")
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token

        token = create_refresh_token(TENANT_ID, ip=DEVICE_IP, user_agent=DEVICE_UA)

        import ponddb.app as app_module
        importlib.reload(app_module)
        from ponddb.app import app
        new_client = TestClient(app, raise_server_exceptions=True)

        resp = new_client.post(
            "/auth/refresh",
            json={"refresh_token": token},
            headers={
                "User-Agent": DIFFERENT_UA,
                "X-Forwarded-For": DEVICE_IP,
            },
        )
        assert resp.status_code == 401

    def test_refresh_endpoint_extracts_ip_from_x_forwarded_for(self, client: TestClient):
        """The /auth/refresh endpoint uses X-Forwarded-For header for IP extraction."""
        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import create_refresh_token

        # Issue token with specific IP
        specific_ip = "172.16.0.5"
        token = create_refresh_token(TENANT_ID, ip=specific_ip, user_agent=DEVICE_UA)

        # Send with same IP in X-Forwarded-For → should succeed
        resp = client.post(
            "/auth/refresh",
            json={"refresh_token": token},
            headers={"User-Agent": DEVICE_UA, "X-Forwarded-For": specific_ip},
        )
        assert resp.status_code == 200

        # Send with wrong IP in X-Forwarded-For → should fail
        resp_bad = client.post(
            "/auth/refresh",
            json={"refresh_token": token},
            headers={"User-Agent": DEVICE_UA, "X-Forwarded-For": DEVICE_IP},
        )
        assert resp_bad.status_code == 401


# ---------------------------------------------------------------------------
# Integration tests: POST /auth/token includes fp when request has IP+UA
# ---------------------------------------------------------------------------


class TestIssueTokenWithFingerprint:
    def test_issue_token_refresh_token_has_fp_claim(self, client: TestClient):
        """POST /auth/token must issue a refresh token with fp claim when request has IP+UA."""
        resp = client.post(
            "/auth/token",
            json={"api_key": VALID_API_KEY},
            headers={
                "User-Agent": DEVICE_UA,
                "X-Forwarded-For": DEVICE_IP,
            },
        )
        assert resp.status_code == 200
        refresh_tok = resp.json()["refresh_token"]
        claims = jose_jwt.decode(refresh_tok, JWT_SECRET, algorithms=["HS256"])
        assert "fp" in claims, "Refresh token must contain fp claim when IP+UA are present"

    def test_issue_token_fp_matches_device(self, client: TestClient):
        """fp claim in issued refresh token must match HMAC(IP, UA, SALT)."""
        resp = client.post(
            "/auth/token",
            json={"api_key": VALID_API_KEY},
            headers={
                "User-Agent": DEVICE_UA,
                "X-Forwarded-For": DEVICE_IP,
            },
        )
        assert resp.status_code == 200
        refresh_tok = resp.json()["refresh_token"]
        claims = jose_jwt.decode(refresh_tok, JWT_SECRET, algorithms=["HS256"])

        import importlib
        import ponddb.jwt_auth as jwt_module
        importlib.reload(jwt_module)
        from ponddb.jwt_auth import compute_fingerprint

        expected_fp = compute_fingerprint(DEVICE_IP, DEVICE_UA, FP_SALT)
        assert claims["fp"] == expected_fp

    def test_full_round_trip_token_refresh_same_device(self, client: TestClient):
        """Full round trip: /auth/token → /auth/refresh with same device → new access token."""
        # Step 1: Issue tokens
        issue_resp = client.post(
            "/auth/token",
            json={"api_key": VALID_API_KEY},
            headers={"User-Agent": DEVICE_UA, "X-Forwarded-For": DEVICE_IP},
        )
        assert issue_resp.status_code == 200
        refresh_tok = issue_resp.json()["refresh_token"]

        # Step 2: Refresh from same device
        refresh_resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_tok},
            headers={"User-Agent": DEVICE_UA, "X-Forwarded-For": DEVICE_IP},
        )
        assert refresh_resp.status_code == 200
        assert "access_token" in refresh_resp.json()

    def test_full_round_trip_token_refresh_different_ip_fails(self, client: TestClient):
        """Full round trip: /auth/token → /auth/refresh from different IP → 401."""
        # Step 1: Issue tokens from DEVICE_IP
        issue_resp = client.post(
            "/auth/token",
            json={"api_key": VALID_API_KEY},
            headers={"User-Agent": DEVICE_UA, "X-Forwarded-For": DEVICE_IP},
        )
        assert issue_resp.status_code == 200
        refresh_tok = issue_resp.json()["refresh_token"]

        # Step 2: Attempt refresh from a different IP
        refresh_resp = client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_tok},
            headers={"User-Agent": DEVICE_UA, "X-Forwarded-For": DIFFERENT_IP},
        )
        assert refresh_resp.status_code == 401
