"""Tests for ponddb.metadata_store — SQLite persistence layer via aiosqlite.

Defines expected behavior for MetadataStore:
  - __init__(db_path) + initialize() creates tables
  - save_session() upserts session rows
  - load_sessions() returns non-destroyed sessions
  - delete_session() removes a session (or marks destroyed)
  - save_mount() persists catalog mount entries
  - list_mounts() returns mounts for a session
  - delete_mounts() removes all mounts for a session

Tests FAIL until ponddb/metadata_store.py is implemented.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path):
    """Fresh in-memory (tmp_path) MetadataStore for each test."""
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "test.db")
    s = MetadataStore(db_path=db_path)
    await s.initialize()
    yield s
    await s.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# initialize — schema creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_creates_sessions_table(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "init.db")
    store = MetadataStore(db_path=db_path)
    await store.initialize()

    # Must not raise — table exists
    sessions = await store.load_sessions()
    assert isinstance(sessions, list)
    await store.close()


@pytest.mark.asyncio
async def test_initialize_creates_catalog_mounts_table(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "init2.db")
    store = MetadataStore(db_path=db_path)
    await store.initialize()

    mounts = await store.list_mounts("any-session-id")
    assert isinstance(mounts, list)
    await store.close()


@pytest.mark.asyncio
async def test_initialize_is_idempotent(tmp_path) -> None:
    """Calling initialize() twice must not raise."""
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "idem.db")
    store = MetadataStore(db_path=db_path)
    await store.initialize()
    await store.initialize()  # must not raise
    await store.close()


# ---------------------------------------------------------------------------
# save_session — insert and upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_session_inserts_row(store) -> None:
    now = _now()
    await store.save_session(
        session_id="sid-1",
        namespace="default",
        state="ACTIVE",
        created_at=now,
        last_active=now,
    )
    sessions = await store.load_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sid-1"


@pytest.mark.asyncio
async def test_save_session_stores_namespace(store) -> None:
    now = _now()
    await store.save_session("sid-2", "team-alpha", "ACTIVE", now, now)
    sessions = await store.load_sessions()
    assert sessions[0]["namespace"] == "team-alpha"


@pytest.mark.asyncio
async def test_save_session_stores_state(store) -> None:
    now = _now()
    await store.save_session("sid-3", "ns", "SUSPENDED", now, now)
    sessions = await store.load_sessions()
    assert sessions[0]["state"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_save_session_stores_created_at(store) -> None:
    now = _now()
    await store.save_session("sid-4", "ns", "ACTIVE", now, now)
    sessions = await store.load_sessions()
    row = sessions[0]
    assert "created_at" in row
    assert row["created_at"] is not None


@pytest.mark.asyncio
async def test_save_session_stores_last_active(store) -> None:
    now = _now()
    await store.save_session("sid-5", "ns", "ACTIVE", now, now)
    sessions = await store.load_sessions()
    row = sessions[0]
    assert "last_active" in row
    assert row["last_active"] is not None


@pytest.mark.asyncio
async def test_save_session_upsert_updates_state(store) -> None:
    """save_session() called twice with same id must update (upsert), not duplicate."""
    now = _now()
    await store.save_session("sid-u", "ns", "ACTIVE", now, now)
    await store.save_session("sid-u", "ns", "SUSPENDED", now, now)

    sessions = await store.load_sessions()
    assert len(sessions) == 1
    assert sessions[0]["state"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_save_session_upsert_updates_last_active(store) -> None:
    t1 = _now()
    await store.save_session("sid-u2", "ns", "ACTIVE", t1, t1)

    import asyncio as _asyncio

    await _asyncio.sleep(0.01)
    t2 = _now()
    await store.save_session("sid-u2", "ns", "ACTIVE", t1, t2)

    sessions = await store.load_sessions()
    assert len(sessions) == 1
    # last_active should be the newer timestamp (stored as string or datetime)
    row = sessions[0]
    assert row["last_active"] is not None


@pytest.mark.asyncio
async def test_save_multiple_sessions(store) -> None:
    now = _now()
    for i in range(5):
        await store.save_session(f"sid-{i}", "ns", "ACTIVE", now, now)
    sessions = await store.load_sessions()
    assert len(sessions) == 5


# ---------------------------------------------------------------------------
# load_sessions — filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_sessions_empty_initially(store) -> None:
    sessions = await store.load_sessions()
    assert sessions == []


@pytest.mark.asyncio
async def test_load_sessions_excludes_destroyed(store) -> None:
    now = _now()
    await store.save_session("alive", "ns", "ACTIVE", now, now)
    await store.save_session("dead", "ns", "DESTROYED", now, now)

    sessions = await store.load_sessions()
    ids = [s["session_id"] for s in sessions]
    assert "alive" in ids
    assert "dead" not in ids


@pytest.mark.asyncio
async def test_load_sessions_includes_active(store) -> None:
    now = _now()
    await store.save_session("s-active", "ns", "ACTIVE", now, now)
    sessions = await store.load_sessions()
    assert any(s["session_id"] == "s-active" for s in sessions)


@pytest.mark.asyncio
async def test_load_sessions_includes_suspended(store) -> None:
    now = _now()
    await store.save_session("s-susp", "ns", "SUSPENDED", now, now)
    sessions = await store.load_sessions()
    assert any(s["session_id"] == "s-susp" for s in sessions)


@pytest.mark.asyncio
async def test_load_sessions_returns_list_of_dicts(store) -> None:
    now = _now()
    await store.save_session("s-dict", "ns", "ACTIVE", now, now)
    sessions = await store.load_sessions()
    assert isinstance(sessions, list)
    assert all(isinstance(s, dict) for s in sessions)


@pytest.mark.asyncio
async def test_load_sessions_each_row_has_required_keys(store) -> None:
    now = _now()
    await store.save_session("s-keys", "myns", "ACTIVE", now, now)
    sessions = await store.load_sessions()
    row = sessions[0]
    assert "session_id" in row
    assert "namespace" in row
    assert "state" in row
    assert "created_at" in row
    assert "last_active" in row


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_removes_from_load(store) -> None:
    now = _now()
    await store.save_session("to-delete", "ns", "ACTIVE", now, now)
    await store.delete_session("to-delete")

    sessions = await store.load_sessions()
    ids = [s["session_id"] for s in sessions]
    assert "to-delete" not in ids


@pytest.mark.asyncio
async def test_delete_session_nonexistent_does_not_raise(store) -> None:
    """Deleting a session that doesn't exist must be a no-op."""
    await store.delete_session("ghost-session-xyz")  # must not raise


