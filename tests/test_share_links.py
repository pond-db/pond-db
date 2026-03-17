"""Integration tests for share links — GET /q/{slug} and query visibility.

Defines expected behavior for:
  - visibility field on queries (default "private", accepts "public")
  - POST /queries with visibility field
  - GET /q/{slug} — re-executes saved query, no session required
  - Auth rules: public queries accessible without API key, private require API key
  - 403 for private query accessed without API key
  - 404 for unknown slug
  - Rate limiting: 10 req/min per IP on public GET /q/{slug} → 429 on 11th
"""

import importlib

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-share-key"


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)


@pytest.fixture
def client(set_api_key) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


def _auth() -> dict:
    return {"X-API-Key": VALID_KEY}


def _create_query(
    client: TestClient,
    *,
    title: str,
    sql: str = "SELECT 42",
    visibility: str = "public",
    created_by: str = "alice",
) -> dict:
    resp = client.post(
        "/queries",
        json={
            "title": title,
            "sql": sql,
            "created_by": created_by,
            "visibility": visibility,
        },
        headers=_auth(),
    )
    assert resp.status_code == 201, f"Setup failed: {resp.json()}"
    return resp.json()


# ---------------------------------------------------------------------------
# POST /queries — visibility field
# ---------------------------------------------------------------------------


def test_post_queries_visibility_defaults_to_private(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={"title": "Vis Default Query", "sql": "SELECT 1", "created_by": "alice"},
        headers=_auth(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["visibility"] == "private"


def test_post_queries_visibility_explicit_private(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Explicit Private",
            "sql": "SELECT 1",
            "created_by": "alice",
            "visibility": "private",
        },
        headers=_auth(),
    )
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "private"


def test_post_queries_visibility_explicit_public(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Explicit Public",
            "sql": "SELECT 1",
            "created_by": "alice",
            "visibility": "public",
        },
        headers=_auth(),
    )
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "public"


def test_post_queries_invalid_visibility_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Bad Vis",
            "sql": "SELECT 1",
            "created_by": "alice",
            "visibility": "secret",
        },
        headers=_auth(),
    )
    assert resp.status_code == 422


def test_post_queries_response_includes_visibility_field(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={"title": "Vis In Response", "sql": "SELECT 1", "created_by": "alice"},
        headers=_auth(),
    )
    assert "visibility" in resp.json()


# ---------------------------------------------------------------------------
# GET /queries — visibility field present in list and get-by-slug
# ---------------------------------------------------------------------------


def test_get_query_by_slug_includes_visibility(client: TestClient) -> None:
    body = _create_query(client, title="Slug Vis Check", visibility="public")
    slug = body["slug"]
    resp = client.get(f"/queries/{slug}", headers=_auth())
    assert resp.status_code == 200
    assert "visibility" in resp.json()
    assert resp.json()["visibility"] == "public"


def test_list_queries_includes_visibility(client: TestClient) -> None:
    _create_query(client, title="List Vis Check", visibility="private")
    resp = client.get("/queries", params={"created_by": "alice"}, headers=_auth())
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    for item in items:
        assert "visibility" in item


# ---------------------------------------------------------------------------
# GET /q/{slug} — public query (no auth required)
# ---------------------------------------------------------------------------


def test_get_public_query_share_link_returns_200(client: TestClient) -> None:
    body = _create_query(client, title="Public Share One", sql="SELECT 42", visibility="public")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    assert resp.status_code == 200


def test_get_public_query_share_link_returns_results(client: TestClient) -> None:
    body = _create_query(
        client, title="Public Share Two", sql="SELECT 42 AS answer", visibility="public"
    )
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert "columns" in data
    assert "rows" in data
    assert isinstance(data["columns"], list)
    assert isinstance(data["rows"], list)


def test_get_public_query_result_values_correct(client: TestClient) -> None:
    body = _create_query(
        client, title="Public Result Val", sql="SELECT 7 AS n", visibility="public"
    )
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    data = resp.json()
    assert len(data["rows"]) == 1
    # The value 7 should appear somewhere in the first row
    flat = [str(v) for v in data["rows"][0]]
    assert "7" in flat


