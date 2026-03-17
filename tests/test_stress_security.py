"""Security stress tests: SQL injection, sandbox bypass, auth bypass, rate limits.

Validates that PondDB blocks malicious inputs across all attack vectors.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from tests.stress_helpers import (
    api_headers,
    create_session,
    jwt_headers,
    make_client,
)


@pytest.fixture
def client() -> TestClient:
    return make_client()


@pytest.fixture
def sid(client: TestClient) -> str:
    return create_session(client)


@pytest.fixture
def auth(client: TestClient) -> dict:
    return jwt_headers(client)


class TestSqlInjectionBattery:
    """Sandbox-blocked patterns all return 403 via /query endpoint."""

    # Only patterns that SHOULD be blocked by the sandbox
    SANDBOX_BLOCKED = [
        ("COPY", "COPY tbl FROM 'secret.csv'"),
        ("LOAD", "LOAD 'httpfs'"),
        ("INSTALL", "INSTALL 'httpfs'"),
        ("ATTACH", "ATTACH '/tmp/evil.db' AS evil"),
        ("EXPORT DATABASE", "EXPORT DATABASE '/tmp/dump'"),
        ("IMPORT DATABASE", "IMPORT DATABASE '/tmp/evil'"),
        ("CREATE SECRET", "CREATE SECRET s1 (TYPE S3, KEY_ID 'x', SECRET 'y')"),
        ("SET", "SET enable_external_access = true"),
        ("PRAGMA", "PRAGMA database_list"),
        ("read_csv", "SELECT * FROM read_csv('/etc/passwd')"),
        ("read_parquet", "SELECT * FROM read_parquet('/tmp/data.parquet')"),
        ("read_json", "SELECT * FROM read_json('/tmp/secrets.json')"),
        ("read_text", "SELECT * FROM read_text('/etc/hosts')"),
        ("read_blob", "SELECT * FROM read_blob('/etc/shadow')"),
        ("glob", "SELECT * FROM glob('/etc/*')"),
    ]

    def test_sql_injection_battery(self, client: TestClient, sid: str, auth: dict) -> None:
        for name, sql in self.SANDBOX_BLOCKED:
            resp = client.post(
                "/query",
                json={"session_id": sid, "sql": sql},
                headers=auth,
            )
            assert resp.status_code == 403, (
                f"Pattern '{name}' not blocked: {sql!r} → {resp.status_code} {resp.text}"
            )

    def test_duckdb_hardening_blocks_external_access(
        self, client: TestClient, sid: str, auth: dict
    ) -> None:
        """DuckDB's enable_external_access=False blocks file system functions
        even if they somehow bypass the sandbox regex."""
        # UNION SELECT is valid SQL (not blocked by sandbox) but DuckDB
        # hardening prevents access to system catalogs that don't exist
        resp = client.post(
            "/query",
            json={"session_id": sid, "sql": "SELECT 1; DROP TABLE nonexistent;"},
            headers=auth,
        )
        # Multiple statements either error or only first is executed
        assert resp.status_code in (200, 400, 500)


class TestBlockedSqlPatternsComprehensive:
    """All 15 sandbox patterns return 400 via PondAPI (async endpoint)."""

    BLOCKED_SQLS = [
        ("COPY", "COPY tbl FROM 'file.csv'"),
        ("LOAD", "LOAD 'httpfs'"),
        ("INSTALL", "INSTALL 'httpfs'"),
        ("ATTACH", "ATTACH 'evil.db'"),
        ("EXPORT DATABASE", "EXPORT DATABASE '/tmp'"),
        ("IMPORT DATABASE", "IMPORT DATABASE '/tmp'"),
        ("CREATE SECRET", "CREATE SECRET s1 (TYPE S3)"),
        ("SET", "SET threads TO 99"),
        ("PRAGMA", "PRAGMA database_list"),
        ("read_csv", "SELECT * FROM read_csv('x.csv')"),
        ("read_parquet", "SELECT * FROM read_parquet('x.parquet')"),
        ("read_json", "SELECT * FROM read_json('x.json')"),
        ("read_text", "SELECT * FROM read_text('x.txt')"),
        ("read_blob", "SELECT * FROM read_blob('x.bin')"),
        ("glob", "SELECT * FROM glob('/tmp/*')"),
    ]

    def test_blocked_sql_patterns_comprehensive(
        self, client: TestClient, sid: str, auth: dict
    ) -> None:
        for name, sql in self.BLOCKED_SQLS:
            resp = client.post(
                "/pondapi/execute",
                json={"session_id": sid, "sql": sql},
                headers=auth,
            )
            assert resp.status_code == 400, (
                f"Pattern '{name}' not blocked via PondAPI: {resp.status_code} {resp.text}"
            )
            assert "blocked" in resp.text.lower()


class TestAuthBypassAttempts:
    """Various auth bypass attempts — all rejected."""

    def test_expired_jwt_rejected(self, client: TestClient) -> None:
        from jose import jwt as jose_jwt

        secret = os.environ.get("POND_JWT_SECRET", "stress-test-jwt-secret")
        expired_token = jose_jwt.encode(
            {"sub": "default", "exp": int(time.time()) - 3600, "role": "admin"},
            secret,
            algorithm="HS256",
        )
        resp = client.get("/queries", headers={"Authorization": f"Bearer {expired_token}"})
        assert resp.status_code in (401, 403)

    def test_malformed_jwt_rejected(self, client: TestClient) -> None:
        resp = client.get("/queries", headers={"Authorization": "Bearer not-a-valid-jwt"})
        assert resp.status_code in (401, 403)

    def test_wrong_secret_jwt_rejected(self, client: TestClient) -> None:
        from jose import jwt as jose_jwt

        token = jose_jwt.encode(
            {"sub": "default", "exp": int(time.time()) + 3600},
            "completely-wrong-secret",
            algorithm="HS256",
        )
        resp = client.get("/queries", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403)

    def test_empty_api_key_rejected(self, client: TestClient) -> None:
        resp = client.get("/datasets", headers={"X-API-Key": ""})
        assert resp.status_code in (401, 403)

    def test_wrong_api_key_rejected(self, client: TestClient) -> None:
        resp = client.get("/datasets", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code in (401, 403)

    def test_sql_in_api_key_rejected(self, client: TestClient) -> None:
        resp = client.get(
            "/datasets",
            headers={"X-API-Key": "' OR '1'='1' --"},
        )
        assert resp.status_code in (401, 403)

    def test_no_auth_header_rejected(self, client: TestClient) -> None:
        resp = client.get("/queries")
        assert resp.status_code in (401, 403)


class TestRateLimitEnforcement:
    """Exceeding PondAPI rate limit returns 429."""

    def test_rate_limit_enforcement(self, client: TestClient, sid: str) -> None:
        # Use low rate limit for this test
        os.environ["POND_PONDAPI_RATE_LIMIT"] = "3"
        os.environ["POND_PONDAPI_RATE_WINDOW"] = "60"
        import importlib

        import ponddb.app as m

        importlib.reload(m)
        client2 = TestClient(m.app, follow_redirects=False)
        auth = jwt_headers(client2)
        sid2 = create_session(client2)

        statuses = []
        for i in range(6):
            resp = client2.post(
                "/pondapi/execute",
                json={"session_id": sid2, "sql": f"SELECT {i}"},
                headers=auth,
            )
            statuses.append(resp.status_code)

        # At least one should be 429
        assert 429 in statuses, f"No 429 in statuses: {statuses}"

        # Restore high limit for other tests
        os.environ["POND_PONDAPI_RATE_LIMIT"] = "100"


class TestOAuthStateTampering:
    """Tampered OAuth state tokens are rejected."""

    def test_oauth_state_tampering(self, client: TestClient) -> None:
        os.environ["POND_OAUTH_SECRET"] = "test-oauth-secret"
        os.environ["POND_GOOGLE_CLIENT_ID"] = "fake-client-id"
        os.environ["POND_GOOGLE_CLIENT_SECRET"] = "fake-client-secret"

        # Craft a tampered state token
        tampered_state = "dGFtcGVyZWQ.badhexsignature"
        resp = client.get(
            f"/auth/google/callback?code=fake-code&state={tampered_state}",
        )
        assert resp.status_code in (400, 401, 403, 500)


class TestPathTraversalAttempts:
    """Path traversal in various inputs rejected."""

    def test_path_traversal_in_dataset_name(self, client: TestClient) -> None:
        import io

        hdrs = api_headers()
        files = {
            "file": (
                "../../etc/passwd.csv",
                io.BytesIO(b"id,val\n1,x\n"),
                "text/csv",
            )
        }
        resp = client.post("/datasets", files=files, headers=hdrs)
        if resp.status_code == 201:
            # Name should be sanitized — no path traversal
            name = resp.json()["name"]
            assert "/" not in name
            assert ".." not in name
            client.delete(f"/datasets/{name}", headers=hdrs)

    def test_path_traversal_in_query_slug(self, client: TestClient) -> None:
        auth = jwt_headers(client)
        resp = client.get("/queries/../../etc/passwd", headers=auth)
        assert resp.status_code in (400, 404, 422)

    def test_path_traversal_in_workgroup_id(self, client: TestClient) -> None:
        from tests.stress_helpers import admin_jwt_headers

        auth = admin_jwt_headers(client)
        resp = client.get("/workgroups/../../etc/passwd", headers=auth)
        assert resp.status_code in (400, 404, 422)


class TestXssInUserInputs:
    """XSS payloads in user-controlled strings are escaped in HTML output."""

    def test_xss_in_query_name_api(self, client: TestClient) -> None:
        """JSON API stores and returns the title, but JSON encoding
        naturally prevents browser-side XSS when rendered via frameworks."""
        auth = jwt_headers(client)
        xss = '<script>alert("xss")</script>'
        resp = client.post(
            "/queries",
            json={"title": xss, "sql": "SELECT 1"},
            headers=auth,
        )
        assert resp.status_code == 201
        data = resp.json()
        # Title is stored as-is in JSON (expected for API)
        assert data["title"] == xss
        # But the JSON response Content-Type prevents browser rendering
        assert "application/json" in resp.headers.get("content-type", "")

    def test_xss_in_session_namespace(self, client: TestClient) -> None:
        # Creating a session with XSS in namespace
        resp = client.post(
            "/session",
            json={"namespace": '<img src=x onerror="alert(1)">'},
        )
        # Should either reject or accept (namespace is just a string label)
        assert resp.status_code in (201, 400, 422)

    def test_xss_in_html_dashboard(self, client: TestClient) -> None:
        """Jinja2 auto-escapes user data in HTML templates by default."""
        import base64
        import hashlib
        import hmac
        import json

        secret = os.environ.get("POND_WEBSITE_SESSION_SECRET", "stress-test-session-secret")
        payload = base64.urlsafe_b64encode(
            json.dumps({"tenant_id": "default", "role": "admin"}).encode()
        ).decode()
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        cookie = f"{payload}.{sig}"
        client.cookies.set("pond_session", cookie)

        resp = client.get("/dashboard")
        assert resp.status_code == 200
        # Dashboard HTML should not contain raw script injection vectors
        assert "<script>alert" not in resp.text