@pytest.mark.asyncio
async def test_delete_session_does_not_affect_others(store) -> None:
    now = _now()
    await store.save_session("keep-me", "ns", "ACTIVE", now, now)
    await store.save_session("delete-me", "ns", "ACTIVE", now, now)
    await store.delete_session("delete-me")

    sessions = await store.load_sessions()
    ids = [s["session_id"] for s in sessions]
    assert "keep-me" in ids
    assert "delete-me" not in ids


# ---------------------------------------------------------------------------
# save_mount / list_mounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_mount_persists_entry(store) -> None:
    now = _now()
    await store.save_session("sm-1", "ns", "ACTIVE", now, now)
    await store.save_mount(
        session_id="sm-1",
        path="/data/file.parquet",
        alias="myfile",
        mount_type="parquet",
    )
    mounts = await store.list_mounts("sm-1")
    assert len(mounts) == 1


@pytest.mark.asyncio
async def test_save_mount_stores_path(store) -> None:
    now = _now()
    await store.save_session("sm-2", "ns", "ACTIVE", now, now)
    await store.save_mount("sm-2", "/tmp/data.csv", "data", "csv")
    mounts = await store.list_mounts("sm-2")
    assert mounts[0]["path"] == "/tmp/data.csv"


@pytest.mark.asyncio
async def test_save_mount_stores_alias(store) -> None:
    now = _now()
    await store.save_session("sm-3", "ns", "ACTIVE", now, now)
    await store.save_mount("sm-3", "/tmp/x.csv", "myalias", "csv")
    mounts = await store.list_mounts("sm-3")
    assert mounts[0]["alias"] == "myalias"


@pytest.mark.asyncio
async def test_save_mount_stores_mount_type(store) -> None:
    now = _now()
    await store.save_session("sm-4", "ns", "ACTIVE", now, now)
    await store.save_mount("sm-4", "/tmp/x.parquet", "pq", "parquet")
    mounts = await store.list_mounts("sm-4")
    assert mounts[0]["mount_type"] == "parquet"


@pytest.mark.asyncio
async def test_list_mounts_empty_for_unknown_session(store) -> None:
    mounts = await store.list_mounts("nonexistent-session")
    assert mounts == []


