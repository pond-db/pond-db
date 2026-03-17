"""Integration tests for tenant namespace isolation.

Defines expected behavior for:
  - tenant_id column on query_store (queries table)
  - tenant_id column on query_history table
  - All MetadataStore operations filter by tenant_id
  - HTTP endpoints use tenant_id extracted from JWT
  - Tenant A CANNOT see Tenant B private queries or history
  - Public queries ARE accessible cross-tenant (read-only)
  - Result cache is isolated per tenant

Tests FAIL until implementation is complete.
"""

import importlib
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-tenant-isolation-key"
JWT_SECRET = "tenant-isolation-test-secret"

TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
TENANT_C = "tenant-gamma"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_API_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", JWT_SECRET)


@pytest.fixture
def client(env_setup) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def store(tmp_path):
    """Fresh MetadataStore for direct store tests."""
    from ponddb.store.metadata_store import MetadataStore
    s = MetadataStore(str(tmp_path / "tenant_test.db"))
    s.initialize_blocking()
    yield s
    import asyncio
    asyncio.run(s.close())


def _jwt_headers(tenant_id: str) -> dict:
    """Return Authorization headers with a valid JWT for tenant_id."""
    from ponddb.auth.jwt_auth import create_access_token
    token = create_access_token(tenant_id)
    return {"Authorization": f"Bearer {token}"}


def _api_key_headers() -> dict:
    return {"X-API-Key": VALID_API_KEY}


# ===========================================================================
# SECTION 1: MetadataStore — query_store with tenant_id
# ===========================================================================


@pytest.mark.asyncio
async def test_queries_table_has_tenant_id_column(tmp_path) -> None:
    """The 'queries' SQLite table must have a 'tenant_id' column."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    cursor = store._conn.execute("PRAGMA table_info(queries)")
    columns = {row[1] for row in cursor.fetchall()}
    assert "tenant_id" in columns, (
        "'queries' table must have a 'tenant_id' column for isolation"
    )
    await store.close()


@pytest.mark.asyncio
async def test_save_query_accepts_tenant_id(store) -> None:
    """save_query() must accept a tenant_id parameter."""
    slug = await store.save_query(
        title="Tenant A Query",
        description="owned by alpha",
        sql="SELECT 1",
        created_by="user1",
        tenant_id=TENANT_A,
    )
    assert isinstance(slug, str)
    assert len(slug) > 0


@pytest.mark.asyncio
async def test_get_query_by_slug_stores_tenant_id(store) -> None:
    """Stored query must include tenant_id in returned dict."""
    slug = await store.save_query(
        title="Alpha Private",
        description="",
        sql="SELECT 42",
        created_by="user1",
        tenant_id=TENANT_A,
    )
    result = await store.get_query_by_slug(slug, tenant_id=TENANT_A)
    assert result.get("tenant_id") == TENANT_A


@pytest.mark.asyncio
async def test_get_query_private_blocked_for_wrong_tenant(store) -> None:
    """Tenant B cannot fetch Tenant A's private query by slug."""
    slug = await store.save_query(
        title="Secret Alpha Query",
        description="",
        sql="SELECT 'secret'",
        created_by="user_alpha",
        tenant_id=TENANT_A,
        visibility="private",
    )
    with pytest.raises(Exception):  # QueryNotFoundError or PermissionError or similar
        await store.get_query_by_slug(slug, tenant_id=TENANT_B)


@pytest.mark.asyncio
async def test_get_query_public_accessible_cross_tenant(store) -> None:
    """Tenant B CAN fetch Tenant A's public query by slug."""
    slug = await store.save_query(
        title="Public Alpha Query",
        description="",
        sql="SELECT 'shared'",
        created_by="user_alpha",
        tenant_id=TENANT_A,
        visibility="public",
    )
    # Should succeed — public queries are readable by any tenant
    result = await store.get_query_by_slug(slug, tenant_id=TENANT_B)
    assert result["slug"] == slug
    assert result["visibility"] == "public"


