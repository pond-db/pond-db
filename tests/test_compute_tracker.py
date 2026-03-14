"""Tests for the compute tracker module.

Defines expected behavior for:
  - ComputeSample dataclass (fields, types)
  - ComputeTracker.track_query() — wall-time + memory measurement
  - query_hash derivation from SQL text
  - SQLite compute_log persistence via MetadataStore
"""

import hashlib
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

import duckdb
import pytest

from ponddb.compute_tracker import ComputeSample, ComputeTracker
from ponddb.metadata_store import MetadataStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def duck_conn():
    """In-memory DuckDB connection."""
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def tmp_db(tmp_path):
    """Temporary SQLite path."""
    return str(tmp_path / "test_pond.db")


@pytest.fixture
def store(tmp_db):
    """Initialized MetadataStore."""
    s = MetadataStore(tmp_db)
    # drive initialize synchronously
    coro = s.initialize()
    try:
        coro.send(None)
    except StopIteration:
        pass
    yield s
    coro = s.close()
    try:
        coro.send(None)
    except StopIteration:
        pass


@pytest.fixture
def tracker(store):
    """ComputeTracker backed by the test store."""
    return ComputeTracker(store=store)


# ---------------------------------------------------------------------------
# ComputeSample — dataclass shape
# ---------------------------------------------------------------------------


def test_compute_sample_has_session_id():
    s = ComputeSample(
        session_id="sid-1",
        query_hash="abc123",
        wall_ms=10.5,
        mem_delta_kb=128.0,
        timestamp=datetime.now(timezone.utc),
    )
    assert s.session_id == "sid-1"


def test_compute_sample_has_query_hash():
    s = ComputeSample(
        session_id="sid-1",
        query_hash="abc123",
        wall_ms=10.5,
        mem_delta_kb=128.0,
        timestamp=datetime.now(timezone.utc),
    )
    assert s.query_hash == "abc123"


def test_compute_sample_has_wall_ms():
    s = ComputeSample(
        session_id="sid-1",
        query_hash="abc123",
        wall_ms=42.0,
        mem_delta_kb=0.0,
        timestamp=datetime.now(timezone.utc),
    )
    assert s.wall_ms == 42.0


def test_compute_sample_has_mem_delta_kb():
    s = ComputeSample(
        session_id="sid-1",
        query_hash="abc123",
        wall_ms=1.0,
        mem_delta_kb=256.0,
        timestamp=datetime.now(timezone.utc),
    )
    assert s.mem_delta_kb == 256.0


def test_compute_sample_has_timestamp():
    now = datetime.now(timezone.utc)
    s = ComputeSample(
        session_id="sid-1",
        query_hash="abc123",
        wall_ms=1.0,
        mem_delta_kb=0.0,
        timestamp=now,
    )
    assert s.timestamp == now


def test_compute_sample_timestamp_is_datetime():
    s = ComputeSample(
        session_id="s",
        query_hash="h",
        wall_ms=1.0,
        mem_delta_kb=0.0,
        timestamp=datetime.now(timezone.utc),
    )
    assert isinstance(s.timestamp, datetime)


# ---------------------------------------------------------------------------
# ComputeTracker — instantiation
# ---------------------------------------------------------------------------


def test_compute_tracker_instantiates_without_store():
    t = ComputeTracker()
    assert t is not None


def test_compute_tracker_instantiates_with_store(store):
    t = ComputeTracker(store=store)
    assert t is not None


# ---------------------------------------------------------------------------
# ComputeTracker.track_query() — happy path
# ---------------------------------------------------------------------------


def test_track_query_returns_compute_sample(tracker, duck_conn):
    sample = tracker.track_query(
        session_id="s1",
        sql="SELECT 1 AS n",
        conn=duck_conn,
    )
    assert isinstance(sample, ComputeSample)


def test_track_query_sample_has_correct_session_id(tracker, duck_conn):
    sample = tracker.track_query(session_id="my-session", sql="SELECT 1", conn=duck_conn)
    assert sample.session_id == "my-session"


def test_track_query_wall_ms_is_positive(tracker, duck_conn):
    sample = tracker.track_query(session_id="s1", sql="SELECT 42", conn=duck_conn)
    assert sample.wall_ms > 0


def test_track_query_wall_ms_is_float(tracker, duck_conn):
    sample = tracker.track_query(session_id="s1", sql="SELECT 1", conn=duck_conn)
    assert isinstance(sample.wall_ms, float)