@pytest.mark.asyncio
async def test_list_mounts_only_returns_own_mounts(store) -> None:
    now = _now()
    await store.save_session("owner", "ns", "ACTIVE", now, now)
    await store.save_session("other", "ns", "ACTIVE", now, now)
    await store.save_mount("owner", "/data/a.csv", "a", "csv")
    await store.save_mount("other", "/data/b.csv", "b", "csv")

    mounts = await store.list_mounts("owner")
    assert len(mounts) == 1
    assert mounts[0]["alias"] == "a"


@pytest.mark.asyncio
async def test_save_multiple_mounts_for_session(store) -> None:
    now = _now()
    await store.save_session("multi", "ns", "ACTIVE", now, now)
    await store.save_mount("multi", "/data/a.csv", "a", "csv")
    await store.save_mount("multi", "/data/b.parquet", "b", "parquet")
    await store.save_mount("multi", "/data/c.json", "c", "json")
    mounts = await store.list_mounts("multi")
    assert len(mounts) == 3


@pytest.mark.asyncio
async def test_list_mounts_returns_list_of_dicts(store) -> None:
    now = _now()
    await store.save_session("sm-dict", "ns", "ACTIVE", now, now)
    await store.save_mount("sm-dict", "/data/x.csv", "x", "csv")
    mounts = await store.list_mounts("sm-dict")
    assert isinstance(mounts, list)
    assert all(isinstance(m, dict) for m in mounts)


@pytest.mark.asyncio
async def test_list_mounts_row_has_required_keys(store) -> None:
    now = _now()
    await store.save_session("sm-keys", "ns", "ACTIVE", now, now)
    await store.save_mount("sm-keys", "/data/x.csv", "x", "csv")
    mounts = await store.list_mounts("sm-keys")
    row = mounts[0]
    assert "session_id" in row
    assert "path" in row
    assert "alias" in row
    assert "mount_type" in row


# ---------------------------------------------------------------------------
# delete_mounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_mounts_removes_all_for_session(store) -> None:
    now = _now()
    await store.save_session("dm-1", "ns", "ACTIVE", now, now)
    await store.save_mount("dm-1", "/a.csv", "a", "csv")
    await store.save_mount("dm-1", "/b.csv", "b", "csv")
    await store.delete_mounts("dm-1")
    mounts = await store.list_mounts("dm-1")
    assert mounts == []


@pytest.mark.asyncio
async def test_delete_mounts_nonexistent_session_does_not_raise(store) -> None:
    await store.delete_mounts("ghost-session")  # must not raise


@pytest.mark.asyncio
async def test_delete_mounts_does_not_affect_other_sessions(store) -> None:
    now = _now()
    await store.save_session("keep", "ns", "ACTIVE", now, now)
    await store.save_session("clean", "ns", "ACTIVE", now, now)
    await store.save_mount("keep", "/x.csv", "x", "csv")
    await store.save_mount("clean", "/y.csv", "y", "csv")
    await store.delete_mounts("clean")

    mounts_keep = await store.list_mounts("keep")
    assert len(mounts_keep) == 1


# ---------------------------------------------------------------------------
# Persistence across store instances (same db_path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_persists_across_store_instances(tmp_path) -> None:
    """Opening a second MetadataStore on same file must see previously saved data."""
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "persist.db")
    now = _now()

    store1 = MetadataStore(db_path=db_path)
    await store1.initialize()
    await store1.save_session("persisted-sid", "ns", "ACTIVE", now, now)
    await store1.close()

    store2 = MetadataStore(db_path=db_path)
    await store2.initialize()
    sessions = await store2.load_sessions()
    await store2.close()

    ids = [s["session_id"] for s in sessions]
    assert "persisted-sid" in ids


@pytest.mark.asyncio
async def test_mount_persists_across_store_instances(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "persist2.db")
    now = _now()

    store1 = MetadataStore(db_path=db_path)
    await store1.initialize()
    await store1.save_session("s-persist", "ns", "ACTIVE", now, now)
    await store1.save_mount("s-persist", "/data/file.csv", "f", "csv")
    await store1.close()

    store2 = MetadataStore(db_path=db_path)
    await store2.initialize()
    mounts = await store2.list_mounts("s-persist")
    await store2.close()

    assert len(mounts) == 1
    assert mounts[0]["path"] == "/data/file.csv"