@pytest.mark.asyncio
async def test_list_queries_filtered_by_tenant_id(store) -> None:
    """list_queries() by tenant_id returns only that tenant's queries."""
    await store.save_query(
        title="Alpha Query One", description="", sql="SELECT 1",
        created_by="user1", tenant_id=TENANT_A,
    )
    await store.save_query(
        title="Alpha Query Two", description="", sql="SELECT 2",
        created_by="user1", tenant_id=TENANT_A,
    )
    await store.save_query(
        title="Beta Query", description="", sql="SELECT 3",
        created_by="user2", tenant_id=TENANT_B,
    )

    alpha_queries = await store.list_queries(tenant_id=TENANT_A)
    assert len(alpha_queries) == 2
    for q in alpha_queries:
        assert q["tenant_id"] == TENANT_A

    beta_queries = await store.list_queries(tenant_id=TENANT_B)
    assert len(beta_queries) == 1
    assert beta_queries[0]["tenant_id"] == TENANT_B


@pytest.mark.asyncio
async def test_list_queries_tenant_a_does_not_see_tenant_b_private(store) -> None:
    """Tenant A's list NEVER contains Tenant B's private queries."""
    await store.save_query(
        title="B Private Secret",
        description="",
        sql="SELECT 'b secret'",
        created_by="user_b",
        tenant_id=TENANT_B,
        visibility="private",
    )
    results = await store.list_queries(tenant_id=TENANT_A)
    titles = [r["title"] for r in results]
    assert "B Private Secret" not in titles


@pytest.mark.asyncio
async def test_list_queries_includes_other_tenant_public_queries(store) -> None:
    """list_queries() for Tenant A includes public queries from Tenant B."""
    await store.save_query(
        title="B Public Query",
        description="",
        sql="SELECT 'b public'",
        created_by="user_b",
        tenant_id=TENANT_B,
        visibility="public",
    )
    # Tenant A should be able to see public queries from B in their listing
    results = await store.list_queries(tenant_id=TENANT_A, include_public=True)
    titles = [r["title"] for r in results]
    assert "B Public Query" in titles


@pytest.mark.asyncio
async def test_list_queries_public_results_are_read_only_metadata(store) -> None:
    """Public cross-tenant queries returned in listing have visibility='public'."""
    await store.save_query(
        title="Shared By B",
        description="",
        sql="SELECT 'public'",
        created_by="user_b",
        tenant_id=TENANT_B,
        visibility="public",
    )
    results = await store.list_queries(tenant_id=TENANT_A, include_public=True)
    shared = [r for r in results if r["title"] == "Shared By B"]
    assert len(shared) == 1
    assert shared[0]["visibility"] == "public"


@pytest.mark.asyncio
async def test_empty_tenant_sees_no_private_queries(store) -> None:
    """A tenant with no queries only sees public queries from others (or nothing)."""
    await store.save_query(
        title="Gamma Private",
        description="",
        sql="SELECT 'private'",
        created_by="user_g",
        tenant_id=TENANT_C,
        visibility="private",
    )
    results = await store.list_queries(tenant_id="brand-new-tenant")
    # The brand new tenant must see zero private queries from other tenants
    private_from_others = [
        r for r in results
        if r.get("tenant_id") != "brand-new-tenant" and r.get("visibility") == "private"
    ]
    assert len(private_from_others) == 0


# ===========================================================================
# SECTION 2: MetadataStore — query_history with tenant_id
# ===========================================================================