def test_get_public_query_columns_match_sql(client: TestClient) -> None:
    body = _create_query(
        client,
        title="Public Cols Check",
        sql="SELECT 1 AS a, 2 AS b",
        visibility="public",
    )
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    data = resp.json()
    assert "a" in data["columns"]
    assert "b" in data["columns"]


def test_get_public_query_no_api_key_allowed(client: TestClient) -> None:
    """Public queries must not require X-API-Key header."""
    body = _create_query(client, title="Public No Key", sql="SELECT 99", visibility="public")
    slug = body["slug"]
    # No headers at all
    resp = client.get(f"/q/{slug}")
    assert resp.status_code == 200


def test_get_public_query_with_api_key_also_works(client: TestClient) -> None:
    """Passing an API key should not break public query access."""
    body = _create_query(client, title="Public With Key", sql="SELECT 11", visibility="public")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}", headers=_auth())
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /q/{slug} — private query auth enforcement
# ---------------------------------------------------------------------------


def test_get_private_query_without_api_key_returns_403(client: TestClient) -> None:
    body = _create_query(client, title="Private Share One", sql="SELECT 1", visibility="private")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    assert resp.status_code == 403


def test_get_private_query_wrong_api_key_returns_403(client: TestClient) -> None:
    body = _create_query(client, title="Private Share Two", sql="SELECT 2", visibility="private")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 403


def test_get_private_query_with_valid_api_key_returns_200(client: TestClient) -> None:
    body = _create_query(client, title="Private Share Three", sql="SELECT 55", visibility="private")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}", headers=_auth())
    assert resp.status_code == 200


def test_get_private_query_with_valid_api_key_returns_results(client: TestClient) -> None:
    body = _create_query(
        client, title="Private Share Four", sql="SELECT 55 AS val", visibility="private"
    )
    slug = body["slug"]
    resp = client.get(f"/q/{slug}", headers=_auth())
    data = resp.json()
    assert "columns" in data
    assert "rows" in data


def test_get_private_query_403_has_detail(client: TestClient) -> None:
    body = _create_query(client, title="Private 403 Detail", sql="SELECT 1", visibility="private")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    assert "detail" in resp.json()


def test_get_default_visibility_query_without_key_returns_403(client: TestClient) -> None:
    """Queries created without explicit visibility should default to private → 403."""
    resp = client.post(
        "/queries",
        json={"title": "Default Vis No Key", "sql": "SELECT 1", "created_by": "alice"},
        headers=_auth(),
    )
    slug = resp.json()["slug"]
    share_resp = client.get(f"/q/{slug}")
    assert share_resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /q/{slug} — 404 for unknown slug
# ---------------------------------------------------------------------------


def test_get_share_link_unknown_slug_returns_404(client: TestClient) -> None:
    resp = client.get("/q/no-such-slug-xyz-abc")
    assert resp.status_code == 404


def test_get_share_link_unknown_slug_has_detail(client: TestClient) -> None:
    resp = client.get("/q/absolutely-nonexistent")
    assert resp.status_code == 404
    assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# GET /q/{slug} — response shape
# ---------------------------------------------------------------------------


def test_share_link_response_includes_rowcount(client: TestClient) -> None:
    body = _create_query(
        client, title="Rowcount Check", sql="SELECT 1 UNION ALL SELECT 2", visibility="public"
    )
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    data = resp.json()
    assert "rowcount" in data
    assert data["rowcount"] == 2


def test_share_link_response_includes_elapsed_ms(client: TestClient) -> None:
    body = _create_query(client, title="Elapsed Check", sql="SELECT 1", visibility="public")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    data = resp.json()
    assert "elapsed_ms" in data
    assert isinstance(data["elapsed_ms"], (int, float))
    assert data["elapsed_ms"] >= 0


def test_share_link_response_includes_slug(client: TestClient) -> None:
    body = _create_query(client, title="Slug In Response", sql="SELECT 1", visibility="public")
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    data = resp.json()
    assert "slug" in data
    assert data["slug"] == slug


def test_share_link_multi_row_result(client: TestClient) -> None:
    body = _create_query(
        client,
        title="Multi Row Share",
        sql="SELECT * FROM (VALUES (1), (2), (3)) t(n)",
        visibility="public",
    )
    slug = body["slug"]
    resp = client.get(f"/q/{slug}")
    data = resp.json()
    assert len(data["rows"]) == 3


