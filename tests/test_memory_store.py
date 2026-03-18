# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Tests for memory store, models, access scope, grants, and background tasks."""

import os

import pytest

# Ensure src is on path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ponddb.memory.store import MemoryStore
from ponddb.memory.access import can_access_memory, get_accessible_workgroups
from ponddb.memory.grants import create_grant, delete_grant, get_grant, list_grants
from ponddb.memory.search import search_memories
from ponddb.memory.access_log import count_recent_actions, get_access_logs, write_access_log
from ponddb.memory.tasks import MemoryCleanupTask, UtilityDecayTask
from ponddb.memory.models import MemoryCreate, MemoryFeedback, GrantCreate


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_memory.db")
    s = MemoryStore(db_path)
    s.initialize_blocking()
    return s


@pytest.fixture
def conn(store):
    return store._conn


WG_ALPHA = "wg-alpha"
WG_BETA = "wg-beta"
WG_GAMMA = "wg-gamma"


# ═══════════════════════════════════════════════════════════════
# WAVE 1: Schema + Models + Access Scope
# ═══════════════════════════════════════════════════════════════


class TestSchema:
    def test_tables_created(self, conn):
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "agent_memories" in tables
        assert "memory_grants" in tables
        assert "memory_access_log" in tables

    def test_indexes_created(self, conn):
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "idx_mem_agent_wg" in indexes
        assert "idx_mem_utility" in indexes
        assert "idx_mem_key_wg" in indexes
        assert "idx_grants_grantor" in indexes
        assert "idx_mal_agent" in indexes

    def test_idempotent_initialization(self, tmp_path):
        db_path = str(tmp_path / "idem.db")
        s1 = MemoryStore(db_path)
        s1.initialize_blocking()
        s2 = MemoryStore(db_path)
        s2.initialize_blocking()  # Should not raise


class TestModels:
    def test_memory_create_valid(self):
        m = MemoryCreate(
            agent_id="agent-1",
            memory_type="semantic",
            content={"fact": "test"},
            access_scope="workgroup",
        )
        assert m.memory_type == "semantic"

    def test_memory_create_invalid_type(self):
        with pytest.raises(Exception):
            MemoryCreate(agent_id="a", memory_type="invalid", content={})

    def test_memory_feedback_bounds(self):
        f = MemoryFeedback(reward=0.5)
        assert f.reward == 0.5
        with pytest.raises(Exception):
            MemoryFeedback(reward=1.5)
        with pytest.raises(Exception):
            MemoryFeedback(reward=-1.5)

    def test_grant_create_valid(self):
        g = GrantCreate(
            grantor_workgroup_id="wg1",
            grantee_workgroup_id="wg2",
            permission="read",
        )
        assert g.permission == "read"