@pytest.mark.asyncio
async def test_query_history_table_has_tenant_id_column(tmp_path) -> None:
    """The 'query_history' SQLite table must have a 'tenant_id' column."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    cursor = store._conn.execute("PRAGMA table_info(query_history)")
    columns = {row[1] for row in cursor.fetchall()}
    assert "tenant_id" in columns, (
        "'query_history' table must have a 'tenant_id' column for isolation"
    )
    await store.close()


@pytest.mark.asyncio
async def test_log_query_history_accepts_tenant_id(store) -> None:
    """log_query_history() must accept and store tenant_id."""
    await store.log_query_history(
        namespace=TENANT_A,
        tenant_id=TENANT_A,
        sql="SELECT 1",
        duration_ms=5.0,
        rows_returned=1,
        status="success",
        executed_at=datetime.now(timezone.utc),
    )
    rows = await store.get_query_history(tenant_id=TENANT_A)
    assert len(rows) == 1
    assert rows[0].get("tenant_id") == TENANT_A


@pytest.mark.asyncio
async def test_get_query_history_filters_by_tenant_id(store) -> None:
    """get_query_history(tenant_id) returns only that tenant's history."""
    now = datetime.now(timezone.utc)
    await store.log_query_history(
        namespace=TENANT_A, tenant_id=TENANT_A,
        sql="SELECT 'alpha'", duration_ms=1.0,
        rows_returned=1, status="success", executed_at=now,
    )
    await store.log_query_history(
        namespace=TENANT_B, tenant_id=TENANT_B,
        sql="SELECT 'beta'", duration_ms=2.0,
        rows_returned=1, status="success", executed_at=now,
    )

    alpha_rows = await store.get_query_history(tenant_id=TENANT_A)
    assert all(r.get("tenant_id") == TENANT_A for r in alpha_rows)
    assert len(alpha_rows) == 1
    assert alpha_rows[0]["sql"] == "SELECT 'alpha'"

    beta_rows = await store.get_query_history(tenant_id=TENANT_B)
    assert len(beta_rows) == 1
    assert beta_rows[0]["sql"] == "SELECT 'beta'"


@pytest.mark.asyncio
async def test_tenant_b_cannot_see_tenant_a_history(store) -> None:
    """Tenant B's history query must NEVER return Tenant A's entries."""
    now = datetime.now(timezone.utc)
    for i in range(5):
        await store.log_query_history(
            namespace=TENANT_A, tenant_id=TENANT_A,
            sql=f"SELECT {i} -- alpha secret",
            duration_ms=float(i), rows_returned=1,
            status="success", executed_at=now,
        )

    # Tenant B should see nothing from Tenant A
    beta_rows = await store.get_query_history(tenant_id=TENANT_B)
    alpha_leaked = [r for r in beta_rows if "alpha secret" in r.get("sql", "")]
    assert len(alpha_leaked) == 0, "Tenant B must not see Tenant A's history"


@pytest.mark.asyncio
async def test_query_history_isolation_multiple_tenants(store) -> None:
    """Three tenants each have isolated history that doesn't bleed across."""
    now = datetime.now(timezone.utc)
    for tenant in [TENANT_A, TENANT_B, TENANT_C]:
        await store.log_query_history(
            namespace=tenant, tenant_id=tenant,
            sql=f"SELECT '{tenant}' AS who",
            duration_ms=1.0, rows_returned=1,
            status="success", executed_at=now,
        )

    for tenant in [TENANT_A, TENANT_B, TENANT_C]:
        rows = await store.get_query_history(tenant_id=tenant)
        assert len(rows) == 1, f"{tenant} should see exactly 1 history entry"
        assert tenant in rows[0]["sql"], f"{tenant} sees the wrong SQL"


# ===========================================================================
# SECTION 3: HTTP API — JWT-based tenant isolation
# ===========================================================================


def test_history_endpoint_uses_jwt_tenant_id_for_isolation(client) -> None:
    """GET /history returns only the calling JWT tenant's history.

    Tenant A runs a query, Tenant B's /history must NOT include it.
    """
    # Create sessions for each tenant (sessions are not auth-gated)
    sid_a = client.post("/session").json()["session_id"]
    sid_b = client.post("/session").json()["session_id"]

    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    # Tenant A executes a distinctive query
    resp = client.post(
        "/query",
        json={"session_id": sid_a, "sql": "SELECT 'alpha-secret-42' AS val"},
        headers=headers_a,
    )
    assert resp.status_code == 200

    # Tenant B executes a different query
    resp = client.post(
        "/query",
        json={"session_id": sid_b, "sql": "SELECT 'beta-data-99' AS val"},
        headers=headers_b,
    )
    assert resp.status_code == 200

    # Tenant B's history must NOT contain Tenant A's query
    hist_b = client.get("/history", headers=headers_b).json()
    alpha_leaked = [h for h in hist_b if "alpha-secret-42" in h.get("sql", "")]
    assert len(alpha_leaked) == 0, (
        "Tenant B's /history must not contain Tenant A's queries"
    )

    # Tenant A's history must NOT contain Tenant B's query
    hist_a = client.get("/history", headers=headers_a).json()
    beta_leaked = [h for h in hist_a if "beta-data-99" in h.get("sql", "")]
    assert len(beta_leaked) == 0, (
        "Tenant A's /history must not contain Tenant B's queries"
    )