# ---------------------------------------------------------------------------
# Rate limiting — public GET /q/{slug} limited to 10/min per IP
# ---------------------------------------------------------------------------


def test_rate_limit_allows_10_requests(client: TestClient) -> None:
    body = _create_query(client, title="Rate Limit Happy", sql="SELECT 1", visibility="public")
    slug = body["slug"]
    # First 10 requests should all succeed
    for i in range(10):
        resp = client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.0.0.1"})
        assert resp.status_code == 200, f"Request {i + 1} should succeed, got {resp.status_code}"


def test_rate_limit_blocks_11th_request(client: TestClient) -> None:
    body = _create_query(client, title="Rate Limit Block", sql="SELECT 2", visibility="public")
    slug = body["slug"]
    for i in range(10):
        client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.0.0.2"})
    # 11th request must be rate limited
    resp = client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.0.0.2"})
    assert resp.status_code == 429


def test_rate_limit_429_has_detail(client: TestClient) -> None:
    body = _create_query(client, title="Rate Limit Detail", sql="SELECT 3", visibility="public")
    slug = body["slug"]
    for _ in range(10):
        client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.0.0.3"})
    resp = client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.0.0.3"})
    assert resp.status_code == 429
    assert "detail" in resp.json()


def test_rate_limit_different_ips_have_separate_buckets(client: TestClient) -> None:
    """Two different IPs should each get their own 10-request allowance."""
    body = _create_query(client, title="Rate Limit Buckets", sql="SELECT 4", visibility="public")
    slug = body["slug"]
    # Exhaust IP A
    for _ in range(10):
        client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.1.0.1"})
    resp_a = client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.1.0.1"})
    assert resp_a.status_code == 429
    # IP B still has a full bucket
    resp_b = client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.1.0.2"})
    assert resp_b.status_code == 200


def test_rate_limit_does_not_apply_to_private_queries_with_auth(client: TestClient) -> None:
    """Authenticated access to private queries is NOT rate-limited."""
    body = _create_query(
        client, title="Rate Limit Private Auth", sql="SELECT 5", visibility="private"
    )
    slug = body["slug"]
    # Make 12 authenticated requests — none should be rate-limited
    for i in range(12):
        resp = client.get(f"/q/{slug}", headers={**_auth(), "X-Forwarded-For": "10.2.0.1"})
        assert resp.status_code == 200, f"Authenticated request {i + 1} should not be rate-limited"


def test_rate_limit_returns_retry_after_header(client: TestClient) -> None:
    """429 response should include Retry-After header indicating when to retry."""
    body = _create_query(client, title="Rate Retry After", sql="SELECT 6", visibility="public")
    slug = body["slug"]
    for _ in range(10):
        client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.3.0.1"})
    resp = client.get(f"/q/{slug}", headers={"X-Forwarded-For": "10.3.0.1"})
    assert resp.status_code == 429
    assert "retry-after" in {k.lower() for k in resp.headers}


# ---------------------------------------------------------------------------
# Token bucket — visibility field persisted after save
# ---------------------------------------------------------------------------


def test_visibility_persisted_on_save(client: TestClient) -> None:
    """Visibility must be stored and retrievable via GET /queries/{slug}."""
    for vis in ("public", "private"):
        resp = client.post(
            "/queries",
            json={
                "title": f"Persist Vis {vis.capitalize()}",
                "sql": "SELECT 1",
                "created_by": "alice",
                "visibility": vis,
            },
            headers=_auth(),
        )
        slug = resp.json()["slug"]
        get_resp = client.get(f"/queries/{slug}", headers=_auth())
        assert get_resp.json()["visibility"] == vis, (
            f"Expected {vis}, got {get_resp.json()['visibility']}"
        )


def test_visibility_included_in_list_response(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Vis In List Check",
            "sql": "SELECT 1",
            "created_by": "vislistuser",
            "visibility": "public",
        },
        headers=_auth(),
    )
    assert resp.status_code == 201
    list_resp = client.get("/queries", params={"created_by": "vislistuser"}, headers=_auth())
    items = list_resp.json()
    assert len(items) >= 1
    assert items[0]["visibility"] == "public"
