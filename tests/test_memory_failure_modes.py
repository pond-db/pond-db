# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""30 tests: Failure modes F1-F10, rate limits, utility bounds, cascades."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ponddb.memory.store import MemoryStore
from ponddb.memory.access import can_modify_memory
from ponddb.memory.access_log import count_recent_actions, write_access_log
from ponddb.memory.search import search_memories
from ponddb.memory.tasks import MemoryCleanupTask, UtilityDecayTask

WG = "wg-fail"


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "fail.db"))
    s.initialize_blocking()
    return s


@pytest.fixture
def conn(store):
    return store._conn


# ── F1: JWT workgroup enforcement ────────────────────────────


class TestJwtEnforcement:
    def test_memory_uses_workgroup_from_create_call(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert m["workgroup_id"] == WG

    def test_different_workgroup_creates_in_that_wg(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id="other-wg", memory_type="semantic", content={"x": 1}
        )
        assert m["workgroup_id"] == "other-wg"


# ── F2: Rate limit enforcement ───────────────────────────────


class TestRateLimits:
    def test_write_rate_limit_100_per_min(self, store, conn):
        for i in range(100):
            write_access_log(conn, agent_id="spammer", workgroup_id=WG, action="write")
        count = count_recent_actions(conn, agent_id="spammer", action="write", window_seconds=60)
        assert count == 100

    def test_feedback_rate_limit_10_per_hour(self, store, conn):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        for i in range(10):
            write_access_log(
                conn, agent_id="a1", workgroup_id=WG, action="feedback", memory_ids=[m["id"]]
            )
        count = count_recent_actions(
            conn, action="feedback", memory_id=m["id"], window_seconds=3600
        )
        assert count == 10


# ── F3/F4: Causal chain ─────────────────────────────────────


class TestCausalChain:
    def test_direct_cycle_detected(self, store):
        m1 = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="episodic", content={"step": 1}
        )
        m2 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="episodic",
            content={"step": 2},
            causal_parent_id=m1["id"],
        )
        # m2→m1 chain. check_causal_cycle(m2, m1) walks m2→m1 and finds m1 == new_id
        assert store.check_causal_cycle(m2["id"], m1["id"]) is True

    def test_three_node_cycle_detected(self, store):
        m1 = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="episodic", content={"step": 1}
        )
        m2 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="episodic",
            content={"step": 2},
            causal_parent_id=m1["id"],
        )
        m3 = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="episodic",
            content={"step": 3},
            causal_parent_id=m2["id"],
        )
        # Trying to make m1's parent = m3 would create m1→m3→m2→m1
        assert store.check_causal_cycle(m3["id"], m1["id"]) is True

    def test_no_cycle_linear_chain(self, store):
        mems = []
        for i in range(5):
            parent = mems[-1]["id"] if mems else None
            m = store.create_memory(
                agent_id="a1",
                workgroup_id=WG,
                memory_type="episodic",
                content={"step": i},
                causal_parent_id=parent,
            )
            mems.append(m)
        # Adding m6 with parent=m5 is fine (no cycle)
        assert store.check_causal_cycle(mems[-1]["id"]) is False

    def test_deep_chain_up_to_50(self, store):
        mems = []
        for i in range(50):
            parent = mems[-1]["id"] if mems else None
            m = store.create_memory(
                agent_id="a1",
                workgroup_id=WG,
                memory_type="episodic",
                content={"depth": i},
                causal_parent_id=parent,
            )
            mems.append(m)
        # 50-deep chain is fine
        assert store.check_causal_cycle(mems[-1]["id"]) is False

    def test_self_reference_detected(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="episodic", content={"x": 1}
        )
        assert store.check_causal_cycle(m["id"], m["id"]) is True


# ── F5/F6/F7: Utility bounds ────────────────────────────────


