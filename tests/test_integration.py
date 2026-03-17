"""End-to-end integration tests for PondDB.

7 scenarios that exercise the full API surface through real HTTP calls:

  1. New tenant onboarding — API key → JWT → authenticated query
  2. Full query lifecycle — session → upload → query → check history
  3. Query sharing workflow — save → share public → access via /q/{slug}
  4. Tenant isolation proof — Tenant A cannot see Tenant B's private data
  5. Cache behavior — verify HIT/MISS headers and cache invalidation
  6. Editor serves correctly — HTML content with CodeMirror
  7. Rate limiting and error handling — 429, 401, 400, 404 responses

All tests use FastAPI TestClient with a fresh app instance (in-memory SQLite).
"""

import importlib
import os

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_KEY = "integration-test-key-2026"
JWT_SECRET = "integration-jwt-secret"

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_setup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Set environment for a clean in-memory test instance."""
    monkeypatch.setenv("POND_API_KEY", API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("POND_SQLITE_PATH", str(tmp_path / "integration.db"))
    monkeypatch.setenv("POND_DATA_ROOT", str(tmp_path / "datasets"))
    os.makedirs(tmp_path / "datasets", exist_ok=True)


@pytest.fixture
def client(env_setup) -> TestClient:
    """Fresh TestClient with reloaded app module."""
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def session_id(client: TestClient) -> str:
    """Create a session and return its ID."""
    resp = client.post("/session")
    assert resp.status_code == 201
    return resp.json()["session_id"]


def _get_jwt(client: TestClient, tenant_id: str = "default") -> str:
    """Exchange API key for JWT access token."""
    resp = client.post(
        "/auth/token",
        json={"api_key": API_KEY, "tenant_id": tenant_id},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _jwt_headers(client: TestClient, tenant_id: str = "default") -> dict:
    """Return Authorization headers with a valid JWT."""
    token = _get_jwt(client, tenant_id)
    return {"Authorization": f"Bearer {token}"}


def _api_headers() -> dict:
    """Return API key headers."""
    return {"X-API-Key": API_KEY}


# ===========================================================================
# Scenario 1: New Tenant Onboarding
# ===========================================================================


class TestTenantOnboarding:
    """API key → JWT tokens → authenticated query execution."""

    def test_exchange_api_key_for_jwt(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/token",
            json={"api_key": API_KEY, "tenant_id": TENANT_A},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    def test_refresh_token_flow(self, client: TestClient, session_id: str) -> None:
        # Get initial tokens
        resp = client.post(
            "/auth/token",
            json={"api_key": API_KEY, "tenant_id": TENANT_A},
        )
        refresh = resp.json()["refresh_token"]

        # Refresh to get new access token
        resp2 = client.post("/auth/refresh", json={"refresh_token": refresh})
        assert resp2.status_code == 200
        new_access = resp2.json()["access_token"]
        assert resp2.json()["token_type"] == "bearer"

        # Verify the refreshed token works for authenticated requests
        resp3 = client.post(
            "/query",
            json={"session_id": session_id, "sql": "SELECT 1"},
            headers={"Authorization": f"Bearer {new_access}"},
        )
        assert resp3.status_code == 200

    def test_jwt_enables_query_execution(self, client: TestClient, session_id: str) -> None:
        headers = _jwt_headers(client, TENANT_A)
        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": "SELECT 42 AS answer"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == [[42]]
        assert data["columns"] == ["answer"]

    def test_invalid_api_key_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/auth/token",
            json={"api_key": "wrong-key"},
        )
        assert resp.status_code == 401


# ===========================================================================
# Scenario 2: Full Query Lifecycle
# ===========================================================================


class TestQueryLifecycle:
    """Session → create table → query → verify history."""

    def test_full_query_cycle(self, client: TestClient, session_id: str) -> None:
        headers = _jwt_headers(client)

        # Step 1: Create a table
        resp = client.post(
            "/query",
            json={
                "session_id": session_id,
                "sql": "CREATE TABLE products (id INT, name VARCHAR, price FLOAT)",
            },
            headers=headers,
        )
        assert resp.status_code == 200

        # Step 2: Insert data
        resp = client.post(
            "/query",
            json={
                "session_id": session_id,
                "sql": "INSERT INTO products VALUES (1, 'Widget', 9.99), (2, 'Gadget', 19.99)",
            },
            headers=headers,
        )
        assert resp.status_code == 200

        # Step 3: Query data
        resp = client.post(
            "/query",
            json={
                "session_id": session_id,
                "sql": "SELECT * FROM products ORDER BY id",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rowcount"] == 2
        assert data["columns"] == ["id", "name", "price"]
        row = data["rows"][0]
        assert row[0] == 1
        assert row[1] == "Widget"
        assert row[2] == pytest.approx(9.99, rel=1e-3)

        # Step 4: Verify history recorded
        resp = client.get("/history", headers=headers)
        assert resp.status_code == 200
        history = resp.json()
        assert len(history) >= 3  # CREATE, INSERT, SELECT
        # Most recent first
        sqls = [h["sql"] for h in history]
        assert any("SELECT" in s for s in sqls)
        assert any("INSERT" in s for s in sqls)
        assert any("CREATE" in s for s in sqls)

    def test_query_error_logged_in_history(self, client: TestClient, session_id: str) -> None:
        headers = _jwt_headers(client)
        # Invalid SQL
        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": "SELECT FROM nonexistent"},
            headers=headers,
        )
        assert resp.status_code == 400

        # Error should appear in history
        resp = client.get("/history?status=error", headers=headers)
        assert resp.status_code == 200
        errors = resp.json()
        assert len(errors) >= 1
        assert errors[0]["status"] == "error"


# ===========================================================================
# Scenario 3: Query Sharing Workflow
# ===========================================================================


class TestQuerySharing:
    """Save query → share publicly → access via /q/{slug}."""

    def test_save_and_share_public_query(self, client: TestClient) -> None:
        headers = _jwt_headers(client, TENANT_A)

        # Save a public query
        resp = client.post(
            "/queries",
            json={
                "title": "Integration Test Query",
                "sql": "SELECT 'hello' AS greeting",
                "visibility": "public",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        slug = resp.json()["slug"]
        assert slug == "integration-test-query"

        # Access via share link (no auth needed for public)
        resp = client.get(f"/q/{slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == [["hello"]]
        assert data["slug"] == slug

    def test_private_query_requires_auth(self, client: TestClient) -> None:
        headers = _jwt_headers(client, TENANT_A)

        # Save a private query
        resp = client.post(
            "/queries",
            json={
                "title": "Private Secret Query",
                "sql": "SELECT 'secret' AS data",
                "visibility": "private",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        slug = resp.json()["slug"]

        # Access without auth → 403
        resp = client.get(f"/q/{slug}")
        assert resp.status_code == 403

        # Access with API key → 200
        resp = client.get(f"/q/{slug}", headers=_api_headers())
        assert resp.status_code == 200
        assert resp.json()["rows"] == [["secret"]]

    def test_list_queries_includes_public(self, client: TestClient) -> None:
        headers_a = _jwt_headers(client, TENANT_A)

        # Create one public and one private query
        client.post(
            "/queries",
            json={"title": "Public Listed", "sql": "SELECT 1", "visibility": "public"},
            headers=headers_a,
        )
        client.post(
            "/queries",
            json={"title": "Private Listed", "sql": "SELECT 2", "visibility": "private"},
            headers=headers_a,
        )

        # List as same tenant — see both
        resp = client.get("/queries", headers=headers_a)
        assert resp.status_code == 200
        slugs = [q["slug"] for q in resp.json()]
        assert "public-listed" in slugs
        assert "private-listed" in slugs


# ===========================================================================
# Scenario 4: Tenant Isolation Proof
# ===========================================================================


class TestTenantIsolation:
    """Tenant A's private data is invisible to Tenant B."""

    def test_private_queries_isolated(self, client: TestClient) -> None:
        headers_a = _jwt_headers(client, TENANT_A)
        headers_b = _jwt_headers(client, TENANT_B)

        # Tenant A saves a private query
        resp = client.post(
            "/queries",
            json={
                "title": "Alpha Secret",
                "sql": "SELECT 'alpha-only'",
                "visibility": "private",
            },
            headers=headers_a,
        )
        assert resp.status_code == 201

        # Tenant B cannot see it in list
        resp = client.get("/queries", headers=headers_b)
        slugs = [q["slug"] for q in resp.json()]
        assert "alpha-secret" not in slugs

        # Tenant B cannot access by slug
        resp = client.get("/queries/alpha-secret", headers=headers_b)
        assert resp.status_code in (403, 404)

    def test_public_queries_visible_cross_tenant(self, client: TestClient) -> None:
        headers_a = _jwt_headers(client, TENANT_A)
        headers_b = _jwt_headers(client, TENANT_B)

        # Tenant A saves a public query
        client.post(
            "/queries",
            json={
                "title": "Alpha Public Data",
                "sql": "SELECT 'open-data'",
                "visibility": "public",
            },
            headers=headers_a,
        )

        # Tenant B can see it
        resp = client.get("/queries", headers=headers_b)
        slugs = [q["slug"] for q in resp.json()]
        assert "alpha-public-data" in slugs

    def test_query_history_isolated(self, client: TestClient, session_id: str) -> None:
        headers_a = _jwt_headers(client, TENANT_A)
        headers_b = _jwt_headers(client, TENANT_B)

        # Tenant A runs a query
        client.post(
            "/query",
            json={"session_id": session_id, "sql": "SELECT 'alpha-query'"},
            headers=headers_a,
        )

        # Tenant B's history should NOT contain A's query
        resp = client.get("/history", headers=headers_b)
        sqls = [h["sql"] for h in resp.json()]
        assert "SELECT 'alpha-query'" not in sqls

        # Tenant A's history SHOULD contain it
        resp = client.get("/history", headers=headers_a)
        sqls = [h["sql"] for h in resp.json()]
        assert "SELECT 'alpha-query'" in sqls


