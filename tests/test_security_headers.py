"""Tests for SecurityHeadersMiddleware — all 7 headers, dev/prod mode, every response."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers: build minimal test apps with the middleware in dev vs prod mode
# ---------------------------------------------------------------------------


def make_app(dev_mode: bool) -> FastAPI:
    from ponddb.security.security_headers import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, dev_mode=dev_mode)

    @app.get("/ok")
    def ok():
        return {"status": "ok"}

    @app.post("/echo")
    def echo(body: dict = None):
        return body or {}

    @app.get("/error")
    def error():
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="bad request")

    @app.get("/server-error")
    def server_error():
        raise RuntimeError("boom")

    return app


@pytest.fixture(scope="module")
def prod_client():
    return TestClient(make_app(dev_mode=False), raise_server_exceptions=False)


@pytest.fixture(scope="module")
def dev_client():
    return TestClient(make_app(dev_mode=True), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# The 7 security header names (lowercase for case-insensitive comparison)
# ---------------------------------------------------------------------------

NON_HSTS_HEADERS = [
    "x-content-type-options",
    "x-frame-options",
    "x-xss-protection",
    "content-security-policy",
    "referrer-policy",
    "permissions-policy",
]
HSTS_HEADER = "strict-transport-security"
ALL_HEADERS = NON_HSTS_HEADERS + [HSTS_HEADER]


# ---------------------------------------------------------------------------
# Production mode — all 7 headers on every response
# ---------------------------------------------------------------------------


class TestProdModeHeaders:
    def test_all_headers_present_on_200(self, prod_client):
        resp = prod_client.get("/ok")
        assert resp.status_code == 200
        for h in ALL_HEADERS:
            assert h in resp.headers, f"Missing header in 200: {h}"

    def test_all_headers_present_on_post(self, prod_client):
        resp = prod_client.post("/echo", json={"key": "value"})
        assert resp.status_code == 200
        for h in ALL_HEADERS:
            assert h in resp.headers, f"Missing header in POST 200: {h}"

    def test_all_headers_present_on_400(self, prod_client):
        resp = prod_client.get("/error")
        assert resp.status_code == 400
        for h in ALL_HEADERS:
            assert h in resp.headers, f"Missing header in 400: {h}"

    def test_all_headers_present_on_500(self, prod_client):
        resp = prod_client.get("/server-error")
        assert resp.status_code == 500
        for h in ALL_HEADERS:
            assert h in resp.headers, f"Missing header in 500: {h}"

    def test_all_headers_present_on_404(self, prod_client):
        resp = prod_client.get("/nonexistent-path")
        assert resp.status_code == 404
        for h in ALL_HEADERS:
            assert h in resp.headers, f"Missing header in 404: {h}"


# ---------------------------------------------------------------------------
# Development mode — 6 headers, NO HSTS
# ---------------------------------------------------------------------------


class TestDevModeHeaders:
    def test_non_hsts_headers_present_on_200(self, dev_client):
        resp = dev_client.get("/ok")
        assert resp.status_code == 200
        for h in NON_HSTS_HEADERS:
            assert h in resp.headers, f"Missing header in dev 200: {h}"

    def test_hsts_absent_in_dev_on_200(self, dev_client):
        resp = dev_client.get("/ok")
        assert HSTS_HEADER not in resp.headers, "HSTS must NOT be set in dev mode"

    def test_hsts_absent_in_dev_on_400(self, dev_client):
        resp = dev_client.get("/error")
        assert resp.status_code == 400
        assert HSTS_HEADER not in resp.headers, (
            "HSTS must NOT be set in dev mode on error responses"
        )

    def test_hsts_absent_in_dev_on_404(self, dev_client):
        resp = dev_client.get("/nonexistent-path")
        assert resp.status_code == 404
        assert HSTS_HEADER not in resp.headers, "HSTS must NOT be set in dev mode on 404"

    def test_non_hsts_headers_present_on_400(self, dev_client):
        resp = dev_client.get("/error")
        for h in NON_HSTS_HEADERS:
            assert h in resp.headers, f"Missing header in dev 400: {h}"

    def test_non_hsts_headers_present_on_404(self, dev_client):
        resp = dev_client.get("/nonexistent-path")
        for h in NON_HSTS_HEADERS:
            assert h in resp.headers, f"Missing header in dev 404: {h}"


# ---------------------------------------------------------------------------
# Header value correctness
# ---------------------------------------------------------------------------


class TestHeaderValues:
    def test_x_content_type_options_nosniff(self, prod_client):
        resp = prod_client.get("/ok")
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_x_frame_options_deny_or_sameorigin(self, prod_client):
        resp = prod_client.get("/ok")
        val = resp.headers["x-frame-options"].upper()
        assert val in ("DENY", "SAMEORIGIN"), f"Unexpected X-Frame-Options: {val}"

    def test_referrer_policy_not_empty(self, prod_client):
        resp = prod_client.get("/ok")
        assert resp.headers["referrer-policy"].strip() != ""

    def test_content_security_policy_not_empty(self, prod_client):
        resp = prod_client.get("/ok")
        assert resp.headers["content-security-policy"].strip() != ""

    def test_hsts_includes_max_age_in_prod(self, prod_client):
        resp = prod_client.get("/ok")
        hsts = resp.headers[HSTS_HEADER]
        assert "max-age=" in hsts, f"HSTS missing max-age: {hsts}"

    def test_hsts_max_age_at_least_one_year(self, prod_client):
        resp = prod_client.get("/ok")
        hsts = resp.headers[HSTS_HEADER]
        for part in hsts.split(";"):
            part = part.strip()
            if part.startswith("max-age="):
                age = int(part.split("=", 1)[1])
                assert age >= 31536000, f"HSTS max-age too short: {age}"
                break
        else:
            pytest.fail(f"max-age not found in HSTS header: {hsts}")

    def test_permissions_policy_not_empty(self, prod_client):
        resp = prod_client.get("/ok")
        assert resp.headers["permissions-policy"].strip() != ""

    def test_x_xss_protection_not_empty(self, prod_client):
        resp = prod_client.get("/ok")
        assert resp.headers["x-xss-protection"].strip() != ""


# ---------------------------------------------------------------------------
# Default mode (no dev_mode arg) should behave as prod (HSTS present)
# ---------------------------------------------------------------------------


class TestDefaultMode:
    def test_default_is_prod_mode(self):
        from ponddb.security.security_headers import SecurityHeadersMiddleware

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)  # no dev_mode arg

        @app.get("/ping")
        def ping():
            return {"ping": "pong"}

        client = TestClient(app)
        resp = client.get("/ping")
        for h in ALL_HEADERS:
            assert h in resp.headers, f"Default mode missing header: {h}"

    def test_default_has_hsts(self):
        from ponddb.security.security_headers import SecurityHeadersMiddleware

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/ping")
        def ping():
            return {}

        client = TestClient(app)
        resp = client.get("/ping")
        assert HSTS_HEADER in resp.headers


# ---------------------------------------------------------------------------
# Integration: middleware wired into the real ponddb app
# ---------------------------------------------------------------------------


class TestAppIntegration:
    """Verify security_headers.py is imported and wired into app.py."""

    def test_security_headers_importable_from_ponddb(self):
        from ponddb.security import security_headers  # noqa: F401

        assert hasattr(security_headers, "SecurityHeadersMiddleware")

    def test_app_has_security_headers_middleware(self):
        """The real app must include SecurityHeadersMiddleware in its middleware stack."""
        from ponddb.app import app

        middleware_types = [m.cls.__name__ for m in app.user_middleware if hasattr(m, "cls")]
        assert "SecurityHeadersMiddleware" in middleware_types, (
            f"SecurityHeadersMiddleware not found in app middleware: {middleware_types}"
        )

    def test_real_app_health_has_security_headers(self):
        """GET /health on the real app must have all non-HSTS headers."""
        from ponddb.app import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        for h in NON_HSTS_HEADERS:
            assert h in resp.headers, f"Real app /health missing header: {h}"