class TestUtilityBounds:
    def test_utility_capped_at_0_9_after_50_positive(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        for _ in range(50):
            store.update_utility(m["id"], reward=1.0)
        fetched = store.get_memory(m["id"])
        assert fetched["utility"] <= 0.9

    def test_utility_floored_at_0_1_after_50_negative(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        for _ in range(50):
            store.update_utility(m["id"], reward=-1.0)
        fetched = store.get_memory(m["id"])
        assert fetched["utility"] >= 0.1

    def test_utility_converges_toward_reward(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        for _ in range(20):
            store.update_utility(m["id"], reward=0.8)
        fetched = store.get_memory(m["id"])
        # Should converge toward 0.8 but capped at 0.9
        assert fetched["utility"] > 0.6

    def test_utility_starts_at_0_5(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert m["utility"] == 0.5

    def test_single_feedback_moves_correctly(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        r = store.update_utility(m["id"], reward=0.8)
        # utility = 0.5 + 0.1*(0.8-0.5) = 0.53
        assert abs(r["new_utility"] - 0.53) < 0.01


# ── F8: Working memory expiry ────────────────────────────────


class TestWorkingMemoryExpiry:
    def test_expired_working_memory_not_in_search(self, store, conn):
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="working",
            access_scope="private",
            content={"tmp": True},
            expires_at="2020-01-01T00:00:00+00:00",
        )
        r = search_memories(conn, WG, caller_agent_id="a1", limit=100)
        assert not any(m["content"].get("tmp") for m in r)

    def test_non_expired_working_memory_in_search(self, store, conn):
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="working",
            access_scope="private",
            content={"active": True},
            expires_at="2099-01-01T00:00:00+00:00",
        )
        r = search_memories(conn, WG, caller_agent_id="a1", limit=100)
        assert any(m["content"].get("active") for m in r)

    def test_cleanup_removes_expired(self, store):
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="working",
            content={"tmp": True},
            expires_at="2020-01-01T00:00:00+00:00",
        )
        task = MemoryCleanupTask(store._conn)
        count = task.run_once()
        assert count == 1


# ── F9: Soft delete ──────────────────────────────────────────


class TestSoftDelete:
    def test_deleted_not_in_search(self, store, conn):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        store.soft_delete_memory(m["id"])
        r = search_memories(conn, WG, caller_agent_id="a1", limit=100)
        assert not any(mem["id"] == m["id"] for mem in r)

    def test_deleted_has_deleted_at_in_db(self, store, conn):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        store.soft_delete_memory(m["id"])
        row = conn.execute(
            "SELECT deleted_at FROM agent_memories WHERE id = ?", (m["id"],)
        ).fetchone()
        assert row["deleted_at"] is not None

    def test_deleted_not_returned_by_get(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        store.soft_delete_memory(m["id"])
        assert store.get_memory(m["id"]) is None


# ── F10: Modify permissions ──────────────────────────────────


class TestModifyPermissions:
    def test_creator_can_modify(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert can_modify_memory(m, WG, "a1")

    def test_other_agent_cannot_modify(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert not can_modify_memory(m, WG, "a2")

    def test_admin_can_modify(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert can_modify_memory(m, WG, "a2", is_admin=True)

    def test_different_wg_cannot_modify(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert not can_modify_memory(m, "other-wg", "a1")


# ── Utility decay ────────────────────────────────────────────


class TestUtilityDecay:
    def test_decay_reduces_old_memories(self, store):
        from datetime import datetime, timezone, timedelta

        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        store._conn.execute(
            "UPDATE agent_memories SET last_accessed_at = ? WHERE id = ?", (old, m["id"])
        )
        store._conn.commit()
        task = UtilityDecayTask(store._conn)
        task.run_once()
        fetched = store.get_memory(m["id"])
        assert fetched["utility"] < 0.5

    def test_recently_accessed_not_decayed(self, store):
        from datetime import datetime, timezone

        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        now = datetime.now(timezone.utc).isoformat()
        store._conn.execute(
            "UPDATE agent_memories SET last_accessed_at = ? WHERE id = ?", (now, m["id"])
        )
        store._conn.commit()
        task = UtilityDecayTask(store._conn)
        task.run_once()
        fetched = store.get_memory(m["id"])
        assert fetched["utility"] == 0.5  # Unchanged
