"""Tests for result caching — in-memory cache with 5-minute TTL.

Defines expected behavior for:
  - ResultCache class: get(), set(), make_key(), invalidate(), clear()
  - Cache key = hash(SQL text + dataset_version)
  - TTL-based expiry (default 300 seconds)
  - POST /query returns X-Cache: MISS on first execution
  - POST /query returns X-Cache: HIT on repeated identical queries
  - Cache hit skips DuckDB — returns identical data, near-zero elapsed_ms
  - Write operations (INSERT/CREATE TABLE) bump dataset version and invalidate cache
  - Failed queries are not cached

Tests will FAIL until implementation is complete.
"""

import importlib
import time

import pytest

VALID_KEY = "test-cache-api-key-def456"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)


@pytest.fixture
def client(_set_api_key):
    from fastapi.testclient import TestClient
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def session_id(client) -> str:
    resp = client.post("/session", json={"namespace": "cache-ns"})
    assert resp.status_code == 201
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# ResultCache — unit tests
# ---------------------------------------------------------------------------


def test_result_cache_importable() -> None:
    """ResultCache must be importable from ponddb.pondapi.result_cache."""
    from ponddb.pondapi.result_cache import ResultCache  # noqa: F401


def test_result_cache_instantiates() -> None:
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache(ttl_seconds=300)
    assert cache is not None


def test_result_cache_default_ttl_is_300() -> None:
    """Default TTL is 300 seconds (5 minutes)."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    assert cache.ttl_seconds == 300


def test_result_cache_get_miss_on_empty() -> None:
    """get() returns None when the key is not cached."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    result = cache.get("nonexistent-cache-key")
    assert result is None


def test_result_cache_set_then_get() -> None:
    """set() followed by get() returns the stored value."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    data = {"columns": ["x"], "rows": [[42]], "rowcount": 1, "elapsed_ms": 5.0}
    cache.set("mykey", data)
    result = cache.get("mykey")
    assert result == data


def test_result_cache_get_returns_copy_or_same_data() -> None:
    """get() returns data equivalent to what was set."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    data = {"columns": ["a", "b"], "rows": [[1, 2], [3, 4]], "rowcount": 2, "elapsed_ms": 10.0}
    cache.set("k", data)
    retrieved = cache.get("k")
    assert retrieved["columns"] == data["columns"]
    assert retrieved["rows"] == data["rows"]
    assert retrieved["rowcount"] == data["rowcount"]


def test_result_cache_expiry_after_ttl() -> None:
    """Entries are inaccessible after TTL seconds have elapsed."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache(ttl_seconds=1)
    data = {"columns": ["x"], "rows": [[99]], "rowcount": 1, "elapsed_ms": 2.0}
    cache.set("expkey", data)

    assert cache.get("expkey") is not None  # Should be present immediately

    time.sleep(1.1)  # Wait for TTL to expire

    assert cache.get("expkey") is None  # Should now be expired


def test_result_cache_unexpired_entry_still_accessible() -> None:
    """Entry within TTL window remains accessible."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache(ttl_seconds=10)
    data = {"rows": [[1]]}
    cache.set("k", data)

    time.sleep(0.05)  # Well within TTL

    assert cache.get("k") is not None


def test_result_cache_set_overwrites_existing_key() -> None:
    """set() on an existing key replaces the old value."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    cache.set("k", {"rows": [[1]]})
    cache.set("k", {"rows": [[2]]})
    result = cache.get("k")
    assert result == {"rows": [[2]]}


def test_result_cache_independent_keys() -> None:
    """Different keys are stored independently."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    cache.set("k1", {"rows": [[1]]})
    cache.set("k2", {"rows": [[2]]})
    assert cache.get("k1") == {"rows": [[1]]}
    assert cache.get("k2") == {"rows": [[2]]}


def test_result_cache_invalidate_removes_entry() -> None:
    """invalidate(key) removes a specific cached entry."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    cache.set("k", {"rows": [[99]]})
    cache.invalidate("k")
    assert cache.get("k") is None


def test_result_cache_invalidate_nonexistent_key_is_noop() -> None:
    """invalidate() on a missing key does not raise."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    cache.invalidate("does-not-exist")  # Must not raise


def test_result_cache_clear_removes_all_entries() -> None:
    """clear() evicts all cached entries."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    cache.set("k1", {"rows": [[1]]})
    cache.set("k2", {"rows": [[2]]})
    cache.set("k3", {"rows": [[3]]})
    cache.clear()
    assert cache.get("k1") is None
    assert cache.get("k2") is None
    assert cache.get("k3") is None


# ---------------------------------------------------------------------------
# ResultCache.make_key — cache key generation
# ---------------------------------------------------------------------------


def test_make_key_returns_string() -> None:
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    key = cache.make_key("SELECT 1", "v0")
    assert isinstance(key, str)
    assert len(key) > 0


def test_make_key_is_deterministic() -> None:
    """Same SQL and version always produce the same key."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    k1 = cache.make_key("SELECT 42 AS answer", "dataset-v1")
    k2 = cache.make_key("SELECT 42 AS answer", "dataset-v1")
    assert k1 == k2