class TestMemoryCRUD:
    def test_create_and_get(self, store):
        m = store.create_memory(
            agent_id="agent-1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"fact": "sky is blue"},
        )
        assert m["agent_id"] == "agent-1"
        assert m["content"] == {"fact": "sky is blue"}
        fetched = store.get_memory(m["id"])
        assert fetched["id"] == m["id"]

    def test_create_all_types(self, store):
        for mtype in ("working", "episodic", "semantic", "procedural", "shared"):
            m = store.create_memory(
                agent_id="a1",
                workgroup_id=WG_ALPHA,
                memory_type=mtype,
                content={"type": mtype},
            )
            assert m["memory_type"] == mtype

    def test_create_with_access_scopes(self, store):
        for scope in ("private", "workgroup", "namespace"):
            m = store.create_memory(
                agent_id="a1",
                workgroup_id=WG_ALPHA,
                memory_type="semantic",
                access_scope=scope,
                content={"scope": scope},
            )
            assert m["access_scope"] == scope

    def test_upsert_via_memory_key(self, store):
        m1 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            memory_key="facts/sky",
            content={"color": "blue"},
        )
        m2 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            memory_key="facts/sky",
            content={"color": "azure"},
        )
        # Same ID returned (upsert)
        assert m2["id"] == m1["id"]
        assert m2["content"] == {"color": "azure"}

    def test_update_content_and_importance(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"v": 1},
            importance=0.5,
        )
        updated = store.update_memory(m["id"], content={"v": 2}, importance=0.8)
        assert updated["content"] == {"v": 2}
        assert updated["importance"] == 0.8

    def test_soft_delete(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        result = store.soft_delete_memory(m["id"])
        assert result["deleted_at"] is not None
        # get_memory returns None for deleted
        assert store.get_memory(m["id"]) is None

    def test_utility_feedback(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        assert m["utility"] == 0.5
        result = store.update_utility(m["id"], reward=0.8)
        # utility = 0.5 + 0.1*(0.8-0.5) = 0.53
        assert abs(result["new_utility"] - 0.53) < 0.01

    def test_utility_clamped(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        # Push utility high
        for _ in range(50):
            store.update_utility(m["id"], reward=1.0)
        fetched = store.get_memory(m["id"])
        assert fetched["utility"] <= 0.9
        # Push utility low
        for _ in range(50):
            store.update_utility(m["id"], reward=-1.0)
        fetched = store.get_memory(m["id"])
        assert fetched["utility"] >= 0.1

    def test_causal_cycle_detection(self, store):
        m1 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="episodic",
            content={"step": 1},
        )
        m2 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="episodic",
            content={"step": 2},
            causal_parent_id=m1["id"],
        )
        # m2→m1 chain exists. If new m3 has parent=m2, that's fine (no cycle)
        assert store.check_causal_cycle(m2["id"]) is False
        # If we tried to set m1.parent=m2, that would create m1→m2→m1 cycle
        # check_causal_cycle(parent_id=m2, new_id=m1) should detect it
        assert store.check_causal_cycle(m2["id"], m1["id"]) is True
        # Self-reference should be caught
        assert store.check_causal_cycle(m1["id"], m1["id"]) is True


# ═══════════════════════════════════════════════════════════════
# Access Scope
# ═══════════════════════════════════════════════════════════════


class TestAccessScope:
    def test_own_workgroup_always_included(self, conn):
        result = get_accessible_workgroups(conn, WG_ALPHA)
        assert len(result) == 1
        assert result[0]["workgroup_id"] == WG_ALPHA

    def test_with_grant(self, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
        )
        result = get_accessible_workgroups(conn, WG_ALPHA)
        assert len(result) == 2
        wg_ids = {r["workgroup_id"] for r in result}
        assert WG_ALPHA in wg_ids
        assert WG_BETA in wg_ids

    def test_expired_grant_excluded(self, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
            valid_until="2020-01-01T00:00:00+00:00",
        )
        result = get_accessible_workgroups(conn, WG_ALPHA)
        assert len(result) == 1  # Only own WG

    def test_type_filtered_grant(self, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
            memory_type_filter="shared",
        )
        result = get_accessible_workgroups(conn, WG_ALPHA)
        granted = [r for r in result if r["grant_id"] is not None]
        assert len(granted) == 1
        assert granted[0]["type_filter"] == "shared"

    def test_agent_specific_grant(self, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_agent_id="agent-special",
            permission="read",
            created_by="admin",
        )
        # agent-special sees the grant
        result = get_accessible_workgroups(conn, WG_ALPHA, caller_agent_id="agent-special")
        assert len(result) == 2
        # other agent does NOT
        result2 = get_accessible_workgroups(conn, WG_ALPHA, caller_agent_id="other-agent")
        assert len(result2) == 1

    def test_can_access_own_workgroup(self, conn, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            access_scope="workgroup",
            content={"x": 1},
        )
        assert can_access_memory(conn, m, WG_ALPHA, "a1")

    def test_private_only_creator(self, conn, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            access_scope="private",
            content={"secret": True},
        )
        assert can_access_memory(conn, m, WG_ALPHA, "a1")
        assert not can_access_memory(conn, m, WG_ALPHA, "a2")

    def test_cross_wg_without_grant(self, conn, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_BETA,
            memory_type="semantic",
            access_scope="workgroup",
            content={"x": 1},
        )
        assert not can_access_memory(conn, m, WG_ALPHA, "a2")


# ═══════════════════════════════════════════════════════════════
# WAVE 2: Search
# ═══════════════════════════════════════════════════════════════


class TestSearch:
    def test_search_own_workgroup(self, store, conn):
        for i in range(5):
            store.create_memory(
                agent_id="a1",
                workgroup_id=WG_ALPHA,
                memory_type="semantic",
                content={"i": i},
            )
        results = search_memories(conn, WG_ALPHA, caller_agent_id="a1")
        assert len(results) == 5

    def test_search_excludes_deleted(self, store, conn):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        store.soft_delete_memory(m["id"])
        results = search_memories(conn, WG_ALPHA, caller_agent_id="a1")
        assert len(results) == 0

    def test_search_filter_by_type(self, store, conn):
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="episodic",
            content={"x": 2},
        )
        results = search_memories(conn, WG_ALPHA, memory_type="semantic", caller_agent_id="a1")
        assert len(results) == 1
        assert results[0]["memory_type"] == "semantic"

    def test_search_content_contains(self, store, conn):
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"fact": "sky is blue"},
        )
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"fact": "grass is green"},
        )
        results = search_memories(
            conn,
            WG_ALPHA,
            content_contains="blue",
            caller_agent_id="a1",
        )
        assert len(results) == 1

    def test_search_ordered_by_utility(self, store, conn):
        m1 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        m2 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 2},
        )
        # Boost m2's utility
        store.update_utility(m2["id"], reward=0.9)
        results = search_memories(conn, WG_ALPHA, caller_agent_id="a1")
        assert results[0]["id"] == m2["id"]

    def test_search_with_grant(self, store, conn):
        # Create memory in WG_BETA
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="shared",
            access_scope="workgroup",
            content={"shared": True},
            importance=0.8,
        )
        # Grant WG_ALPHA read access to WG_BETA
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
        )
        granted = get_accessible_workgroups(conn, WG_ALPHA, caller_agent_id="a1")
        granted_only = [g for g in granted if g["grant_id"]]
        results = search_memories(
            conn,
            WG_ALPHA,
            caller_agent_id="a1",
            granted_workgroups=granted_only,
        )
        assert any(r.get("content", {}).get("shared") for r in results)

    def test_search_private_not_visible_cross_wg(self, store, conn):
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="semantic",
            access_scope="private",
            content={"secret": True},
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
        )
        granted = get_accessible_workgroups(conn, WG_ALPHA, caller_agent_id="a1")
        granted_only = [g for g in granted if g["grant_id"]]
        results = search_memories(
            conn,
            WG_ALPHA,
            caller_agent_id="a1",
            granted_workgroups=granted_only,
        )
        # Private memories should NOT be visible even with grant
        assert not any(r.get("content", {}).get("secret") for r in results)

    def test_search_updates_access_count(self, store, conn):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        search_memories(conn, WG_ALPHA, caller_agent_id="a1")
        fetched = store.get_memory(m["id"])
        assert fetched["access_count"] == 1


