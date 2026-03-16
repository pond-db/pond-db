"""Tests for CORSMiddleware with POND_CORS_ORIGINS allowlist.

Expected behavior:
- Allowed origin → Access-Control-Allow-Origin (ACAO) header echoes that origin
- Disallowed origin → no ACAO header
- No wildcard (*) in ACAO — allowlist only
- Preflight OPTIONS → 204 with ACAO + Access-Control-Allow-Methods for allowed origins
- Preflight OPTIONS → no ACAO for disallowed origins
- POND_CORS_ORIGINS is comma-separated; whitespace around entries is stripped
"""

import pytest
from fastapi.testclient import TestClient

ALLOWED = "https://app.example.com"
OTHER = "https://other.example.com"
DISALLOWED = "https://evil.example.com"


# ---------------------------------------------------------------------------
# Fixtures — each builds a fresh app with specific CORS env config
# ---------------------------------------------------------------------------


@pytest.fixture
def app_single(monkeypatch):
    """App configured with a single allowed origin."""
    monkeypatch.setenv("POND_CORS_ORIGINS", ALLOWED)
    from ponddb.app import build_app

    return build_app()


@pytest.fixture
def app_multi(monkeypatch):
    """App configured with two allowed origins (comma-separated)."""
    monkeypatch.setenv("POND_CORS_ORIGINS", f"{ALLOWED},{OTHER}")
    from ponddb.app import build_app

    return build_app()


@pytest.fixture
def app_no_cors(monkeypatch):
    """App with POND_CORS_ORIGINS unset — no origins should be allowed."""
    monkeypatch.delenv("POND_CORS_ORIGINS", raising=False)
    from ponddb.app import build_app

    return build_app()


# ---------------------------------------------------------------------------
# Allowed origin — simple (non-preflight) requests
# ---------------------------------------------------------------------------


def test_allowed_origin_gets_acao_header(app_single) -> None:
    """An allowed origin must receive Access-Control-Allow-Origin in response."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == ALLOWED


def test_acao_header_is_never_wildcard(app_single) -> None:
    """ACAO header must never be '*' — allowlist semantics require echoing the origin."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert response.headers.get("access-control-allow-origin") != "*"


def test_response_still_succeeds_for_allowed_origin(app_single) -> None:
    """Allowed origin request should not be rejected — 200 from /health."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Disallowed origin — simple requests
# ---------------------------------------------------------------------------


def test_disallowed_origin_no_acao_header(app_single) -> None:
    """A request from a disallowed origin must not receive ACAO header."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": DISALLOWED})
    assert "access-control-allow-origin" not in response.headers


def test_no_origin_header_no_acao(app_single) -> None:
    """Request with no Origin header should receive no ACAO header."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.get("/health")
    assert "access-control-allow-origin" not in response.headers


def test_response_still_200_for_disallowed_origin(app_single) -> None:
    """CORS rejection means no ACAO header, not an HTTP error — 200 still returned."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": DISALLOWED})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Preflight (OPTIONS) — allowed origin
# ---------------------------------------------------------------------------


def test_preflight_allowed_origin_returns_204(app_single) -> None:
    """OPTIONS preflight from an allowed origin should return 204 No Content."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/health",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 204


def test_preflight_allowed_origin_has_acao_header(app_single) -> None:
    """OPTIONS preflight from an allowed origin must include ACAO header."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/health",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == ALLOWED


def test_preflight_allowed_origin_has_acam_header(app_single) -> None:
    """OPTIONS preflight should include Access-Control-Allow-Methods."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/health",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-methods" in response.headers


def test_preflight_acao_not_wildcard(app_single) -> None:
    """ACAO in preflight response must not be '*'."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/health",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "*"


# ---------------------------------------------------------------------------
# Preflight (OPTIONS) — disallowed origin
# ---------------------------------------------------------------------------


def test_preflight_disallowed_origin_no_acao_header(app_single) -> None:
    """OPTIONS preflight from a disallowed origin must not include ACAO header."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/health",
        headers={
            "Origin": DISALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_preflight_disallowed_origin_not_204(app_single) -> None:
    """Disallowed preflight must not return 204 (CORSMiddleware returns 400)."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/health",
        headers={
            "Origin": DISALLOWED,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code != 204


# ---------------------------------------------------------------------------
# Multiple origins in POND_CORS_ORIGINS
# ---------------------------------------------------------------------------


def test_first_origin_in_multi_allowlist_gets_acao(app_multi) -> None:
    """First origin in comma-separated list should be allowed."""
    client = TestClient(app_multi, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert response.headers.get("access-control-allow-origin") == ALLOWED


def test_second_origin_in_multi_allowlist_gets_acao(app_multi) -> None:
    """Second origin in comma-separated list should also be allowed."""
    client = TestClient(app_multi, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": OTHER})
    assert response.headers.get("access-control-allow-origin") == OTHER


def test_unlisted_origin_blocked_in_multi_allowlist(app_multi) -> None:
    """Origin not in the multi-origin list should not receive ACAO."""
    client = TestClient(app_multi, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": DISALLOWED})
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# POND_CORS_ORIGINS env var parsing edge cases
# ---------------------------------------------------------------------------


def test_cors_origins_whitespace_stripped(monkeypatch) -> None:
    """Origins with surrounding whitespace in env var should still be allowed."""
    monkeypatch.setenv("POND_CORS_ORIGINS", f"  {ALLOWED}  ,  {OTHER}  ")
    from ponddb.app import build_app

    app = build_app()
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert response.headers.get("access-control-allow-origin") == ALLOWED


def test_empty_cors_origins_blocks_all(monkeypatch) -> None:
    """If POND_CORS_ORIGINS is empty string, no origins should be allowed."""
    monkeypatch.setenv("POND_CORS_ORIGINS", "")
    from ponddb.app import build_app

    app = build_app()
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert "access-control-allow-origin" not in response.headers


def test_unset_cors_origins_blocks_all(app_no_cors) -> None:
    """If POND_CORS_ORIGINS is not set, no origins should be allowed."""
    client = TestClient(app_no_cors, raise_server_exceptions=True)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# Preflight against /query (POST-only route) — allowed origin
# ---------------------------------------------------------------------------


def test_preflight_post_route_allowed_origin_returns_204(app_single) -> None:
    """Preflight for POST /query from allowed origin should return 204."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/query",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.status_code == 204


def test_preflight_post_route_allowed_origin_acao(app_single) -> None:
    """Preflight for POST /query from allowed origin should echo ACAO."""
    client = TestClient(app_single, raise_server_exceptions=True)
    response = client.options(
        "/query",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.headers.get("access-control-allow-origin") == ALLOWED
