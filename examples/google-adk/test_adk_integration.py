"""Integration tests for the Google ADK + PondDB demo.

These tests do NOT require API keys or a running PondDB instance.
All httpx calls are mocked via unittest.mock.patch.
"""

from __future__ import annotations

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out google.adk so the module can be imported without the package
# installed.
# ---------------------------------------------------------------------------

_google = types.ModuleType("google")
_adk = types.ModuleType("google.adk")
_agents_mod = types.ModuleType("google.adk.agents")
_tools_mod = types.ModuleType("google.adk.tools")


class _StubAgent:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _StubFunctionTool:
    def __init__(self, fn: object) -> None:
        self.fn = fn


_agents_mod.Agent = _StubAgent
_tools_mod.FunctionTool = _StubFunctionTool

_google.adk = _adk
_adk.agents = _agents_mod
_adk.tools = _tools_mod

sys.modules.setdefault("google", _google)
sys.modules.setdefault("google.adk", _adk)
sys.modules.setdefault("google.adk.agents", _agents_mod)
sys.modules.setdefault("google.adk.tools", _tools_mod)

# Now import the module under test using explicit file path
# to avoid collision with other examples/*/demo.py modules.
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "adk_demo", os.path.join(os.path.dirname(__file__), "demo.py")
)
demo = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(demo)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, body: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRemember:
    def test_formats_correct_post_body(self) -> None:
        """remember() sends a POST to /memories with the expected JSON body."""
        expected_id = "mem-abc-123"
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                201, {"id": expected_id, "memory_type": "episodic"}
            )
            result = demo.remember(
                agent_id="agent-1",
                memory_type="episodic",
                content={"task": "analyse sales data"},
                importance=0.8,
                access_scope="workgroup",
            )

        mock_post.assert_called_once()
        _url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        assert _url.endswith("/memories")
        sent_body = kwargs["json"]
        assert sent_body["agent_id"] == "agent-1"
        assert sent_body["memory_type"] == "episodic"
        assert sent_body["content"] == {"task": "analyse sales data"}
        assert sent_body["importance"] == 0.8
        assert sent_body["access_scope"] == "workgroup"

    def test_returns_confirmation_with_id(self) -> None:
        """remember() returns a human-readable confirmation containing the memory id."""
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                201, {"id": "mem-xyz-999", "memory_type": "semantic"}
            )
            result = demo.remember(
                agent_id="agent-2",
                memory_type="semantic",
                content={"fact": "PondDB supports DuckDB SQL"},
                importance=0.9,
                access_scope="namespace",
            )

        assert "mem-xyz-999" in result

    def test_api_key_header_sent(self) -> None:
        """remember() includes X-API-Key in the request headers."""
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(201, {"id": "mem-1"})
            demo.remember(
                agent_id="a",
                memory_type="working",
                content={},
                importance=0.5,
                access_scope="private",
            )

        headers = mock_post.call_args[1]["headers"]
        assert "X-API-Key" in headers


class TestRecall:
    def test_formats_correct_get_params(self) -> None:
        """recall() sends a GET to /memories/search with the expected query params."""
        memories = [
            {"id": "1", "content": {"note": "hello"}, "memory_type": "episodic", "importance": 0.7}
        ]
        with patch("demo.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(200, memories)
            demo.recall(
                content_contains="hello",
                memory_type="episodic",
                min_importance=0.5,
                limit=10,
            )

        mock_get.assert_called_once()
        params = mock_get.call_args[1]["params"]
        assert params["content_contains"] == "hello"
        assert params["memory_type"] == "episodic"
        assert params["min_importance"] == 0.5
        assert params["limit"] == 10

    def test_returns_formatted_results(self) -> None:
        """recall() returns a string summarising found memories."""
        memories = [
            {
                "id": "mem-a",
                "content": {"task": "write report"},
                "memory_type": "procedural",
                "importance": 0.85,
            },
            {
                "id": "mem-b",
                "content": {"task": "review PR"},
                "memory_type": "procedural",
                "importance": 0.6,
            },
        ]
        with patch("demo.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(200, memories)
            result = demo.recall(
                content_contains="task",
                memory_type="procedural",
                min_importance=0.5,
                limit=5,
            )

        assert "2" in result or len(result) > 0  # found count or content present
        assert isinstance(result, str)

    def test_empty_results_returns_appropriate_message(self) -> None:
        """recall() with no results returns a message indicating nothing was found."""
        with patch("demo.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(200, [])
            result = demo.recall(
                content_contains="nonexistent topic",
                memory_type="semantic",
                min_importance=0.9,
                limit=5,
            )

        assert isinstance(result, str)
        # Should communicate that no memories were found
        lower = result.lower()
        assert any(word in lower for word in ("no memories", "no results", "found 0", "0 memories", "nothing"))


class TestRateMemory:
    def test_calls_correct_feedback_endpoint(self) -> None:
        """rate_memory() POSTs to /memories/{id}/feedback with the reward value."""
        memory_id = "mem-feedback-42"
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"status": "ok"})
            result = demo.rate_memory(memory_id=memory_id, reward=0.75)

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert f"/memories/{memory_id}/feedback" in url
        body = mock_post.call_args[1]["json"]
        assert body["reward"] == 0.75

    def test_returns_confirmation_string(self) -> None:
        """rate_memory() returns a non-empty confirmation string."""
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"status": "ok"})
            result = demo.rate_memory(memory_id="mem-1", reward=-0.5)

        assert isinstance(result, str)
        assert len(result) > 0


class TestMemoryAnalytics:
    def test_formats_sql_query_correctly(self) -> None:
        """memory_analytics() sends a POST to /pondapi/execute with valid SQL."""
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                200, {"rows": [], "rows_returned": 0}
            )
            demo.memory_analytics(session_id="sess-demo-1")

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "/pondapi/execute" in url
        body = mock_post.call_args[1]["json"]
        assert "sql" in body
        assert isinstance(body["sql"], str)
        # SQL should reference the memories table
        assert "agent_memories" in body["sql"].lower() or "memories" in body["sql"].lower()

    def test_session_id_included_in_request(self) -> None:
        """memory_analytics() includes the session_id in the pondapi/execute body."""
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(200, {"rows": [], "rows_returned": 0})
            demo.memory_analytics(session_id="my-session-99")

        body = mock_post.call_args[1]["json"]
        assert body.get("session_id") == "my-session-99"

    def test_returns_formatted_analytics_string(self) -> None:
        """memory_analytics() returns a string with query results."""
        rows = [
            {"memory_type": "episodic", "count": 5, "avg_importance": 0.72},
            {"memory_type": "semantic", "count": 3, "avg_importance": 0.88},
        ]
        with patch("demo.httpx.post") as mock_post:
            mock_post.return_value = _mock_response(
                200, {"rows": rows, "rows_returned": 2}
            )
            result = demo.memory_analytics(session_id="sess-1")

        assert isinstance(result, str)
        assert len(result) > 0
