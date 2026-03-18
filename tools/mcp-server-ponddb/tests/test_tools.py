"""Tests for MCP server tool dispatch and JSON-RPC handling."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_server_ponddb.server import TOOLS, handle_jsonrpc, handle_tool_call


class TestToolDefinitions:
    def test_five_tools_defined(self):
        assert len(TOOLS) == 5

    def test_tool_names(self):
        names = {t["name"] for t in TOOLS}
        assert names == {"ponddb_remember", "ponddb_recall", "ponddb_query", "ponddb_forget", "ponddb_feedback"}

    def test_all_tools_have_input_schema(self):
        for tool in TOOLS:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


class TestJsonRpc:
    def test_initialize(self):
        resp = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert resp["result"]["serverInfo"]["name"] == "mcp-server-ponddb"
        assert "tools" in resp["result"]["capabilities"]

    def test_tools_list(self):
        resp = handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert len(resp["result"]["tools"]) == 5

    def test_unknown_method(self):
        resp = handle_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "unknown/method", "params": {}})
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_notification_returns_none(self):
        resp = handle_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert resp is None

    def test_tool_call_unknown_tool(self):
        resp = handle_jsonrpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        assert "error" in resp


class TestToolCallDispatch:
    """Test that tool calls route to correct client methods.
    
    These mock the client to avoid needing a live PondDB server.
    """

    def test_remember_routes_correctly(self, monkeypatch):
        calls = []
        def mock_remember(self, **kwargs):
            calls.append(("remember", kwargs))
            return {"id": "test-id", "agent_id": "a1", "memory_type": "semantic", "created_at": "now"}

        from mcp_server_ponddb import client
        monkeypatch.setattr(client.PondDBClient, "remember", mock_remember)

        result = handle_tool_call("ponddb_remember", {
            "agent_id": "a1", "memory_type": "semantic",
            "content": {"fact": "test"},
        })
        assert len(calls) == 1
        assert calls[0][1]["agent_id"] == "a1"

    def test_recall_routes_correctly(self, monkeypatch):
        calls = []
        def mock_recall(self, **kwargs):
            calls.append(("recall", kwargs))
            return [{"id": "m1", "content": {"fact": "sky is blue"}}]

        from mcp_server_ponddb import client
        monkeypatch.setattr(client.PondDBClient, "recall", mock_recall)

        result = handle_tool_call("ponddb_recall", {"memory_type": "semantic", "limit": 5})
        assert len(calls) == 1

    def test_forget_routes_correctly(self, monkeypatch):
        calls = []
        def mock_forget(self, memory_id):
            calls.append(("forget", memory_id))
            return {"id": memory_id, "deleted_at": "now"}

        from mcp_server_ponddb import client
        monkeypatch.setattr(client.PondDBClient, "forget", mock_forget)

        result = handle_tool_call("ponddb_forget", {"memory_id": "mem-123"})
        assert len(calls) == 1
        assert calls[0][1] == "mem-123"

    def test_feedback_routes_correctly(self, monkeypatch):
        calls = []
        def mock_feedback(self, memory_id, reward):
            calls.append(("feedback", memory_id, reward))
            return {"id": memory_id, "old_utility": 0.5, "new_utility": 0.53}

        from mcp_server_ponddb import client
        monkeypatch.setattr(client.PondDBClient, "feedback", mock_feedback)

        result = handle_tool_call("ponddb_feedback", {"memory_id": "mem-123", "reward": 0.8})
        assert len(calls) == 1
        assert calls[0][2] == 0.8
