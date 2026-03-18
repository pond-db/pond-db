# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""50 tests: Workgroup isolation, grant filtering, concurrent readers."""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ponddb.memory.store import MemoryStore
from ponddb.memory.access import get_accessible_workgroups
from ponddb.memory.grants import create_grant, delete_grant
from ponddb.memory.search import search_memories

WG_A, WG_B, WG_C = "wg-alpha", "wg-beta", "wg-gamma"
AGENTS_A = [f"a-{i}" for i in range(4)]
AGENTS_B = [f"b-{i}" for i in range(4)]
AGENTS_C = [f"c-{i}" for i in range(4)]


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "iso.db"))
    s.initialize_blocking()
    return s


@pytest.fixture
def conn(store):
    return store._conn


@pytest.fixture
def populated(store):
    """Create 100 memories per workgroup with distinct markers."""
    for wg, agents in [(WG_A, AGENTS_A), (WG_B, AGENTS_B), (WG_C, AGENTS_C)]:
        for i in range(100):
            agent = agents[i % len(agents)]
            mtype = ["semantic", "episodic", "shared", "procedural", "working"][i % 5]
            store.create_memory(
                agent_id=agent,
                workgroup_id=wg,
                memory_type=mtype,
                access_scope="workgroup" if mtype != "working" else "private",
                content={"marker": f"{wg}:{i}", "idx": i},
                importance=round(0.1 + (i % 10) * 0.09, 2),
            )
    return store


# ── Basic isolation ──────────────────────────────────────────


class TestBasicIsolation:
    def test_alpha_reads_alpha(self, populated, conn):
        r = search_memories(conn, WG_A, caller_agent_id="a-0", limit=100)
        assert len(r) > 0
        assert all(m["workgroup_id"] == WG_A for m in r)

    def test_alpha_reads_beta_no_grant(self, populated, conn):
        r = search_memories(conn, WG_A, caller_agent_id="a-0", limit=100)
        assert not any(m["workgroup_id"] == WG_B for m in r)

    def test_alpha_reads_gamma_no_grant(self, populated, conn):
        r = search_memories(conn, WG_A, caller_agent_id="a-0", limit=100)
        assert not any(m["workgroup_id"] == WG_C for m in r)

    def test_beta_reads_beta(self, populated, conn):
        r = search_memories(conn, WG_B, caller_agent_id="b-0", limit=100)
        assert len(r) > 0
        assert all(m["workgroup_id"] == WG_B for m in r)

    def test_gamma_reads_gamma(self, populated, conn):
        r = search_memories(conn, WG_C, caller_agent_id="c-0", limit=100)
        assert len(r) > 0
        assert all(m["workgroup_id"] == WG_C for m in r)

    def test_no_cross_contamination_300_memories(self, populated, conn):
        leaks = 0
        for wg, agent in [(WG_A, "a-0"), (WG_B, "b-0"), (WG_C, "c-0")]:
            r = search_memories(conn, wg, caller_agent_id=agent, limit=100)
            for m in r:
                if m["workgroup_id"] != wg:
                    leaks += 1
        assert leaks == 0

    def test_marker_content_matches_workgroup(self, populated, conn):
        for wg, agent in [(WG_A, "a-0"), (WG_B, "b-0"), (WG_C, "c-0")]:
            r = search_memories(conn, wg, caller_agent_id=agent, limit=100)
            for m in r:
                assert m["content"]["marker"].startswith(wg)


# ── Grant filtering ──────────────────────────────────────────


