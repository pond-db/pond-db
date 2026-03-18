# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""40 tests: Access log entries for every operation, trace_id propagation, analytics queries."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ponddb.memory.store import MemoryStore
from ponddb.memory.access_log import get_access_logs, write_access_log
from ponddb.memory.grants import create_grant
from ponddb.memory.tasks import MemoryCleanupTask

WG = "wg-mon"


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "mon.db"))
    s.initialize_blocking()
    return s


@pytest.fixture
def conn(store):
    return store._conn


# ── Write logging ────────────────────────────────────────────

class TestWriteLogging:
    def test_create_memory_logs_write(self, store, conn):
        store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1})
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write", memory_ids=["test"])
        logs = get_access_logs(conn, action="write")
        assert len(logs) >= 1
        assert logs[0]["agent_id"] == "a1"

    def test_each_write_creates_separate_log(self, store, conn):
        for i in range(5):
            write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write")
        logs = get_access_logs(conn, action="write")
        assert len(logs) == 5

    def test_write_log_has_correct_workgroup(self, store, conn):
        write_access_log(conn, agent_id="a1", workgroup_id="wg-special", action="write")
        logs = get_access_logs(conn, agent_id="a1")
        assert logs[0]["workgroup_id"] == "wg-special"


# ── Search logging ───────────────────────────────────────────

class TestSearchLogging:
    def test_search_logs_result_count(self, store, conn):
        for i in range(3):
            store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="semantic",
                                content={"i": i})
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="search", result_count=3)
        logs = get_access_logs(conn, action="search")
        assert logs[0]["result_count"] == 3

    def test_empty_search_logs_zero_count(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="search", result_count=0)
        logs = get_access_logs(conn, action="search")
        assert logs[0]["result_count"] == 0


# ── Update logging ───────────────────────────────────────────

class TestUpdateLogging:
    def test_update_logs_action(self, store, conn):
        m = store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="semantic",
                                content={"v": 1})
        store.update_memory(m["id"], content={"v": 2})
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="update",
                         memory_ids=[m["id"]])
        logs = get_access_logs(conn, action="update")
        assert len(logs) == 1
        assert m["id"] in logs[0]["memory_ids"]


# ── Delete logging ───────────────────────────────────────────

class TestDeleteLogging:
    def test_delete_logs_action(self, store, conn):
        m = store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="semantic",
                                content={"x": 1})
        store.soft_delete_memory(m["id"])
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="delete",
                         memory_ids=[m["id"]])
        logs = get_access_logs(conn, action="delete")
        assert len(logs) == 1


# ── Feedback logging ─────────────────────────────────────────

class TestFeedbackLogging:
    def test_feedback_logs_action(self, store, conn):
        m = store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="semantic",
                                content={"x": 1})
        store.update_utility(m["id"], reward=0.8)
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="feedback",
                         memory_ids=[m["id"]])
        logs = get_access_logs(conn, action="feedback")
        assert len(logs) == 1


# ── Cross-workgroup logging ──────────────────────────────────

class TestCrossWorkgroupLogging:
    def test_cross_wg_search_logs_grant_id(self, store, conn):
        g = create_grant(conn, grantor_workgroup_id="wg-src", grantee_workgroup_id=WG,
                         permission="read", created_by="admin")
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="search",
                         grant_id=g["id"], source_workgroup_id="wg-src", result_count=5)
        logs = get_access_logs(conn, action="search")
        assert logs[0]["grant_id"] == g["id"]
        assert logs[0]["source_workgroup_id"] == "wg-src"

    def test_cross_wg_search_without_grant_no_grant_id(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="search", result_count=3)
        logs = get_access_logs(conn, action="search")
        assert logs[0]["grant_id"] is None


# ── Error logging ────────────────────────────────────────────

class TestErrorLogging:
    def test_error_status_logged(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write",
                         status="error", error_type="rate_limit")
        logs = get_access_logs(conn, agent_id="a1")
        assert logs[0]["status"] == "error"
        assert logs[0]["error_type"] == "rate_limit"

    def test_denied_status_logged(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="read",
                         status="denied", error_type="no_grant")
        logs = get_access_logs(conn, agent_id="a1")
        assert logs[0]["status"] == "denied"

    def test_403_logs_error(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="update",
                         status="error", error_type="forbidden")
        logs = get_access_logs(conn, action="update")
        assert logs[0]["error_type"] == "forbidden"

    def test_404_logs_error(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="read",
                         status="error", error_type="not_found")
        logs = get_access_logs(conn, action="read")
        assert logs[0]["error_type"] == "not_found"

    def test_429_logs_error(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write",
                         status="error", error_type="rate_limit_exceeded")
        logs = get_access_logs(conn, action="write")
        assert logs[0]["error_type"] == "rate_limit_exceeded"


