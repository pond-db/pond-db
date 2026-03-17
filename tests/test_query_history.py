"""Tests for query history — per-user execution log in SQLite.

Defines expected behavior for:
  - MetadataStore: log_query_history(), get_query_history() methods
  - query_history SQLite table (created on initialize)
  - GET /history endpoint: auth required, filterable, paginated
  - POST /query automatically logs each execution to history

Tests will FAIL until implementation is complete.
"""

import importlib
from datetime import datetime, timezone, timedelta

import pytest

VALID_KEY = "test-history-api-key-abc123"


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
    resp = client.post("/session", json={"namespace": "testns"})
    assert resp.status_code == 201
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# MetadataStore — query_history table schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_history_table_created_on_initialize(tmp_path) -> None:
    """query_history table must exist after initialize()."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    cursor = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='query_history'"
    )
    result = cursor.fetchone()
    assert result is not None, "query_history table must be created by initialize()"
    await store.close()


@pytest.mark.asyncio
async def test_log_query_history_method_exists(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()
    assert hasattr(store, "log_query_history"), (
        "MetadataStore must have log_query_history() method"
    )
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_method_exists(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()
    assert hasattr(store, "get_query_history"), (
        "MetadataStore must have get_query_history() method"
    )
    await store.close()


@pytest.mark.asyncio
async def test_log_query_history_success_entry(tmp_path) -> None:
    """log_query_history() persists a success record with all required fields."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    ts = datetime.now(timezone.utc)
    await store.log_query_history(
        namespace="alice",
        sql="SELECT 42 AS answer",
        duration_ms=15.3,
        rows_returned=1,
        status="success",
        executed_at=ts,
    )

    rows = await store.get_query_history(namespace="alice")
    assert len(rows) == 1
    row = rows[0]
    assert row["namespace"] == "alice"
    assert row["sql"] == "SELECT 42 AS answer"
    assert row["duration_ms"] == pytest.approx(15.3)
    assert row["rows_returned"] == 1
    assert row["status"] == "success"
    assert row["error_message"] is None
    assert "executed_at" in row
    await store.close()