def test_track_query_wall_ms_is_reasonable(tracker, duck_conn):
    """Simple in-memory query should finish well under 5 seconds."""
    sample = tracker.track_query(session_id="s1", sql="SELECT 1", conn=duck_conn)
    assert sample.wall_ms < 5000.0


def test_track_query_mem_delta_kb_is_numeric(tracker, duck_conn):
    sample = tracker.track_query(session_id="s1", sql="SELECT 1", conn=duck_conn)
    assert isinstance(sample.mem_delta_kb, (int, float))


def test_track_query_timestamp_is_set(tracker, duck_conn):
    before = datetime.now(timezone.utc)
    sample = tracker.track_query(session_id="s1", sql="SELECT 1", conn=duck_conn)
    after = datetime.now(timezone.utc)
    assert before <= sample.timestamp <= after


def test_track_query_timestamp_is_utc(tracker, duck_conn):
    sample = tracker.track_query(session_id="s1", sql="SELECT 1", conn=duck_conn)
    assert sample.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# ComputeTracker — query_hash derivation
# ---------------------------------------------------------------------------


def test_track_query_hash_is_hex_string(tracker, duck_conn):
    sample = tracker.track_query(session_id="s1", sql="SELECT 1", conn=duck_conn)
    assert isinstance(sample.query_hash, str)
    assert len(sample.query_hash) > 0


def test_track_query_same_sql_same_hash(tracker, duck_conn):
    s1 = tracker.track_query(session_id="a", sql="SELECT 1 AS n", conn=duck_conn)
    s2 = tracker.track_query(session_id="b", sql="SELECT 1 AS n", conn=duck_conn)
    assert s1.query_hash == s2.query_hash


def test_track_query_different_sql_different_hash(tracker, duck_conn):
    s1 = tracker.track_query(session_id="a", sql="SELECT 1", conn=duck_conn)
    s2 = tracker.track_query(session_id="a", sql="SELECT 2", conn=duck_conn)
    assert s1.query_hash != s2.query_hash


def test_query_hash_matches_sha256_of_sql(tracker, duck_conn):
    sql = "SELECT 99 AS answer"
    sample = tracker.track_query(session_id="s1", sql=sql, conn=duck_conn)
    expected = hashlib.sha256(sql.encode()).hexdigest()
    assert sample.query_hash == expected


# ---------------------------------------------------------------------------
# ComputeTracker — measures actual execution time
# ---------------------------------------------------------------------------


def test_slower_query_has_larger_wall_ms(tracker, duck_conn):
    """A query that does more work should report higher wall_ms."""
    fast = tracker.track_query(session_id="s", sql="SELECT 1", conn=duck_conn)
    # Generate a moderately sized cross-join to burn some CPU
    slow = tracker.track_query(
        session_id="s",
        sql="SELECT count(*) FROM range(10000) a, range(100) b",
        conn=duck_conn,
    )
    assert slow.wall_ms >= fast.wall_ms


# ---------------------------------------------------------------------------
# ComputeTracker — SQLite persistence
# ---------------------------------------------------------------------------


def test_track_query_persists_to_compute_log(tracker, duck_conn, store):
    tracker.track_query(session_id="sess-persist", sql="SELECT 7", conn=duck_conn)
    # Read directly from SQLite to verify persistence
    rows = _read_compute_log(store.db_path)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "sess-persist"


def test_track_query_persists_wall_ms(tracker, duck_conn, store):
    tracker.track_query(session_id="s", sql="SELECT 1", conn=duck_conn)
    rows = _read_compute_log(store.db_path)
    assert rows[0]["wall_ms"] > 0


def test_track_query_persists_query_hash(tracker, duck_conn, store):
    sql = "SELECT 55"
    tracker.track_query(session_id="s", sql=sql, conn=duck_conn)
    rows = _read_compute_log(store.db_path)
    expected_hash = hashlib.sha256(sql.encode()).hexdigest()
    assert rows[0]["query_hash"] == expected_hash


def test_track_query_persists_mem_delta_kb(tracker, duck_conn, store):
    tracker.track_query(session_id="s", sql="SELECT 1", conn=duck_conn)
    rows = _read_compute_log(store.db_path)
    assert "mem_delta_kb" in rows[0]


def test_track_query_persists_timestamp(tracker, duck_conn, store):
    tracker.track_query(session_id="s", sql="SELECT 1", conn=duck_conn)
    rows = _read_compute_log(store.db_path)
    assert rows[0]["timestamp"] is not None