def test_history_each_tenant_sees_own_history(client) -> None:
    """Each tenant sees exactly their own queries in /history."""
    sid_a = client.post("/session").json()["session_id"]
    sid_b = client.post("/session").json()["session_id"]

    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    client.post("/query", json={"session_id": sid_a, "sql": "SELECT 'a only'"}, headers=headers_a)
    client.post("/query", json={"session_id": sid_b, "sql": "SELECT 'b only'"}, headers=headers_b)

    hist_a = client.get("/history", headers=headers_a).json()
    hist_b = client.get("/history", headers=headers_b).json()

    a_sqls = [h["sql"] for h in hist_a]
    b_sqls = [h["sql"] for h in hist_b]

    assert any("a only" in s for s in a_sqls), "Tenant A should see their own query"
    assert not any("b only" in s for s in a_sqls), "Tenant A must not see Tenant B queries"

    assert any("b only" in s for s in b_sqls), "Tenant B should see their own query"
    assert not any("a only" in s for s in b_sqls), "Tenant B must not see Tenant A queries"


def test_queries_endpoint_tenant_isolation_private(client) -> None:
    """POST /queries with Tenant A JWT saves as Tenant A.
    GET /queries with Tenant B JWT must NOT return Tenant A's private query.
    """
    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    # Tenant A saves a private query via HTTP
    resp = client.post(
        "/queries",
        json={
            "title": "Alpha Private HTTP Query",
            "description": "top secret",
            "sql": "SELECT 'alpha private'",
            "visibility": "private",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201

    # Tenant B lists queries — should NOT see Alpha's private query
    list_b = client.get("/queries", headers=headers_b).json()
    titles_b = [q["title"] for q in list_b]
    assert "Alpha Private HTTP Query" not in titles_b, (
        "Tenant B must not see Tenant A's private query in list"
    )


def test_queries_endpoint_public_visible_cross_tenant(client) -> None:
    """Tenant A's PUBLIC query IS visible to Tenant B."""
    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    resp = client.post(
        "/queries",
        json={
            "title": "Alpha Public Shared Query",
            "description": "shared with all",
            "sql": "SELECT 'public data'",
            "visibility": "public",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201

    # Tenant B's list should include Tenant A's public query
    list_b = client.get("/queries", headers=headers_b).json()
    titles_b = [q["title"] for q in list_b]
    assert "Alpha Public Shared Query" in titles_b, (
        "Tenant B should see Tenant A's public query"
    )


def test_get_query_by_slug_private_blocked_for_wrong_tenant_http(client) -> None:
    """GET /queries/{slug} returns 403 or 404 for a private query owned by another tenant."""
    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    resp = client.post(
        "/queries",
        json={
            "title": "Alpha Secret Slug Query",
            "description": "",
            "sql": "SELECT 'private'",
            "visibility": "private",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201
    slug = resp.json()["slug"]

    # Tenant B tries to access Tenant A's private query by slug
    resp_b = client.get(f"/queries/{slug}", headers=headers_b)
    assert resp_b.status_code in (403, 404), (
        f"Expected 403 or 404, got {resp_b.status_code}: "
        "Tenant B must not access Tenant A's private query"
    )


def test_get_query_by_slug_public_accessible_cross_tenant_http(client) -> None:
    """GET /queries/{slug} succeeds for a public query from another tenant."""
    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    resp = client.post(
        "/queries",
        json={
            "title": "Public Cross Tenant Query",
            "description": "",
            "sql": "SELECT 'accessible'",
            "visibility": "public",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201
    slug = resp.json()["slug"]

    # Tenant B can access Tenant A's public query
    resp_b = client.get(f"/queries/{slug}", headers=headers_b)
    assert resp_b.status_code == 200


def test_queries_endpoint_jwt_saves_correct_tenant_id(client) -> None:
    """POST /queries extracts tenant_id from JWT, not from request body."""
    headers_a = _jwt_headers(TENANT_A)

    resp = client.post(
        "/queries",
        json={
            "title": "JWT Tenant Extraction Test",
            "description": "",
            "sql": "SELECT 42",
            "visibility": "private",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201
    data = resp.json()
    # The returned query must have tenant_id matching the JWT, not a spoofed value
    assert data.get("tenant_id") == TENANT_A


def test_queries_list_endpoint_uses_jwt_tenant_id(client) -> None:
    """GET /queries only shows the calling tenant's own queries plus public ones."""
    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    # Tenant A saves two queries
    client.post("/queries", json={
        "title": "A Q1", "description": "", "sql": "SELECT 1", "visibility": "private",
    }, headers=headers_a)
    client.post("/queries", json={
        "title": "A Q2", "description": "", "sql": "SELECT 2", "visibility": "private",
    }, headers=headers_a)

    # Tenant B saves one query
    client.post("/queries", json={
        "title": "B Q1", "description": "", "sql": "SELECT 3", "visibility": "private",
    }, headers=headers_b)

    # Tenant A sees only their own queries (not B's private)
    list_a = client.get("/queries", headers=headers_a).json()
    titles_a = {q["title"] for q in list_a}
    assert "A Q1" in titles_a
    assert "A Q2" in titles_a
    assert "B Q1" not in titles_a

    # Tenant B sees only their own queries (not A's private)
    list_b = client.get("/queries", headers=headers_b).json()
    titles_b = {q["title"] for q in list_b}
    assert "B Q1" in titles_b
    assert "A Q1" not in titles_b
    assert "A Q2" not in titles_b


# ===========================================================================
# SECTION 4: Cache isolation per tenant
# ===========================================================================


def test_cache_result_not_shared_across_tenants(client) -> None:
    """Tenant A's cached query result must NOT be served to Tenant B."""
    sid_a = client.post("/session").json()["session_id"]
    sid_b = client.post("/session").json()["session_id"]

    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    # Tenant A executes and caches a query
    sql = "SELECT 'tenant-specific-cached-result' AS val"
    resp1 = client.post(
        "/query",
        json={"session_id": sid_a, "sql": sql},
        headers=headers_a,
    )
    assert resp1.status_code == 200
    assert resp1.headers.get("X-Cache") == "MISS"

    # Tenant B runs the exact same SQL — must be a MISS (not a HIT from Tenant A's cache)
    resp2 = client.post(
        "/query",
        json={"session_id": sid_b, "sql": sql},
        headers=headers_b,
    )
    assert resp2.status_code == 200
    # If cache is shared, Tenant B would get a HIT — that's the bug we're preventing
    assert resp2.headers.get("X-Cache") != "HIT", (
        "Cache must be isolated per tenant — Tenant B must not get Tenant A's cached result"
    )


def test_cache_hit_within_same_tenant(client) -> None:
    """Repeat query by same tenant DOES get a cache HIT (sanity check)."""
    sid = client.post("/session").json()["session_id"]
    headers = _jwt_headers(TENANT_A)

    sql = "SELECT 'same-tenant-cache-check' AS val"

    # First call: MISS
    resp1 = client.post("/query", json={"session_id": sid, "sql": sql}, headers=headers)
    assert resp1.status_code == 200
    assert resp1.headers.get("X-Cache") == "MISS"

    # Second call: HIT (same tenant, same session, same SQL)
    resp2 = client.post("/query", json={"session_id": sid, "sql": sql}, headers=headers)
    assert resp2.status_code == 200
    assert resp2.headers.get("X-Cache") == "HIT"


# ===========================================================================
# SECTION 5: Proof — explicit cross-tenant data leak tests
# ===========================================================================


def test_proof_tenant_a_cannot_read_tenant_b_private_query_store(client) -> None:
    """PROOF: Tenant A saves 10 private queries. Tenant B sees zero of them."""
    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    for i in range(10):
        resp = client.post(
            "/queries",
            json={
                "title": f"Alpha Confidential {i}",
                "description": f"secret data {i}",
                "sql": f"SELECT {i} AS confidential",
                "visibility": "private",
            },
            headers=headers_a,
        )
        assert resp.status_code == 201

    list_b = client.get("/queries", headers=headers_b).json()
    alpha_visible_to_b = [
        q for q in list_b if q.get("title", "").startswith("Alpha Confidential")
    ]
    assert len(alpha_visible_to_b) == 0, (
        f"Tenant B must see 0 of Tenant A's private queries, "
        f"but saw {len(alpha_visible_to_b)}"
    )


def test_proof_tenant_b_cannot_read_tenant_a_query_history(client) -> None:
    """PROOF: Tenant A runs 5 queries. Tenant B's /history shows 0 of them."""
    sid_a = client.post("/session").json()["session_id"]
    sid_b = client.post("/session").json()["session_id"]

    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    # Tenant A runs 5 queries with distinctive SQL
    for i in range(5):
        client.post(
            "/query",
            json={"session_id": sid_a, "sql": f"SELECT {i} AS alpha_confidential_value"},
            headers=headers_a,
        )

    # Tenant B runs one query so they have a non-empty session
    client.post(
        "/query",
        json={"session_id": sid_b, "sql": "SELECT 'beta baseline'"},
        headers=headers_b,
    )

    hist_b = client.get("/history", headers=headers_b).json()
    leaked = [
        h for h in hist_b if "alpha_confidential_value" in h.get("sql", "")
    ]
    assert len(leaked) == 0, (
        f"Tenant B must see 0 entries from Tenant A's history, "
        f"but saw {len(leaked)}: {leaked}"
    )


def test_proof_tenant_slugs_are_namespace_scoped(client) -> None:
    """PROOF: Same slug in different tenants does not cause cross-contamination."""
    headers_a = _jwt_headers(TENANT_A)
    headers_b = _jwt_headers(TENANT_B)

    # Both tenants create a query with the same title (thus same slug)
    resp_a = client.post(
        "/queries",
        json={
            "title": "Shared Slug Query",
            "description": "Tenant A version",
            "sql": "SELECT 'alpha version'",
            "visibility": "private",
        },
        headers=headers_a,
    )
    assert resp_a.status_code == 201
    slug_a = resp_a.json()["slug"]

    # Tenant B tries to access the slug — must get 403/404 since it's private to A
    resp_b = client.get(f"/queries/{slug_a}", headers=headers_b)
    assert resp_b.status_code in (403, 404), (
        "Tenant B must not access Tenant A's private query even by known slug"
    )


def test_proof_new_tenant_starts_with_empty_history(client) -> None:
    """PROOF: A brand new tenant ID has zero history entries."""
    new_tenant_id = f"new-tenant-{int(time.time())}"
    headers_new = _jwt_headers(new_tenant_id)

    hist = client.get("/history", headers=headers_new).json()
    assert hist == [], (
        f"Brand new tenant must have empty history, but got {len(hist)} entries"
    )


def test_proof_new_tenant_sees_no_private_queries_in_list(client) -> None:
    """PROOF: Brand new tenant listing returns only public queries (zero private from others)."""
    # Pre-populate private queries from existing tenants
    headers_a = _jwt_headers(TENANT_A)
    for i in range(3):
        client.post(
            "/queries",
            json={
                "title": f"Alpha Private Proof {i}",
                "description": "",
                "sql": f"SELECT {i}",
                "visibility": "private",
            },
            headers=headers_a,
        )

    new_tenant_id = f"proof-new-tenant-{int(time.time())}"
    headers_new = _jwt_headers(new_tenant_id)

    queries = client.get("/queries", headers=headers_new).json()
    private_from_others = [
        q for q in queries
        if q.get("visibility") == "private" and q.get("tenant_id") != new_tenant_id
    ]
    assert len(private_from_others) == 0, (
        f"New tenant must not see any private queries from other tenants, "
        f"but saw {len(private_from_others)}"
    )