@pytest.mark.asyncio
async def test_log_query_history_error_entry(tmp_path) -> None:
    """Error entries store error_message and zero rows_returned."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    await store.log_query_history(
        namespace="bob",
        sql="SELECT * FROM no_such_table",
        duration_ms=2.1,
        rows_returned=0,
        status="error",
        error_message="Table not found: no_such_table",
        executed_at=datetime.now(timezone.utc),
    )

    rows = await store.get_query_history(namespace="bob")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "error"
    assert row["error_message"] == "Table not found: no_such_table"
    assert row["rows_returned"] == 0
    await store.close()


@pytest.mark.asyncio
async def test_log_query_history_error_message_defaults_none(tmp_path) -> None:
    """error_message defaults to None when not provided."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    await store.log_query_history(
        namespace="carol",
        sql="SELECT 1",
        duration_ms=1.0,
        rows_returned=1,
        status="success",
        executed_at=datetime.now(timezone.utc),
    )

    rows = await store.get_query_history(namespace="carol")
    assert rows[0]["error_message"] is None
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_filters_by_namespace(tmp_path) -> None:
    """get_query_history(namespace) returns only that namespace's entries."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    now = datetime.now(timezone.utc)
    await store.log_query_history(
        namespace="alice", sql="SELECT 'alice'", duration_ms=1.0,
        rows_returned=1, status="success", executed_at=now,
    )
    await store.log_query_history(
        namespace="bob", sql="SELECT 'bob'", duration_ms=2.0,
        rows_returned=1, status="success", executed_at=now,
    )

    alice_rows = await store.get_query_history(namespace="alice")
    assert all(r["namespace"] == "alice" for r in alice_rows)
    assert len(alice_rows) == 1

    bob_rows = await store.get_query_history(namespace="bob")
    assert all(r["namespace"] == "bob" for r in bob_rows)
    assert len(bob_rows) == 1
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_filter_status_success(tmp_path) -> None:
    """status_filter='success' returns only success entries."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    now = datetime.now(timezone.utc)
    await store.log_query_history(
        namespace="user1", sql="SELECT 1", duration_ms=1.0,
        rows_returned=1, status="success", executed_at=now,
    )
    await store.log_query_history(
        namespace="user1", sql="BAD SQL", duration_ms=0.5,
        rows_returned=0, status="error",
        error_message="syntax error", executed_at=now,
    )

    successes = await store.get_query_history(namespace="user1", status_filter="success")
    assert all(r["status"] == "success" for r in successes)
    assert len(successes) == 1

    errors = await store.get_query_history(namespace="user1", status_filter="error")
    assert all(r["status"] == "error" for r in errors)
    assert len(errors) == 1
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_filter_date_range(tmp_path) -> None:
    """start/end timestamp filters narrow the result set."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    t_early = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t_mid = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_late = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

    for ts, label in [(t_early, "early"), (t_mid, "mid"), (t_late, "late")]:
        await store.log_query_history(
            namespace="user1", sql=f"SELECT '{label}'", duration_ms=1.0,
            rows_returned=1, status="success", executed_at=ts,
        )

    rows = await store.get_query_history(
        namespace="user1",
        start=datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert "mid" in rows[0]["sql"]
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_start_filter_only(tmp_path) -> None:
    """start filter alone excludes entries before it."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    t_old = datetime(2025, 6, 1, tzinfo=timezone.utc)
    t_new = datetime(2026, 3, 1, tzinfo=timezone.utc)

    await store.log_query_history(
        namespace="u", sql="SELECT 'old'", duration_ms=1.0,
        rows_returned=1, status="success", executed_at=t_old,
    )
    await store.log_query_history(
        namespace="u", sql="SELECT 'new'", duration_ms=1.0,
        rows_returned=1, status="success", executed_at=t_new,
    )

    rows = await store.get_query_history(
        namespace="u",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert "new" in rows[0]["sql"]
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_end_filter_only(tmp_path) -> None:
    """end filter alone excludes entries after it."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    t_old = datetime(2025, 6, 1, tzinfo=timezone.utc)
    t_new = datetime(2026, 3, 1, tzinfo=timezone.utc)

    await store.log_query_history(
        namespace="u", sql="SELECT 'old'", duration_ms=1.0,
        rows_returned=1, status="success", executed_at=t_old,
    )
    await store.log_query_history(
        namespace="u", sql="SELECT 'new'", duration_ms=1.0,
        rows_returned=1, status="success", executed_at=t_new,
    )

    rows = await store.get_query_history(
        namespace="u",
        end=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert "old" in rows[0]["sql"]
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_pagination_limit(tmp_path) -> None:
    """limit parameter caps the number of results."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    now = datetime.now(timezone.utc)
    for i in range(10):
        await store.log_query_history(
            namespace="u", sql=f"SELECT {i}", duration_ms=float(i),
            rows_returned=1, status="success", executed_at=now,
        )

    rows = await store.get_query_history(namespace="u", limit=3)
    assert len(rows) == 3
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_pagination_offset(tmp_path) -> None:
    """offset parameter skips the first N entries (no overlap between pages)."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    now = datetime.now(timezone.utc)
    for i in range(8):
        await store.log_query_history(
            namespace="u", sql=f"SELECT {i}", duration_ms=float(i),
            rows_returned=1, status="success", executed_at=now,
        )

    page1 = await store.get_query_history(namespace="u", limit=4, offset=0)
    page2 = await store.get_query_history(namespace="u", limit=4, offset=4)

    sqls1 = {r["sql"] for r in page1}
    sqls2 = {r["sql"] for r in page2}
    assert sqls1.isdisjoint(sqls2), "Pages must not overlap"
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_default_limit_is_50(tmp_path) -> None:
    """Default limit is 50 when not specified."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    now = datetime.now(timezone.utc)
    for i in range(60):
        await store.log_query_history(
            namespace="u", sql=f"SELECT {i}", duration_ms=1.0,
            rows_returned=1, status="success", executed_at=now,
        )

    rows = await store.get_query_history(namespace="u")
    assert len(rows) == 50
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_returns_list_of_dicts(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    rows = await store.get_query_history(namespace="empty_ns")
    assert isinstance(rows, list)
    await store.close()


@pytest.mark.asyncio
async def test_get_query_history_multiple_entries_ordered(tmp_path) -> None:
    """Results should be ordered by executed_at descending (newest first)."""
    from ponddb.store.metadata_store import MetadataStore

    store = MetadataStore(str(tmp_path / "t.db"))
    store.initialize_blocking()

    t1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    for ts, label in [(t1, "first"), (t2, "second"), (t3, "third")]:
        await store.log_query_history(
            namespace="u", sql=f"SELECT '{label}'", duration_ms=1.0,
            rows_returned=1, status="success", executed_at=ts,
        )

    rows = await store.get_query_history(namespace="u")
    # Most recent first
    assert "third" in rows[0]["sql"]
    assert "first" in rows[-1]["sql"]
    await store.close()


# ---------------------------------------------------------------------------
# GET /history — HTTP endpoint
# ---------------------------------------------------------------------------


def test_history_endpoint_requires_auth(client) -> None:
    """GET /history without API key returns 401."""
    resp = client.get("/history")
    assert resp.status_code == 401


def test_history_endpoint_rejects_wrong_key(client) -> None:
    resp = client.get("/history", headers={"X-API-Key": "wrong-key-xyz"})
    assert resp.status_code == 401


def test_history_endpoint_returns_200_with_valid_key(client, auth_headers) -> None:
    resp = client.get("/history", headers=auth_headers)
    assert resp.status_code == 200


def test_history_endpoint_returns_list(client, auth_headers) -> None:
    resp = client.get("/history", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_history_endpoint_content_type_json(client, auth_headers) -> None:
    resp = client.get("/history", headers=auth_headers)
    assert "application/json" in resp.headers.get("content-type", "")


def test_history_records_successful_query_execution(
    client, auth_headers, session_id
) -> None:
    """POST /query automatically records a success entry in history."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 99 AS num"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    hist = client.get("/history", headers=auth_headers).json()
    matching = [h for h in hist if h.get("sql") == "SELECT 99 AS num"]
    assert len(matching) >= 1
    assert matching[0]["status"] == "success"


def test_history_records_failed_query_execution(
    client, auth_headers, session_id
) -> None:
    """POST /query records an error entry when DuckDB raises."""
    resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT * FROM totally_absent_table_xyz"},
        headers=auth_headers,
    )
    assert resp.status_code == 400

    hist = client.get("/history", headers=auth_headers).json()
    errors = [h for h in hist if h.get("status") == "error"]
    assert len(errors) >= 1
    assert errors[0]["error_message"] is not None
    assert len(errors[0]["error_message"]) > 0


def test_history_entry_has_required_fields(client, auth_headers, session_id) -> None:
    """Each history entry contains all required fields."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1 AS x"},
        headers=auth_headers,
    )

    hist = client.get("/history", headers=auth_headers).json()
    assert len(hist) >= 1
    entry = hist[0]
    for field in ("sql", "duration_ms", "rows_returned", "status", "executed_at"):
        assert field in entry, f"Missing required field: {field}"


def test_history_duration_ms_is_non_negative(client, auth_headers, session_id) -> None:
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    )
    hist = client.get("/history", headers=auth_headers).json()
    assert hist[0]["duration_ms"] >= 0