# ===========================================================================
# Scenario 5: Cache Behavior
# ===========================================================================


class TestCacheBehavior:
    """Verify cache HIT/MISS headers and write-invalidation."""

    def test_cache_miss_then_hit(self, client: TestClient, session_id: str) -> None:
        headers = _jwt_headers(client)
        payload = {"session_id": session_id, "sql": "SELECT 1 + 1 AS result"}

        # First call → MISS
        resp1 = client.post("/query", json=payload, headers=headers)
        assert resp1.status_code == 200
        assert resp1.headers.get("X-Cache") == "MISS"

        # Second call (same SQL) → HIT
        resp2 = client.post("/query", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.headers.get("X-Cache") == "HIT"
        assert resp2.json()["rows"] == [[2]]

    def test_write_invalidates_cache(self, client: TestClient, session_id: str) -> None:
        headers = _jwt_headers(client)

        # Create table and populate
        client.post(
            "/query",
            json={"session_id": session_id, "sql": "CREATE TABLE cache_test (v INT)"},
            headers=headers,
        )
        client.post(
            "/query",
            json={"session_id": session_id, "sql": "INSERT INTO cache_test VALUES (1)"},
            headers=headers,
        )

        # Read → MISS, cached
        select = {"session_id": session_id, "sql": "SELECT * FROM cache_test"}
        resp1 = client.post("/query", json=select, headers=headers)
        assert resp1.headers.get("X-Cache") == "MISS"

        # Read again → HIT
        resp2 = client.post("/query", json=select, headers=headers)
        assert resp2.headers.get("X-Cache") == "HIT"

        # Write operation bumps version
        client.post(
            "/query",
            json={"session_id": session_id, "sql": "INSERT INTO cache_test VALUES (2)"},
            headers=headers,
        )

        # Next read → MISS (cache key changed due to version bump)
        resp3 = client.post("/query", json=select, headers=headers)
        assert resp3.headers.get("X-Cache") == "MISS"
        assert resp3.json()["rowcount"] == 2


# ===========================================================================
# Scenario 6: Editor Serves Correctly
# ===========================================================================


class TestEditorEndpoint:
    """GET /editor returns valid HTML with CodeMirror."""

    def test_editor_returns_html(self, client: TestClient) -> None:
        resp = client.get("/editor")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        html = resp.text
        assert "<html" in html.lower() or "<!doctype" in html.lower()

    def test_editor_contains_codemirror(self, client: TestClient) -> None:
        resp = client.get("/editor")
        html = resp.text
        # CodeMirror 6 should be loaded from a CDN
        assert "codemirror" in html.lower()

    def test_editor_has_run_button(self, client: TestClient) -> None:
        resp = client.get("/editor")
        html = resp.text.lower()
        # Should have some form of run/execute button
        assert "run" in html or "execute" in html


# ===========================================================================
# Scenario 7: Rate Limiting and Error Handling
# ===========================================================================


class TestRateLimitingAndErrors:
    """Verify rate limiting, auth errors, and bad request handling."""

    def test_unauthenticated_query_rejected(self, client: TestClient, session_id: str) -> None:
        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": "SELECT 1"},
        )
        assert resp.status_code == 401

    def test_empty_sql_rejected(self, client: TestClient, session_id: str) -> None:
        headers = _jwt_headers(client)
        resp = client.post(
            "/query",
            json={"session_id": session_id, "sql": ""},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_invalid_session_rejected(self, client: TestClient) -> None:
        headers = _jwt_headers(client)
        resp = client.post(
            "/query",
            json={"session_id": "nonexistent-session", "sql": "SELECT 1"},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_share_link_rate_limiting(self, client: TestClient) -> None:
        headers = _jwt_headers(client, TENANT_A)

        # Create a public query to test rate limiting on
        resp = client.post(
            "/queries",
            json={
                "title": "Rate Limit Test",
                "sql": "SELECT 'limited'",
                "visibility": "public",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        slug = resp.json()["slug"]

        # Send requests up to the limit (rate limiter allows < RATE_LIMIT)
        success_count = 0
        for i in range(15):
            r = client.get(f"/q/{slug}")
            if r.status_code == 200:
                success_count += 1
            elif r.status_code == 429:
                break

        # Should have succeeded some requests and eventually hit 429
        assert success_count >= 1, "Should allow at least some requests"
        assert r.status_code == 429, "Should eventually be rate limited"

    def test_nonexistent_share_slug_404(self, client: TestClient) -> None:
        resp = client.get("/q/this-slug-does-not-exist")
        assert resp.status_code == 404

    def test_duplicate_query_title_409(self, client: TestClient) -> None:
        headers = _jwt_headers(client, TENANT_A)
        payload = {
            "title": "Duplicate Title",
            "sql": "SELECT 1",
            "visibility": "public",
        }
        resp1 = client.post("/queries", json=payload, headers=headers)
        assert resp1.status_code == 201

        resp2 = client.post("/queries", json=payload, headers=headers)
        assert resp2.status_code == 409

    def test_health_endpoint(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "sessions" in data
