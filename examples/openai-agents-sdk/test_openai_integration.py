"""Integration tests for the PondDB OpenAI Agents SDK demo tools.

These tests mock httpx so they run without a live PondDB server or OpenAI API key.
Run with: pytest examples/openai-agents-sdk/test_integration.py -v
"""

from __future__ import annotations

import json
import sys
import os
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out the `agents` package before importing demo.py so the import does
# not fail in environments where openai-agents is not installed.
# ---------------------------------------------------------------------------

def _make_agents_stub() -> types.ModuleType:
    mod = types.ModuleType("agents")

    def function_tool(fn):  # passthrough decorator
        return fn

    class Agent:
        def __init__(self, **kwargs):
            self.name = kwargs.get("name", "Agent")
            self.instructions = kwargs.get("instructions", "")
            self.tools = kwargs.get("tools", [])

    class Runner:
        @staticmethod
        def run_sync(agent, prompt):
            result = MagicMock()
            result.final_output = f"Mocked response to: {prompt}"
            return result

    mod.function_tool = function_tool
    mod.Agent = Agent
    mod.Runner = Runner
    return mod


sys.modules.setdefault("agents", _make_agents_stub())

# Now we can safely import the demo module using explicit file path
# to avoid collision with other examples/*/demo.py modules.
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "openai_demo", os.path.join(os.path.dirname(__file__), "demo.py")
)
demo = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(demo)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int = 200, body: dict | list | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRememberTool:
    """remember() must POST to /memories with the correct JSON body."""

    def test_posts_correct_body_and_returns_id(self):
        fake_id = "mem-abc-123"
        mock_resp = _mock_response(201, {"id": fake_id})

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = demo.remember(
                agent_id="test-agent",
                memory_type="episodic",
                content={"note": "hello"},
                importance=0.8,
                access_scope="private",
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args

        # Verify URL contains /memories
        url_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
        assert "/memories" in url_arg

        # Verify JSON body fields
        sent_json = call_kwargs.kwargs.get("json", {})
        assert sent_json["agent_id"] == "test-agent"
        assert sent_json["memory_type"] == "episodic"
        assert sent_json["importance"] == 0.8
        assert sent_json["access_scope"] == "private"

        # Verify return value contains the memory ID
        assert fake_id in result

    def test_returns_error_message_on_failure(self):
        mock_resp = _mock_response(500, {"detail": "internal error"})

        with patch("httpx.post", return_value=mock_resp):
            result = demo.remember(
                agent_id="agent-x",
                memory_type="working",
                content={"tmp": "data"},
                importance=0.5,
                access_scope="private",
            )

        assert "error" in result.lower() or "failed" in result.lower() or "500" in result


class TestRecallTool:
    """recall() must GET /memories/search with the correct query params."""

    def test_formats_correct_query_params_and_returns_results(self):
        memories = [
            {
                "id": "m1",
                "memory_type": "semantic",
                "content": {"fact": "PondDB is fast"},
                "importance": 0.9,
            }
        ]
        mock_resp = _mock_response(200, memories)

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            result = demo.recall(
                content_contains="PondDB",
                memory_type="semantic",
                min_importance=0.7,
                limit=5,
            )

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args

        url_arg = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
        assert "/memories/search" in url_arg

        params = call_kwargs.kwargs.get("params", {})
        assert params.get("content_contains") == "PondDB"
        assert params.get("memory_type") == "semantic"
        assert params.get("min_importance") == 0.7
        assert params.get("limit") == 5

        # Result should mention the memory content or ID
        assert "PondDB" in result or "m1" in result

    def test_returns_no_memories_message_when_empty(self):
        mock_resp = _mock_response(200, [])

        with patch("httpx.get", return_value=mock_resp):
            result = demo.recall(content_contains="nothing", memory_type="episodic")

        assert "no memories found" in result.lower()

    def test_handles_multiple_results(self):
        memories = [
            {"id": "m1", "memory_type": "episodic", "content": {"event": "A"}, "importance": 0.8},
            {"id": "m2", "memory_type": "episodic", "content": {"event": "B"}, "importance": 0.6},
        ]
        mock_resp = _mock_response(200, memories)

        with patch("httpx.get", return_value=mock_resp):
            result = demo.recall(content_contains="event")

        # Both memories should be represented
        assert "m1" in result or "A" in result
        assert "m2" in result or "B" in result


class TestMemoryStatsTool:
    """memory_stats() must POST to /pondapi/execute with a SQL query and
    return a formatted summary of the results."""

    def test_formats_sql_response_correctly(self):
        rows = [
            {"memory_type": "episodic", "count": 10, "avg_importance": 0.75},
            {"memory_type": "semantic", "count": 5, "avg_importance": 0.90},
        ]
        mock_resp = _mock_response(200, {"rows": rows, "rows_returned": 2})

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = demo.memory_stats(session_id="sess-1")

        mock_post.assert_called_once()
        sent_json = mock_post.call_args.kwargs.get("json", {})
        assert "sql" in sent_json
        assert "session_id" in sent_json

        # Both memory types should appear in output
        assert "episodic" in result
        assert "semantic" in result

    def test_returns_error_on_bad_status(self):
        mock_resp = _mock_response(400, {"detail": "bad request"})

        with patch("httpx.post", return_value=mock_resp):
            result = demo.memory_stats(session_id="sess-2")

        assert "error" in result.lower() or "failed" in result.lower() or "400" in result