# ═══════════════════════════════════════════════════════════════
# WAVE 3: Grants
# ═══════════════════════════════════════════════════════════════


class TestGrants:
    def test_create_and_get_grant(self, conn):
        g = create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
        )
        assert g["grantor_workgroup_id"] == WG_BETA
        fetched = get_grant(conn, g["id"])
        assert fetched is not None

    def test_revoke_grant(self, conn, store):
        g = create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
        )
        # Create memory in BETA
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="shared",
            access_scope="workgroup",
            content={"visible": True},
        )
        # Before revoke: visible
        granted = get_accessible_workgroups(conn, WG_ALPHA)
        granted_only = [x for x in granted if x["grant_id"]]
        results = search_memories(
            conn, WG_ALPHA, caller_agent_id="a1", granted_workgroups=granted_only
        )
        assert len(results) >= 1

        # Revoke
        delete_grant(conn, g["id"])

        # After revoke: not visible
        granted = get_accessible_workgroups(conn, WG_ALPHA)
        granted_only = [x for x in granted if x["grant_id"]]
        results = search_memories(
            conn, WG_ALPHA, caller_agent_id="a1", granted_workgroups=granted_only
        )
        assert len(results) == 0

    def test_type_filtered_grant(self, conn, store):
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
            memory_type_filter="shared",
        )
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="shared",
            access_scope="workgroup",
            content={"type": "shared"},
        )
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="episodic",
            access_scope="workgroup",
            content={"type": "episodic"},
        )
        granted = get_accessible_workgroups(conn, WG_ALPHA)
        granted_only = [x for x in granted if x["grant_id"]]
        results = search_memories(
            conn, WG_ALPHA, caller_agent_id="a1", granted_workgroups=granted_only
        )
        # Only shared type should be visible
        assert all(r["memory_type"] == "shared" for r in results if r["workgroup_id"] == WG_BETA)

    def test_importance_filtered_grant(self, conn, store):
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
            min_importance=0.7,
        )
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="semantic",
            access_scope="workgroup",
            content={"imp": "high"},
            importance=0.9,
        )
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="semantic",
            access_scope="workgroup",
            content={"imp": "low"},
            importance=0.3,
        )
        granted = get_accessible_workgroups(conn, WG_ALPHA)
        granted_only = [x for x in granted if x["grant_id"]]
        results = search_memories(
            conn, WG_ALPHA, caller_agent_id="a1", granted_workgroups=granted_only
        )
        beta_results = [r for r in results if r["workgroup_id"] == WG_BETA]
        assert all(r["importance"] >= 0.7 for r in beta_results)

    def test_list_grants(self, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_GAMMA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
        )
        all_grants = list_grants(conn, grantee_workgroup_id=WG_ALPHA)
        assert len(all_grants) == 2


# ═══════════════════════════════════════════════════════════════
# WAVE 4: Access Log + Background Tasks
# ═══════════════════════════════════════════════════════════════


