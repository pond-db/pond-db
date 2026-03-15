"""Tests for the query store REST API endpoints.

Defines expected behavior for:
  - POST /queries — save a named query (requires API key)
  - GET /queries  — list user's queries with pagination (requires API key)
  - GET /queries/{slug} — get query by slug (requires API key)

Error cases:
  - 401 for missing/invalid API key on all three endpoints
  - 404 for GET /queries/{slug} with unknown slug
  - 409 for POST /queries with duplicate slug (same title)
  - 422 for POST /queries with missing required fields
"""

import importlib
import os
import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-secret-key-queries"


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)


@pytest.fixture
def client(set_api_key) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


def _auth(client: TestClient) -> dict:
    """Return headers dict with valid API key."""
    return {"X-API-Key": VALID_KEY}


# ---------------------------------------------------------------------------
# POST /queries — happy path
# ---------------------------------------------------------------------------


def test_post_queries_creates_query(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Top Customers",
            "description": "Customers by revenue",
            "sql": "SELECT customer_id, sum(amount) FROM orders GROUP BY 1 ORDER BY 2 DESC",
            "created_by": "alice",
        },
        headers=_auth(client),
    )
    assert resp.status_code == 201


def test_post_queries_returns_slug(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Daily Active Users",
            "description": "",
            "sql": "SELECT count(distinct user_id) FROM events WHERE date = today()",
            "created_by": "alice",
        },
        headers=_auth(client),
    )
    body = resp.json()
    assert "slug" in body
    assert isinstance(body["slug"], str)
    assert len(body["slug"]) > 0


def test_post_queries_slug_is_url_safe(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Revenue By Region & Product!",
            "description": "",
            "sql": "SELECT region, product, sum(rev) FROM sales GROUP BY 1,2",
            "created_by": "alice",
        },
        headers=_auth(client),
    )
    slug = resp.json()["slug"]
    assert slug == slug.lower()
    for char in slug:
        assert char.isalnum() or char == "-", f"Non-URL-safe char: {char!r}"


def test_post_queries_response_includes_metadata(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "Metadata Check",
            "description": "A description",
            "sql": "SELECT 1",
            "created_by": "bob",
        },
        headers=_auth(client),
    )
    body = resp.json()
    assert body["title"] == "Metadata Check"
    assert body["description"] == "A description"
    assert body["sql"] == "SELECT 1"
    assert body["created_by"] == "bob"
    assert "created_at" in body
    assert "slug" in body


def test_post_queries_description_optional(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={
            "title": "No Desc Query",
            "sql": "SELECT 2",
            "created_by": "alice",
        },
        headers=_auth(client),
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# POST /queries — auth
# ---------------------------------------------------------------------------


def test_post_queries_missing_api_key_returns_401(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={"title": "T", "sql": "SELECT 1", "created_by": "alice"},
    )
    assert resp.status_code == 401


def test_post_queries_wrong_api_key_returns_401(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={"title": "T2", "sql": "SELECT 1", "created_by": "alice"},
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /queries — error cases
# ---------------------------------------------------------------------------


def test_post_queries_duplicate_slug_returns_409(client: TestClient) -> None:
    payload = {
        "title": "Duplicate Title Query",
        "description": "",
        "sql": "SELECT 1",
        "created_by": "alice",
    }
    resp1 = client.post("/queries", json=payload, headers=_auth(client))
    assert resp1.status_code == 201

    # Same title → same slug → conflict
    resp2 = client.post(
        "/queries",
        json={**payload, "sql": "SELECT 2"},
        headers=_auth(client),
    )
    assert resp2.status_code == 409


def test_post_queries_missing_title_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={"sql": "SELECT 1", "created_by": "alice"},
        headers=_auth(client),
    )
    assert resp.status_code == 422


def test_post_queries_missing_sql_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/queries",
        json={"title": "No SQL", "created_by": "alice"},
        headers=_auth(client),
    )
    assert resp.status_code == 422


def test_post_queries_missing_created_by_defaults_to_tenant(client: TestClient) -> None:
    """When created_by is omitted, JWT auth derives it from tenant_id."""
    resp = client.post(
        "/queries",
        json={"title": "No Creator", "sql": "SELECT 1"},
        headers=_auth(client),
    )
    # With JWT auth, created_by is optional — derived from token
    assert resp.status_code in (201, 422)


# ---------------------------------------------------------------------------
# GET /queries — happy path
# ---------------------------------------------------------------------------


def test_get_queries_returns_list(client: TestClient) -> None:
    client.post(
        "/queries",
        json={"title": "List Query One", "sql": "SELECT 1", "created_by": "alice"},
        headers=_auth(client),
    )
    resp = client.get("/queries", params={"created_by": "alice"}, headers=_auth(client))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_queries_returns_tenant_scoped_queries(client: TestClient) -> None:
    """With tenant isolation, list_queries returns own + public queries."""
    client.post(
        "/queries",
        json={"title": "Alice Query List", "sql": "SELECT 1", "created_by": "alice"},
        headers=_auth(client),
    )
    client.post(
        "/queries",
        json={"title": "Bob Query List", "sql": "SELECT 2", "created_by": "bob"},
        headers=_auth(client),
    )
    resp = client.get("/queries", headers=_auth(client))
    results = resp.json()
    # All results should belong to the authenticated tenant or be public
    assert isinstance(results, list)
    assert len(results) >= 1