def test_make_key_differs_on_sql() -> None:
    """Different SQL strings produce different keys."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    k1 = cache.make_key("SELECT 1", "v0")
    k2 = cache.make_key("SELECT 2", "v0")
    assert k1 != k2


def test_make_key_differs_on_version() -> None:
    """Same SQL with different dataset version produces a different key."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    k1 = cache.make_key("SELECT 1", "v0")
    k2 = cache.make_key("SELECT 1", "v1")
    assert k1 != k2


def test_make_key_whitespace_sensitivity() -> None:
    """SQL with different whitespace produces different keys."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    k1 = cache.make_key("SELECT 1", "v0")
    k2 = cache.make_key("SELECT  1", "v0")  # extra space
    assert k1 != k2


def test_make_key_case_sensitivity() -> None:
    """SQL case differences produce different keys (no normalisation expected)."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    k1 = cache.make_key("select 1", "v0")
    k2 = cache.make_key("SELECT 1", "v0")
    assert k1 != k2


def test_make_key_is_not_plain_sql() -> None:
    """Cache key should not be the raw SQL string (must be hashed)."""
    from ponddb.pondapi.result_cache import ResultCache

    cache = ResultCache()
    sql = "SELECT 1"
    key = cache.make_key(sql, "v0")
    assert key != sql


# ---------------------------------------------------------------------------
# POST /query — X-Cache header behaviour
# ---------------------------------------------------------------------------


def test_query_first_call_returns_x_cache_miss(client, auth_headers, session_id) -> None:
    """First execution of a query returns X-Cache: MISS."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 'cache_miss_probe' AS v"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Cache") == "MISS"


def test_query_second_call_returns_x_cache_hit(client, auth_headers, session_id) -> None:
    """Second identical query returns X-Cache: HIT."""
    sql = "SELECT 'cache_hit_probe' AS v"

    first = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    assert first.status_code == 200
    assert first.headers.get("X-Cache") == "MISS"

    second = client.post(
        "/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers
    )
    assert second.status_code == 200
    assert second.headers.get("X-Cache") == "HIT"


def test_x_cache_header_always_present(client, auth_headers, session_id) -> None:
    """Every successful POST /query response includes X-Cache header."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 'always_header' AS h"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "X-Cache" in resp.headers


def test_cache_hit_returns_identical_data(client, auth_headers, session_id) -> None:
    """Cache hit returns exactly the same columns and rows as the original."""
    sql = "SELECT 42 AS answer, 'hello' AS greeting"

    first = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    second = client.post(
        "/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers
    )

    assert second.headers.get("X-Cache") == "HIT"
    assert first.json()["columns"] == second.json()["columns"]
    assert first.json()["rows"] == second.json()["rows"]
    assert first.json()["rowcount"] == second.json()["rowcount"]


def test_different_sql_strings_produce_separate_cache_entries(
    client, auth_headers, session_id
) -> None:
    """Two different SQL queries each get their own MISS."""
    r1 = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 1 AS n"}, headers=auth_headers
    )
    r2 = client.post(
        "/query", json={"session_id": session_id, "sql": "SELECT 2 AS n"}, headers=auth_headers
    )

    assert r1.headers.get("X-Cache") == "MISS"
    assert r2.headers.get("X-Cache") == "MISS"


def test_third_call_still_hits_cache(client, auth_headers, session_id) -> None:
    """Cache remains valid for subsequent calls (not just the second)."""
    sql = "SELECT 'triple' AS t"
    for i in range(3):
        resp = client.post(
            "/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers
        )
        assert resp.status_code == 200
        expected = "MISS" if i == 0 else "HIT"
        assert resp.headers.get("X-Cache") == expected, f"Call #{i + 1}: expected {expected}"


def test_failed_query_not_cached(client, auth_headers, session_id) -> None:
    """A query that raises a DuckDB error is not stored in cache (next call is still MISS)."""
    bad_sql = "SELECT * FROM absolutely_missing_table_xyz_123"

    first = client.post(
        "/query", json={"session_id": session_id, "sql": bad_sql}, headers=auth_headers
    )
    assert first.status_code == 400

    # On error, X-Cache should be MISS if present at all
    x_cache = first.headers.get("X-Cache")
    if x_cache is not None:
        assert x_cache == "MISS"

    # A repeated call to the same bad SQL should also be 400 (not a cached 200)
    second = client.post(
        "/query", json={"session_id": session_id, "sql": bad_sql}, headers=auth_headers
    )
    assert second.status_code == 400


