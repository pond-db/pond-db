# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""20 tests: Edge cases — empty content, large payloads, unicode, special chars."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ponddb.memory.store import MemoryStore
from ponddb.memory.access import can_modify_memory
from ponddb.memory.search import search_memories

WG = "wg-edge"


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "edge.db"))
    s.initialize_blocking()
    return s


@pytest.fixture
def conn(store):
    return store._conn


class TestEmptyContent:
    def test_empty_dict_accepted(self, store):
        m = store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="semantic", content={})
        assert m["content"] == {}

    def test_empty_dict_searchable(self, store, conn):
        store.create_memory(agent_id="a1", workgroup_id=WG, memory_type="semantic", content={})
        r = search_memories(conn, WG, caller_agent_id="a1", limit=10)
        assert len(r) == 1


class TestLargeContent:
    def test_100kb_content(self, store):
        large = {"data": "x" * 100_000}
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content=large
        )
        fetched = store.get_memory(m["id"])
        assert len(fetched["content"]["data"]) == 100_000

    def test_deeply_nested_json(self, store):
        nested = {"level": 0}
        current = nested
        for i in range(1, 50):
            current["child"] = {"level": i}
            current = current["child"]
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content=nested
        )
        fetched = store.get_memory(m["id"])
        assert fetched["content"]["level"] == 0


class TestUnicodeContent:
    def test_chinese_characters(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"text": "这是一个测试"}
        )
        fetched = store.get_memory(m["id"])
        assert fetched["content"]["text"] == "这是一个测试"

    def test_emoji_content(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"emoji": "🧠💡🔬"}
        )
        fetched = store.get_memory(m["id"])
        assert fetched["content"]["emoji"] == "🧠💡🔬"

    def test_arabic_content(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="semantic",
            content={"text": "مرحبا بالعالم"},
        )
        fetched = store.get_memory(m["id"])
        assert fetched["content"]["text"] == "مرحبا بالعالم"

    def test_mixed_unicode(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="semantic",
            content={"jp": "日本語", "kr": "한국어", "ru": "Русский"},
        )
        fetched = store.get_memory(m["id"])
        assert fetched["content"]["jp"] == "日本語"

    def test_unicode_in_search(self, store, conn):
        store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"text": "测试数据"}
        )
        r = search_memories(conn, WG, content_contains="测试", caller_agent_id="a1", limit=10)
        assert len(r) == 1


class TestMemoryKeyEdgeCases:
    def test_key_with_slashes(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="semantic",
            memory_key="path/to/fact",
            content={"x": 1},
        )
        assert m["memory_key"] == "path/to/fact"

    def test_key_with_dots(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="semantic",
            memory_key="config.db.host",
            content={"x": 1},
        )
        assert m["memory_key"] == "config.db.host"

    def test_key_with_spaces(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="semantic",
            memory_key="my fact key",
            content={"x": 1},
        )
        assert m["memory_key"] == "my fact key"

    def test_key_with_unicode(self, store):
        m = store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="semantic",
            memory_key="事实/关键",
            content={"x": 1},
        )
        assert m["memory_key"] == "事实/关键"


class TestSearchEdgeCases:
    def test_search_no_filters_returns_all(self, store, conn):
        for i in range(5):
            store.create_memory(
                agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"i": i}
            )
        r = search_memories(conn, WG, caller_agent_id="a1", limit=100)
        assert len(r) == 5

    def test_search_all_filters_intersection(self, store, conn):
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="semantic",
            access_scope="workgroup",
            content={"target": "yes"},
            importance=0.8,
        )
        store.create_memory(
            agent_id="a1",
            workgroup_id=WG,
            memory_type="episodic",
            content={"target": "no"},
            importance=0.8,
        )
        store.create_memory(
            agent_id="a2",
            workgroup_id=WG,
            memory_type="semantic",
            content={"target": "no"},
            importance=0.8,
        )
        r = search_memories(
            conn,
            WG,
            agent_id="a1",
            memory_type="semantic",
            min_importance=0.7,
            content_contains="yes",
            caller_agent_id="a1",
            limit=100,
        )
        assert len(r) == 1
        assert r[0]["content"]["target"] == "yes"

    def test_search_limit_capping(self, store, conn):
        for i in range(30):
            store.create_memory(
                agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"i": i}
            )
        r = search_memories(conn, WG, caller_agent_id="a1", limit=10)
        assert len(r) == 10


class TestFeedbackEdgeCases:
    def test_feedback_on_nonexistent_memory(self, store):
        result = store.update_utility("nonexistent-id", reward=0.5)
        assert result is None

    def test_update_nonexistent_memory(self, store):
        result = store.update_memory("nonexistent-id", content={"x": 1})
        assert result is None

    def test_modify_different_agent_blocked(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert not can_modify_memory(m, WG, "a2")

    def test_modify_different_wg_blocked(self, store):
        m = store.create_memory(
            agent_id="a1", workgroup_id=WG, memory_type="semantic", content={"x": 1}
        )
        assert not can_modify_memory(m, "other-wg", "a1")