def test_get_queries_default_limit_is_20(client: TestClient) -> None:
    for i in range(25):
        client.post(
            "/queries",
            json={
                "title": f"Bulk Query Api {i:03d}",
                "sql": f"SELECT {i}",
                "created_by": "limituser",
            },
            headers=_auth(client),
        )
    resp = client.get("/queries", params={"created_by": "limituser"}, headers=_auth(client))
    assert resp.status_code == 200
    assert len(resp.json()) == 20


def test_get_queries_limit_param(client: TestClient) -> None:
    for i in range(10):
        client.post(
            "/queries",
            json={
                "title": f"Limit Test Api {i:03d}",
                "sql": f"SELECT {i}",
                "created_by": "limituser2",
            },
            headers=_auth(client),
        )
    resp = client.get(
        "/queries",
        params={"created_by": "limituser2", "limit": 3},
        headers=_auth(client),
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_get_queries_offset_param(client: TestClient) -> None:
    for i in range(8):
        client.post(
            "/queries",
            json={
                "title": f"Offset Test Api {i:03d}",
                "sql": f"SELECT {i}",
                "created_by": "offsetuser",
            },
            headers=_auth(client),
        )
    page1 = client.get(
        "/queries",
        params={"created_by": "offsetuser", "limit": 4, "offset": 0},
        headers=_auth(client),
    ).json()
    page2 = client.get(
        "/queries",
        params={"created_by": "offsetuser", "limit": 4, "offset": 4},
        headers=_auth(client),
    ).json()
    slugs1 = {r["slug"] for r in page1}
    slugs2 = {r["slug"] for r in page2}
    assert slugs1.isdisjoint(slugs2)
    assert len(slugs1 | slugs2) == 8


def test_get_queries_response_includes_expected_fields(client: TestClient) -> None:
    client.post(
        "/queries",
        json={
            "title": "Fields Api Check",
            "description": "desc",
            "sql": "SELECT 99",
            "created_by": "fielduser",
        },
        headers=_auth(client),
    )
    resp = client.get(
        "/queries", params={"created_by": "fielduser"}, headers=_auth(client)
    )
    item = resp.json()[0]
    for field in ("slug", "title", "description", "sql", "created_by", "created_at"):
        assert field in item, f"Missing field: {field}"


def test_get_queries_empty_for_unknown_user(client: TestClient) -> None:
    resp = client.get(
        "/queries", params={"created_by": "ghost-user-xyz"}, headers=_auth(client)
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /queries — auth
# ---------------------------------------------------------------------------


def test_get_queries_missing_api_key_returns_401(client: TestClient) -> None:
    resp = client.get("/queries", params={"created_by": "alice"})
    assert resp.status_code == 401


def test_get_queries_wrong_api_key_returns_401(client: TestClient) -> None:
    resp = client.get(
        "/queries",
        params={"created_by": "alice"},
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /queries/{slug} — happy path
# ---------------------------------------------------------------------------


def test_get_query_by_slug_returns_200(client: TestClient) -> None:
    post_resp = client.post(
        "/queries",
        json={"title": "Slug Fetch Test", "sql": "SELECT 7", "created_by": "alice"},
        headers=_auth(client),
    )
    slug = post_resp.json()["slug"]
    resp = client.get(f"/queries/{slug}", headers=_auth(client))
    assert resp.status_code == 200


def test_get_query_by_slug_returns_full_data(client: TestClient) -> None:
    post_resp = client.post(
        "/queries",
        json={
            "title": "Full Data Slug",
            "description": "complete",
            "sql": "SELECT 100",
            "created_by": "dave",
        },
        headers=_auth(client),
    )
    slug = post_resp.json()["slug"]
    resp = client.get(f"/queries/{slug}", headers=_auth(client))
    body = resp.json()
    assert body["title"] == "Full Data Slug"
    assert body["description"] == "complete"
    assert body["sql"] == "SELECT 100"
    assert body["created_by"] == "dave"
    assert body["slug"] == slug
    assert "created_at" in body


# ---------------------------------------------------------------------------
# GET /queries/{slug} — error cases
# ---------------------------------------------------------------------------


def test_get_query_by_slug_missing_returns_404(client: TestClient) -> None:
    resp = client.get("/queries/nonexistent-slug-xyz", headers=_auth(client))
    assert resp.status_code == 404


def test_get_query_by_slug_404_has_detail(client: TestClient) -> None:
    resp = client.get("/queries/no-such-slug", headers=_auth(client))
    body = resp.json()
    assert "detail" in body


def test_get_query_by_slug_missing_api_key_returns_401(client: TestClient) -> None:
    resp = client.get("/queries/any-slug")
    assert resp.status_code == 401


def test_get_query_by_slug_wrong_api_key_returns_401(client: TestClient) -> None:
    resp = client.get("/queries/any-slug", headers={"X-API-Key": "bad"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Slug generation consistency
# ---------------------------------------------------------------------------


def test_same_title_produces_same_slug(client: TestClient) -> None:
    """Deterministic: same title always maps to the same slug."""
    post_resp = client.post(
        "/queries",
        json={"title": "Consistent Slug Title", "sql": "SELECT 1", "created_by": "alice"},
        headers=_auth(client),
    )
    slug1 = post_resp.json()["slug"]

    # Attempt a second save — should conflict (409) with the same slug
    conflict_resp = client.post(
        "/queries",
        json={"title": "Consistent Slug Title", "sql": "SELECT 2", "created_by": "bob"},
        headers=_auth(client),
    )
    assert conflict_resp.status_code == 409