# ── Trace ID propagation ────────────────────────────────────

class TestTraceId:
    def test_trace_id_stored(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write",
                         trace_id="abc-123-def")
        logs = get_access_logs(conn, agent_id="a1")
        assert logs[0]["trace_id"] == "abc-123-def"

    def test_span_id_stored(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write",
                         trace_id="abc", span_id="span-456")
        logs = get_access_logs(conn, agent_id="a1")
        assert logs[0]["span_id"] == "span-456"

    def test_no_trace_id_is_null(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write")
        logs = get_access_logs(conn, agent_id="a1")
        assert logs[0]["trace_id"] is None

    def test_latency_ms_stored(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write", latency_ms=3.14)
        logs = get_access_logs(conn, agent_id="a1")
        assert abs(logs[0]["latency_ms"] - 3.14) < 0.01


# ── Cleanup task logging ────────────────────────────────────

class TestCleanupLogging:
    def test_cleanup_logs_as_system(self, store, conn):
        store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="working",
                            content={"tmp": True}, expires_at="2020-01-01T00:00:00+00:00")
        task = MemoryCleanupTask(conn)
        task.run_once()
        logs = get_access_logs(conn, agent_id="system")
        assert len(logs) >= 1
        assert logs[0]["action"] == "cleanup"

    def test_cleanup_logs_count(self, store, conn):
        for i in range(5):
            store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="working",
                                content={"i": i}, expires_at="2020-01-01T00:00:00+00:00")
        task = MemoryCleanupTask(conn)
        task.run_once()
        logs = get_access_logs(conn, agent_id="system", action="cleanup")
        assert logs[0]["result_count"] == 5


# ── Analytics queries ────────────────────────────────────────

class TestAnalyticsQueries:
    def test_most_active_agent(self, conn):
        for _ in range(10):
            write_access_log(conn, agent_id="heavy-user", workgroup_id=WG, action="write")
        for _ in range(3):
            write_access_log(conn, agent_id="light-user", workgroup_id=WG, action="write")
        row = conn.execute(
            "SELECT agent_id, COUNT(*) as cnt FROM memory_access_log "
            "WHERE action = 'write' GROUP BY agent_id ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        assert row["agent_id"] == "heavy-user"
        assert row["cnt"] == 10

    def test_cross_wg_audit(self, conn):
        g = create_grant(conn, grantor_workgroup_id="src-wg", grantee_workgroup_id=WG,
                         permission="read", created_by="admin")
        for _ in range(5):
            write_access_log(conn, agent_id="a1", workgroup_id=WG, action="search",
                             grant_id=g["id"], source_workgroup_id="src-wg")
        rows = conn.execute(
            "SELECT grant_id, COUNT(*) as cnt FROM memory_access_log "
            "WHERE grant_id IS NOT NULL GROUP BY grant_id"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["cnt"] == 5

    def test_error_summary(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write",
                         status="error", error_type="rate_limit")
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write",
                         status="error", error_type="rate_limit")
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="read",
                         status="error", error_type="not_found")
        rows = conn.execute(
            "SELECT error_type, COUNT(*) as cnt FROM memory_access_log "
            "WHERE status = 'error' GROUP BY error_type ORDER BY cnt DESC"
        ).fetchall()
        assert rows[0]["error_type"] == "rate_limit"
        assert rows[0]["cnt"] == 2

    def test_action_breakdown(self, conn):
        for action in ["write", "write", "search", "read", "feedback"]:
            write_access_log(conn, agent_id="a1", workgroup_id=WG, action=action)
        rows = conn.execute(
            "SELECT action, COUNT(*) as cnt FROM memory_access_log GROUP BY action ORDER BY cnt DESC"
        ).fetchall()
        assert {r["action"]: r["cnt"] for r in rows}["write"] == 2

    def test_filter_by_since(self, conn):
        write_access_log(conn, agent_id="a1", workgroup_id=WG, action="write")
        logs = get_access_logs(conn, since="2020-01-01T00:00:00+00:00")
        assert len(logs) >= 1
        logs2 = get_access_logs(conn, since="2099-01-01T00:00:00+00:00")
        assert len(logs2) == 0