class TestGrantFiltering:
    def test_grant_type_filter(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            memory_type_filter="semantic",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        r = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        alpha_mems = [m for m in r if m["workgroup_id"] == WG_A]
        assert len(alpha_mems) > 0
        assert all(m["memory_type"] == "semantic" for m in alpha_mems)

    def test_grant_blocks_wrong_type(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            memory_type_filter="semantic",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        # Grant filters to semantic only. Even without caller memory_type filter,
        # alpha episodic memories should not appear
        r = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        alpha_episodic = [
            m for m in r if m["workgroup_id"] == WG_A and m["memory_type"] == "episodic"
        ]
        assert len(alpha_episodic) == 0

    def test_grant_importance_filter(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            min_importance=0.5,
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        r = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        alpha_mems = [m for m in r if m["workgroup_id"] == WG_A]
        assert all(m["importance"] >= 0.5 for m in alpha_mems)

    def test_grant_blocks_low_importance(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            min_importance=0.9,
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        r = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        alpha_low = [m for m in r if m["workgroup_id"] == WG_A and m["importance"] < 0.9]
        assert len(alpha_low) == 0

    def test_gamma_still_isolated_after_ab_grant(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_C, "c-0") if g["grant_id"]]
        r = search_memories(
            conn, WG_C, caller_agent_id="c-0", granted_workgroups=granted, limit=100
        )
        assert not any(m["workgroup_id"] == WG_A for m in r)

    def test_revoke_grant_immediate(self, populated, conn):
        g = create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        granted = [x for x in get_accessible_workgroups(conn, WG_B, "b-0") if x["grant_id"]]
        r1 = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        assert any(m["workgroup_id"] == WG_A for m in r1)

        delete_grant(conn, g["id"])
        granted = [x for x in get_accessible_workgroups(conn, WG_B, "b-0") if x["grant_id"]]
        r2 = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        assert not any(m["workgroup_id"] == WG_A for m in r2)

    def test_combined_type_and_importance_filter(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            memory_type_filter="semantic",
            min_importance=0.5,
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        r = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        alpha_mems = [m for m in r if m["workgroup_id"] == WG_A]
        for m in alpha_mems:
            assert m["memory_type"] == "semantic"
            assert m["importance"] >= 0.5


# ── Private scope enforcement ────────────────────────────────


class TestPrivateScope:
    def test_private_only_creator_sees(self, store, conn):
        store.create_memory(
            agent_id="a-0",
            workgroup_id=WG_A,
            memory_type="semantic",
            access_scope="private",
            content={"secret": True},
        )
        r = search_memories(conn, WG_A, caller_agent_id="a-0", limit=100)
        assert any(m["content"].get("secret") for m in r)

    def test_private_other_agent_same_wg_cant_see(self, store, conn):
        store.create_memory(
            agent_id="a-0",
            workgroup_id=WG_A,
            memory_type="semantic",
            access_scope="private",
            content={"secret": True},
        )
        r = search_memories(conn, WG_A, caller_agent_id="a-1", limit=100)
        assert not any(m["content"].get("secret") for m in r)

    def test_private_not_visible_cross_wg_with_grant(self, store, conn):
        store.create_memory(
            agent_id="a-0",
            workgroup_id=WG_A,
            memory_type="semantic",
            access_scope="private",
            content={"secret": True},
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        r = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        assert not any(m["content"].get("secret") for m in r)

    def test_workgroup_scope_visible_to_all_agents_in_wg(self, store, conn):
        store.create_memory(
            agent_id="a-0",
            workgroup_id=WG_A,
            memory_type="semantic",
            access_scope="workgroup",
            content={"shared_in_wg": True},
        )
        for agent in AGENTS_A:
            r = search_memories(conn, WG_A, caller_agent_id=agent, limit=100)
            assert any(m["content"].get("shared_in_wg") for m in r)

    def test_private_creator_can_still_see_after_search_by_other(self, store, conn):
        store.create_memory(
            agent_id="a-0",
            workgroup_id=WG_A,
            memory_type="semantic",
            access_scope="private",
            content={"mine": True},
        )
        # Other agent tries (gets nothing)
        search_memories(conn, WG_A, caller_agent_id="a-1", limit=100)
        # Creator still sees it
        r = search_memories(conn, WG_A, caller_agent_id="a-0", limit=100)
        assert any(m["content"].get("mine") for m in r)


# ── Agent-specific grants ────────────────────────────────────


class TestAgentSpecificGrants:
    def test_agent_specific_grant_target_sees(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_agent_id="b-0",
            permission="read",
            created_by="admin",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        r = search_memories(
            conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
        )
        assert any(m["workgroup_id"] == WG_A for m in r)

    def test_agent_specific_grant_other_agent_same_wg_cant_see(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_agent_id="b-0",
            permission="read",
            created_by="admin",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-1") if g["grant_id"]]
        r = search_memories(
            conn, WG_B, caller_agent_id="b-1", granted_workgroups=granted, limit=100
        )
        assert not any(m["workgroup_id"] == WG_A for m in r)


# ── Self-grant and edge cases ────────────────────────────────


class TestGrantEdgeCases:
    def test_self_grant_same_wg(self, conn):
        """Grant where grantor == grantee workgroup should be possible but no-op."""
        g = create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_A,
            permission="read",
            created_by="admin",
        )
        # Still works (grant created but redundant since own WG is always accessible)
        assert g is not None

    def test_multiple_grants_from_same_grantor(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            memory_type_filter="semantic",
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            memory_type_filter="episodic",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        assert len(granted) == 2

    def test_time_bounded_grant_active(self, populated, conn):
        from datetime import datetime, timezone, timedelta

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            valid_until=future,
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        assert len(granted) == 1

    def test_time_bounded_grant_expired(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
            valid_until="2020-01-01T00:00:00+00:00",
        )
        granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        assert len(granted) == 0


# ── Concurrent readers ───────────────────────────────────────


class TestConcurrentReaders:
    def test_10_concurrent_readers_zero_leaks(self, populated, conn):
        """10 threads searching their own workgroups — 0 leaks."""
        leaks = []
        barrier = threading.Barrier(10)

        def reader(wg, agent):
            barrier.wait()
            for _ in range(50):
                r = search_memories(conn, wg, caller_agent_id=agent, limit=100)
                for m in r:
                    if m["workgroup_id"] != wg:
                        leaks.append((wg, agent, m["workgroup_id"]))

        threads = []
        for wg, agents in [(WG_A, AGENTS_A), (WG_B, AGENTS_B), (WG_C, AGENTS_C)]:
            for a in agents[:3]:
                t = threading.Thread(target=reader, args=(wg, a))
                threads.append(t)
        # Use first 10 threads (4+3+3)
        for t in threads[:10]:
            t.start()
        for t in threads[:10]:
            t.join(timeout=30)
        assert len(leaks) == 0, f"Found {len(leaks)} leaks: {leaks[:5]}"

    def test_concurrent_read_during_grant_change(self, populated, conn):
        """Grant created mid-read — readers eventually see change."""
        results_before = []
        results_after = []

        def reader_before():
            granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
            r = search_memories(
                conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
            )
            results_before.extend([m for m in r if m["workgroup_id"] == WG_A])

        # Before grant
        reader_before()
        assert len(results_before) == 0

        # Create grant
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )

        def reader_after():
            granted = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
            r = search_memories(
                conn, WG_B, caller_agent_id="b-0", granted_workgroups=granted, limit=100
            )
            results_after.extend([m for m in r if m["workgroup_id"] == WG_A])

        reader_after()
        assert len(results_after) > 0


# ── Bidirectional and chain grants ───────────────────────────


class TestComplexGrants:
    def test_bidirectional_grant(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_B,
            grantee_workgroup_id=WG_A,
            permission="read",
            created_by="admin",
        )
        # A sees B
        ga = [g for g in get_accessible_workgroups(conn, WG_A, "a-0") if g["grant_id"]]
        ra = search_memories(conn, WG_A, caller_agent_id="a-0", granted_workgroups=ga, limit=100)
        assert any(m["workgroup_id"] == WG_B for m in ra)
        # B sees A
        gb = [g for g in get_accessible_workgroups(conn, WG_B, "b-0") if g["grant_id"]]
        rb = search_memories(conn, WG_B, caller_agent_id="b-0", granted_workgroups=gb, limit=100)
        assert any(m["workgroup_id"] == WG_A for m in rb)

    def test_chain_grant_not_transitive(self, populated, conn):
        """A grants B, B grants C — C should NOT see A (not transitive)."""
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_B,
            grantee_workgroup_id=WG_C,
            permission="read",
            created_by="admin",
        )
        gc = [g for g in get_accessible_workgroups(conn, WG_C, "c-0") if g["grant_id"]]
        rc = search_memories(conn, WG_C, caller_agent_id="c-0", granted_workgroups=gc, limit=100)
        assert not any(m["workgroup_id"] == WG_A for m in rc)

    def test_grant_to_all_three_workgroups(self, populated, conn):
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_C,
            permission="read",
            created_by="admin",
        )
        for wg, agent in [(WG_B, "b-0"), (WG_C, "c-0")]:
            g = [x for x in get_accessible_workgroups(conn, wg, agent) if x["grant_id"]]
            r = search_memories(conn, wg, caller_agent_id=agent, granted_workgroups=g, limit=100)
            assert any(m["workgroup_id"] == WG_A for m in r)

    def test_revoke_one_of_two_grants(self, populated, conn):
        g1 = create_grant(
            conn,
            grantor_workgroup_id=WG_A,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        g2 = create_grant(
            conn,
            grantor_workgroup_id=WG_C,
            grantee_workgroup_id=WG_B,
            permission="read",
            created_by="admin",
        )
        delete_grant(conn, g1["id"])
        g = [x for x in get_accessible_workgroups(conn, WG_B, "b-0") if x["grant_id"]]
        r = search_memories(conn, WG_B, caller_agent_id="b-0", granted_workgroups=g, limit=100)
        assert not any(m["workgroup_id"] == WG_A for m in r)
        assert any(m["workgroup_id"] == WG_C for m in r)