class TestAccessLog:
    def test_write_and_read_log(self, conn):
        lid = write_access_log(
            conn,
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            action="write",
            memory_ids=["mem-1"],
            latency_ms=2.5,
        )
        logs = get_access_logs(conn, agent_id="a1")
        assert len(logs) == 1
        assert logs[0]["action"] == "write"

    def test_trace_id_logged(self, conn):
        write_access_log(
            conn,
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            action="read",
            trace_id="abc-123",
        )
        logs = get_access_logs(conn, agent_id="a1")
        assert logs[0]["trace_id"] == "abc-123"

    def test_count_recent_actions(self, conn):
        for _ in range(5):
            write_access_log(
                conn,
                agent_id="a1",
                workgroup_id=WG_ALPHA,
                action="write",
            )
        count = count_recent_actions(conn, agent_id="a1", action="write")
        assert count == 5


class TestCleanupTask:
    def test_cleanup_expired_working_memory(self, store):
        # Create expired working memory
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="working",
            content={"temp": True},
            expires_at="2020-01-01T00:00:00+00:00",
        )
        # Create non-expired
        m2 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="working",
            content={"temp": False},
            expires_at="2099-01-01T00:00:00+00:00",
        )
        task = MemoryCleanupTask(store._conn)
        count = task.run_once()
        assert count == 1
        # Non-expired should still exist
        assert store.get_memory(m2["id"]) is not None

    def test_cleanup_health(self, store):
        task = MemoryCleanupTask(store._conn)
        h = task.health()
        assert h["status"] == "not_started"
        task.run_once()
        h = task.health()
        assert h["status"] == "ok"

    def test_non_working_memory_not_cleaned(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"perm": True},
        )
        task = MemoryCleanupTask(store._conn)
        task.run_once()
        assert store.get_memory(m["id"]) is not None


class TestUtilityDecay:
    def test_decay_old_memories(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG_ALPHA,
            memory_type="semantic",
            content={"x": 1},
        )
        # Manually set last_accessed_at to 30 days ago
        from datetime import timedelta

        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        store._conn.execute(
            "UPDATE agent_memories SET last_accessed_at = ? WHERE id = ?",
            (old, m["id"]),
        )
        store._conn.commit()

        task = UtilityDecayTask(store._conn)
        count = task.run_once()
        assert count >= 1
        fetched = store.get_memory(m["id"])
        assert fetched["utility"] < 0.5  # Decayed from 0.5


# ═══════════════════════════════════════════════════════════════
# Isolation (cross-workgroup boundary tests)
# ═══════════════════════════════════════════════════════════════


class TestIsolation:
    def test_three_workgroups_isolated(self, store, conn):
        """3 workgroups, each with 100 memories. Zero cross-WG leaks."""
        for wg in [WG_ALPHA, WG_BETA, WG_GAMMA]:
            for i in range(100):
                store.create_memory(
                    agent_id=f"agent-{wg}",
                    workgroup_id=wg,
                    memory_type="semantic",
                    access_scope="workgroup",
                    content={"marker": f"{wg}-{i}"},
                )

        leaks = 0
        for wg in [WG_ALPHA, WG_BETA, WG_GAMMA]:
            results = search_memories(conn, wg, limit=100, caller_agent_id=f"agent-{wg}")
            for r in results:
                if r["workgroup_id"] != wg:
                    leaks += 1
        assert leaks == 0, f"Found {leaks} cross-workgroup leaks!"

    def test_grant_enables_selective_access(self, store, conn):
        """Grant from BETA→ALPHA only exposes shared type with high importance."""
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="shared",
            access_scope="workgroup",
            content={"visible": True},
            importance=0.9,
        )
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="episodic",
            access_scope="workgroup",
            content={"hidden_type": True},
            importance=0.9,
        )
        store.create_memory(
            agent_id="b1",
            workgroup_id=WG_BETA,
            memory_type="shared",
            access_scope="workgroup",
            content={"hidden_imp": True},
            importance=0.2,
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_BETA,
            grantee_workgroup_id=WG_ALPHA,
            permission="read",
            created_by="admin",
            memory_type_filter="shared",
            min_importance=0.7,
        )
        granted = get_accessible_workgroups(conn, WG_ALPHA, caller_agent_id="a1")
        granted_only = [g for g in granted if g["grant_id"]]
        results = search_memories(
            conn,
            WG_ALPHA,
            caller_agent_id="a1",
            granted_workgroups=granted_only,
        )
        beta_results = [r for r in results if r["workgroup_id"] == WG_BETA]
        # Only the high-importance shared memory should be visible
        assert len(beta_results) == 1
        assert beta_results[0]["content"]["visible"] is True


# Need these imports for TestUtilityDecay
from datetime import datetime, timezone
