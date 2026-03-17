"""Tests for SessionManager SQLite persistence integration.

Defines expected behavior when SessionManager is wired to a MetadataStore:
  - create_session() → persists to SQLite immediately
  - suspend_session() → state change persisted to SQLite
  - resume_session() → state change persisted to SQLite
  - destroy_session() → row removed from SQLite
  - SessionManager.load_from_store() → repopulates in-memory state from SQLite
  - Catalog mounts persisted via manager.mount_catalog()
  - Mounts loaded back on resume when store is present

Tests FAIL until:
  - ponddb/metadata_store.py is implemented
  - ponddb/session_manager.py accepts a MetadataStore and persists transitions
"""


import pytest
import pytest_asyncio

from ponddb.engine.session_manager import SessionManager, SessionStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path):
    from ponddb.store.metadata_store import MetadataStore

    s = MetadataStore(db_path=str(tmp_path / "test.db"))
    await s.initialize()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def manager(store):
    """SessionManager wired to a fresh MetadataStore."""
    mgr = SessionManager(store=store)
    yield mgr
    for s in mgr.list_sessions():
        try:
            mgr.destroy_session(s["session_id"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# create_session persists to SQLite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_persists_to_store(manager, store) -> None:
    sid = manager.create_session(namespace="ns-test")
    sessions = await store.load_sessions()
    ids = [s["session_id"] for s in sessions]
    assert sid in ids


@pytest.mark.asyncio
async def test_create_session_persists_namespace(manager, store) -> None:
    sid = manager.create_session(namespace="my-namespace")
    sessions = await store.load_sessions()
    row = next(s for s in sessions if s["session_id"] == sid)
    assert row["namespace"] == "my-namespace"


@pytest.mark.asyncio
async def test_create_session_persists_active_state(manager, store) -> None:
    sid = manager.create_session()
    sessions = await store.load_sessions()
    row = next(s for s in sessions if s["session_id"] == sid)
    assert row["state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_create_multiple_sessions_all_persisted(manager, store) -> None:
    sids = [manager.create_session() for _ in range(3)]
    sessions = await store.load_sessions()
    stored_ids = {s["session_id"] for s in sessions}
    for sid in sids:
        assert sid in stored_ids


# ---------------------------------------------------------------------------
# suspend_session persists state change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_persists_suspended_state(manager, store) -> None:
    sid = manager.create_session()
    manager.suspend_session(sid)
    sessions = await store.load_sessions()
    row = next(s for s in sessions if s["session_id"] == sid)
    assert row["state"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_suspend_does_not_remove_from_store(manager, store) -> None:
    sid = manager.create_session()
    manager.suspend_session(sid)
    sessions = await store.load_sessions()
    ids = [s["session_id"] for s in sessions]
    assert sid in ids


# ---------------------------------------------------------------------------
# resume_session persists state change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_persists_active_state(manager, store) -> None:
    sid = manager.create_session()
    manager.suspend_session(sid)
    manager.resume_session(sid)
    sessions = await store.load_sessions()
    row = next(s for s in sessions if s["session_id"] == sid)
    assert row["state"] == "ACTIVE"


# ---------------------------------------------------------------------------
# destroy_session removes from SQLite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destroy_removes_from_store(manager, store) -> None:
    sid = manager.create_session()
    manager.destroy_session(sid)
    sessions = await store.load_sessions()
    ids = [s["session_id"] for s in sessions]
    assert sid not in ids


@pytest.mark.asyncio
async def test_destroy_does_not_affect_sibling_in_store(manager, store) -> None:
    sid_a = manager.create_session()
    sid_b = manager.create_session()
    manager.destroy_session(sid_b)
    sessions = await store.load_sessions()
    ids = [s["session_id"] for s in sessions]
    assert sid_a in ids
    assert sid_b not in ids


# ---------------------------------------------------------------------------
# SessionManager.load_from_store() — restores state on startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_from_store_restores_active_sessions(tmp_path) -> None:
    """A second SessionManager on the same store sees previously created sessions."""
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "restart.db")

    # First manager: create sessions
    store1 = MetadataStore(db_path=db_path)
    await store1.initialize()
    mgr1 = SessionManager(store=store1)
    sid = mgr1.create_session(namespace="restart-ns")
    await store1.close()

    # Second manager: load from same store
    store2 = MetadataStore(db_path=db_path)
    await store2.initialize()
    mgr2 = SessionManager(store=store2)
    await mgr2.load_from_store()

    sessions = mgr2.list_sessions()
    ids = [s["session_id"] for s in sessions]
    assert sid in ids
    await store2.close()


@pytest.mark.asyncio
async def test_load_from_store_restores_suspended_sessions(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "restart2.db")

    store1 = MetadataStore(db_path=db_path)
    await store1.initialize()
    mgr1 = SessionManager(store=store1)
    sid = mgr1.create_session()
    mgr1.suspend_session(sid)
    await store1.close()

    store2 = MetadataStore(db_path=db_path)
    await store2.initialize()
    mgr2 = SessionManager(store=store2)
    await mgr2.load_from_store()

    info = mgr2.get_session(sid)
    assert info["status"] == SessionStatus.SUSPENDED
    await store2.close()


@pytest.mark.asyncio
async def test_load_from_store_does_not_restore_destroyed_sessions(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "restart3.db")

    store1 = MetadataStore(db_path=db_path)
    await store1.initialize()
    mgr1 = SessionManager(store=store1)
    sid = mgr1.create_session()
    mgr1.destroy_session(sid)
    await store1.close()

    store2 = MetadataStore(db_path=db_path)
    await store2.initialize()
    mgr2 = SessionManager(store=store2)
    await mgr2.load_from_store()

    assert mgr2.session_count == 0
    await store2.close()


@pytest.mark.asyncio
async def test_load_from_store_restores_namespace(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "restart4.db")

    store1 = MetadataStore(db_path=db_path)
    await store1.initialize()
    mgr1 = SessionManager(store=store1)
    sid = mgr1.create_session(namespace="preserved-ns")
    await store1.close()

    store2 = MetadataStore(db_path=db_path)
    await store2.initialize()
    mgr2 = SessionManager(store=store2)
    await mgr2.load_from_store()

    info = mgr2.get_session(sid)
    assert info["namespace"] == "preserved-ns"
    await store2.close()


@pytest.mark.asyncio
async def test_load_from_store_restores_multiple_sessions(tmp_path) -> None:
    from ponddb.store.metadata_store import MetadataStore

    db_path = str(tmp_path / "restart5.db")

    store1 = MetadataStore(db_path=db_path)
    await store1.initialize()
    mgr1 = SessionManager(store=store1)
    sids = [mgr1.create_session(namespace=f"ns-{i}") for i in range(4)]
    await store1.close()

    store2 = MetadataStore(db_path=db_path)
    await store2.initialize()
    mgr2 = SessionManager(store=store2)
    await mgr2.load_from_store()

    assert mgr2.session_count == 4
    await store2.close()


# ---------------------------------------------------------------------------
# SessionManager without store — backward compatibility
# ---------------------------------------------------------------------------


def test_session_manager_without_store_still_works() -> None:
    """SessionManager(store=None) must behave exactly as before."""
    mgr = SessionManager()  # no store argument
    sid = mgr.create_session()
    assert mgr.session_count == 1
    mgr.destroy_session(sid)
    assert mgr.session_count == 0


def test_session_manager_default_store_is_none() -> None:
    """When no store is provided, manager.store must be None."""
    mgr = SessionManager()
    assert mgr.store is None


# ---------------------------------------------------------------------------
# list_sessions filtered by namespace (manager-level, not API)
# ---------------------------------------------------------------------------


def test_list_sessions_by_namespace_returns_only_matching(store) -> None:
    """list_sessions(namespace=) must filter by namespace."""
    mgr = SessionManager(store=store)
    sid_a = mgr.create_session(namespace="team-a")
    sid_b = mgr.create_session(namespace="team-b")
    mgr.create_session(namespace="team-a")

    result = mgr.list_sessions(namespace="team-a")
    ids = [s["session_id"] for s in result]
    assert sid_a in ids
    assert sid_b not in ids
    assert len(result) == 2


def test_list_sessions_no_namespace_filter_returns_all(store) -> None:
    mgr = SessionManager(store=store)
    for ns in ("a", "b", "c"):
        mgr.create_session(namespace=ns)
    all_sessions = mgr.list_sessions()
    assert len(all_sessions) == 3


def test_list_sessions_namespace_no_match_returns_empty(store) -> None:
    mgr = SessionManager(store=store)
    mgr.create_session(namespace="existing-ns")
    result = mgr.list_sessions(namespace="no-such-ns")
    assert result == []