def test_multiple_queries_accumulate_in_compute_log(tracker, duck_conn, store):
    tracker.track_query(session_id="s", sql="SELECT 1", conn=duck_conn)
    tracker.track_query(session_id="s", sql="SELECT 2", conn=duck_conn)
    tracker.track_query(session_id="s", sql="SELECT 3", conn=duck_conn)
    rows = _read_compute_log(store.db_path)
    assert len(rows) == 3


def test_compute_log_records_different_sessions(tracker, duck_conn, store):
    tracker.track_query(session_id="sess-A", sql="SELECT 1", conn=duck_conn)
    tracker.track_query(session_id="sess-B", sql="SELECT 2", conn=duck_conn)
    rows = _read_compute_log(store.db_path)
    session_ids = {r["session_id"] for r in rows}
    assert "sess-A" in session_ids
    assert "sess-B" in session_ids


# ---------------------------------------------------------------------------
# ComputeTracker — no store (fire and forget gracefully)
# ---------------------------------------------------------------------------


def test_track_query_without_store_still_returns_sample(duck_conn):
    tracker = ComputeTracker(store=None)
    sample = tracker.track_query(session_id="s", sql="SELECT 1", conn=duck_conn)
    assert isinstance(sample, ComputeSample)


# ---------------------------------------------------------------------------
# MetadataStore — compute_log table schema
# ---------------------------------------------------------------------------


def test_metadata_store_creates_compute_log_table(store):
    conn = sqlite3.connect(store.db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='compute_log'"
    )
    row = cursor.fetchone()
    conn.close()
    assert row is not None, "compute_log table should exist after initialize()"


def test_compute_log_table_has_required_columns(store):
    conn = sqlite3.connect(store.db_path)
    cursor = conn.execute("PRAGMA table_info(compute_log)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()
    required = {"session_id", "query_hash", "wall_ms", "mem_delta_kb", "timestamp"}
    assert required.issubset(columns), f"Missing columns: {required - columns}"


# ---------------------------------------------------------------------------
# MetadataStore — log_compute_sample / get_compute_samples
# ---------------------------------------------------------------------------


def test_log_compute_sample_inserts_row(store):
    sample = ComputeSample(
        session_id="s1",
        query_hash="deadbeef",
        wall_ms=33.3,
        mem_delta_kb=64.0,
        timestamp=datetime.now(timezone.utc),
    )
    _drive_coro(store.log_compute_sample(sample))
    rows = _read_compute_log(store.db_path)
    assert len(rows) == 1


def test_get_compute_samples_returns_all(store):
    for i in range(3):
        sample = ComputeSample(
            session_id=f"s{i}",
            query_hash=f"hash{i}",
            wall_ms=float(i),
            mem_delta_kb=0.0,
            timestamp=datetime.now(timezone.utc),
        )
        _drive_coro(store.log_compute_sample(sample))
    rows = _drive_coro_return(store.get_compute_samples())
    assert len(rows) == 3


def test_get_compute_samples_filters_by_session(store):
    for sid in ("alpha", "alpha", "beta"):
        sample = ComputeSample(
            session_id=sid,
            query_hash="h",
            wall_ms=1.0,
            mem_delta_kb=0.0,
            timestamp=datetime.now(timezone.utc),
        )
        _drive_coro(store.log_compute_sample(sample))
    rows = _drive_coro_return(store.get_compute_samples(session_id="alpha"))
    assert len(rows) == 2
    assert all(r["session_id"] == "alpha" for r in rows)


def test_get_compute_samples_returns_dicts(store):
    sample = ComputeSample(
        session_id="s",
        query_hash="h",
        wall_ms=1.0,
        mem_delta_kb=0.0,
        timestamp=datetime.now(timezone.utc),
    )
    _drive_coro(store.log_compute_sample(sample))
    rows = _drive_coro_return(store.get_compute_samples())
    assert isinstance(rows[0], dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_compute_log(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT session_id, query_hash, wall_ms, mem_delta_kb, timestamp FROM compute_log"
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _drive_coro(coro) -> None:
    """Drive a fake-async coroutine (synchronous sqlite3 internals)."""
    try:
        coro.send(None)
    except StopIteration:
        pass


def _drive_coro_return(coro):
    """Drive and capture the return value of a fake-async coroutine."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