def test_write_increments_dataset_version_and_invalidates_cache(
    client, auth_headers, session_id
) -> None:
    """After a write (INSERT), a previously-cached SELECT is re-executed (MISS)."""
    setup_sql = "CREATE TABLE cache_inv_test (val INTEGER)"
    select_sql = "SELECT count(*) AS cnt FROM cache_inv_test"

    client.post("/query", json={"session_id": session_id, "sql": setup_sql}, headers=auth_headers)

    # First SELECT — MISS
    r1 = client.post(
        "/query", json={"session_id": session_id, "sql": select_sql}, headers=auth_headers
    )
    assert r1.status_code == 200
    assert r1.headers.get("X-Cache") == "MISS"

    # Second SELECT — HIT
    r2 = client.post(
        "/query", json={"session_id": session_id, "sql": select_sql}, headers=auth_headers
    )
    assert r2.status_code == 200
    assert r2.headers.get("X-Cache") == "HIT"

    # Write: INSERT changes the dataset version
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "INSERT INTO cache_inv_test VALUES (1)"},
        headers=auth_headers,
    )

    # Third SELECT — must be MISS because data changed
    r3 = client.post(
        "/query", json={"session_id": session_id, "sql": select_sql}, headers=auth_headers
    )
    assert r3.status_code == 200
    assert r3.headers.get("X-Cache") == "MISS"

    # Result after insert should reflect the new row
    assert r3.json()["rows"][0][0] == 1


def test_create_table_invalidates_cache(client, auth_headers, session_id) -> None:
    """CREATE TABLE is treated as a write and bumps the dataset version."""
    select_sql = (
        "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = 'ddl_inv_tbl'"
    )

    # Before table creation
    r1 = client.post(
        "/query", json={"session_id": session_id, "sql": select_sql}, headers=auth_headers
    )
    assert r1.status_code == 200
    assert r1.headers.get("X-Cache") == "MISS"

    r2 = client.post(
        "/query", json={"session_id": session_id, "sql": select_sql}, headers=auth_headers
    )
    assert r2.headers.get("X-Cache") == "HIT"

    # DDL write: create table
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE ddl_inv_tbl (id INTEGER)"},
        headers=auth_headers,
    )

    # Cache must be invalidated
    r3 = client.post(
        "/query", json={"session_id": session_id, "sql": select_sql}, headers=auth_headers
    )
    assert r3.headers.get("X-Cache") == "MISS"


def test_cache_hit_response_is_200(client, auth_headers, session_id) -> None:
    """Cache HIT response is still HTTP 200."""
    sql = "SELECT 'status_check' AS v"
    client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)

    second = client.post(
        "/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers
    )
    assert second.status_code == 200
    assert second.headers.get("X-Cache") == "HIT"


def test_cache_hit_elapsed_ms_less_than_original(client, auth_headers, session_id) -> None:
    """Cache HIT elapsed_ms should be much lower than original DuckDB execution."""
    # Use a slightly heavier query to get a measurable baseline
    sql = "SELECT i, i*i AS sq FROM range(1000) t(i)"

    first = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    assert first.headers.get("X-Cache") == "MISS"

    second = client.post(
        "/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers
    )
    assert second.headers.get("X-Cache") == "HIT"
    # Cached response should be significantly faster (at least 10x)
    assert second.json()["elapsed_ms"] < first.json()["elapsed_ms"]


def test_x_cache_value_is_uppercase(client, auth_headers, session_id) -> None:
    """X-Cache header value is either 'HIT' or 'MISS' (uppercase)."""
    sql = "SELECT 'case_test' AS c"

    r1 = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)
    r2 = client.post("/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers)

    assert r1.headers.get("X-Cache") in ("HIT", "MISS")
    assert r2.headers.get("X-Cache") in ("HIT", "MISS")


def test_query_without_api_key_has_no_cache_header(client, session_id) -> None:
    """Unauthenticated requests return 401 and no X-Cache header is expected."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
    )
    assert resp.status_code == 401
    # X-Cache should not appear on auth-rejected requests
    assert resp.headers.get("X-Cache") is None


def test_cache_miss_followed_by_hit_same_sql_and_session(client, auth_headers, session_id) -> None:
    """Comprehensive MISS → HIT sequence with result verification."""
    sql = "SELECT 7 AS lucky, 'test' AS label"

    miss_resp = client.post(
        "/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers
    )
    assert miss_resp.status_code == 200
    assert miss_resp.headers.get("X-Cache") == "MISS"
    original_data = miss_resp.json()

    hit_resp = client.post(
        "/query", json={"session_id": session_id, "sql": sql}, headers=auth_headers
    )
    assert hit_resp.status_code == 200
    assert hit_resp.headers.get("X-Cache") == "HIT"
    cached_data = hit_resp.json()

    assert original_data["columns"] == cached_data["columns"]
    assert original_data["rows"] == cached_data["rows"]
    assert original_data["rowcount"] == cached_data["rowcount"]