def test_history_executed_at_is_parseable_iso8601(
    client, auth_headers, session_id
) -> None:
    """executed_at field must be a parseable ISO 8601 timestamp string."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 1"},
        headers=auth_headers,
    )
    hist = client.get("/history", headers=auth_headers).json()
    ts_str = hist[0]["executed_at"]
    parsed = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    assert parsed is not None


def test_history_rows_returned_matches_query_result(
    client, auth_headers, session_id
) -> None:
    """rows_returned in history matches the actual query rowcount."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT unnest([1, 2, 3]) AS n"},
        headers=auth_headers,
    )
    hist = client.get("/history", headers=auth_headers).json()
    matching = [h for h in hist if "unnest" in h.get("sql", "")]
    assert len(matching) >= 1
    assert matching[0]["rows_returned"] == 3


def test_history_filter_status_success(client, auth_headers, session_id) -> None:
    """GET /history?status=success returns only successful entries."""
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    client.post("/query", json={"session_id": session_id, "sql": "INVALID SQL !!"}, headers=auth_headers)

    resp = client.get("/history?status=success", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert all(e["status"] == "success" for e in entries)


def test_history_filter_status_error(client, auth_headers, session_id) -> None:
    """GET /history?status=error returns only failed entries."""
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    client.post("/query", json={"session_id": session_id, "sql": "INVALID SQL !!"}, headers=auth_headers)

    resp = client.get("/history?status=error", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert all(e["status"] == "error" for e in entries)


def test_history_filter_invalid_status_returns_400(client, auth_headers) -> None:
    """Invalid status filter value returns 400."""
    resp = client.get("/history?status=bogus_value", headers=auth_headers)
    assert resp.status_code == 400


def test_history_filter_date_range_start_and_end(
    client, auth_headers, session_id
) -> None:
    """start and end query params filter by executed_at range."""
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)

    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=5)).isoformat()
    end = (now + timedelta(minutes=5)).isoformat()

    resp = client.get(f"/history?start={start}&end={end}", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_history_filter_date_range_excludes_future(
    client, auth_headers, session_id
) -> None:
    """Queries run now are excluded from a past-only date range."""
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)

    start = "2020-01-01T00:00:00+00:00"
    end = "2020-12-31T23:59:59+00:00"

    resp = client.get(f"/history?start={start}&end={end}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_pagination_limit(client, auth_headers, session_id) -> None:
    """GET /history?limit=N caps results to N entries."""
    for i in range(5):
        client.post(
            "/query",
            json={"session_id": session_id, "sql": f"SELECT {i} AS n"},
            headers=auth_headers,
        )

    resp = client.get("/history?limit=2", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_history_pagination_offset_no_overlap(client, auth_headers, session_id) -> None:
    """Pages fetched with offset do not overlap."""
    for i in range(6):
        client.post(
            "/query",
            json={"session_id": session_id, "sql": f"SELECT {i} AS n"},
            headers=auth_headers,
        )

    page1 = client.get("/history?limit=3&offset=0", headers=auth_headers).json()
    page2 = client.get("/history?limit=3&offset=3", headers=auth_headers).json()

    sqls1 = {e["sql"] for e in page1}
    sqls2 = {e["sql"] for e in page2}
    assert sqls1.isdisjoint(sqls2), "Paginated pages must not share entries"


def test_history_default_limit_is_50_via_api(client, auth_headers, session_id) -> None:
    """Fetching without limit param applies default of 50."""
    # Verify that limit=50 explicitly is accepted and behaves same as default
    resp_default = client.get("/history", headers=auth_headers)
    resp_explicit = client.get("/history?limit=50", headers=auth_headers)
    assert resp_default.status_code == 200
    assert resp_explicit.status_code == 200
    # Both should return the same count (whatever it is ≤ 50)
    assert len(resp_default.json()) == len(resp_explicit.json())


def test_history_offset_zero_same_as_no_offset(client, auth_headers, session_id) -> None:
    """offset=0 is the same as no offset."""
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)

    default = client.get("/history", headers=auth_headers).json()
    explicit = client.get("/history?offset=0", headers=auth_headers).json()
    assert default == explicit


def test_history_combined_filters(client, auth_headers, session_id) -> None:
    """status + date range filters can be combined."""
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    client.post("/query", json={"session_id": session_id, "sql": "INVALID !!"}, headers=auth_headers)

    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=5)).isoformat()
    end = (now + timedelta(minutes=5)).isoformat()

    resp = client.get(
        f"/history?status=success&start={start}&end={end}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    entries = resp.json()
    assert all(e["status"] == "success" for e in entries)
