"""Integration tests for PondDB CrewAI tools.

These tests do NOT require a running PondDB instance or real API keys.
All HTTP calls are mocked via unittest.mock.patch on httpx.
"""

from __future__ import annotations

import json
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# Allow importing demo.py from the same directory without installing crewai.
sys.path.insert(0, os.path.dirname(__file__))

# ---------------------------------------------------------------------------
# Stub out crewai so the module imports without crewai installed.
# ---------------------------------------------------------------------------

_fake_crewai = MagicMock()


class _BaseToolStub:
    """Minimal BaseTool stand-in that lets RememberTool / RecallTool inherit."""

    name: str = ""
    description: str = ""

    def _run(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def run(self, *args, **kwargs):
        return self._run(*args, **kwargs)


_fake_crewai.tools.BaseTool = _BaseToolStub

# Pydantic is a real dependency — use the real one for input schemas.
import pydantic  # noqa: E402

_fake_crewai_tools = MagicMock()

sys.modules.setdefault("crewai", _fake_crewai)
sys.modules.setdefault("crewai.tools", _fake_crewai.tools)

# Now import the module under test using explicit file path
# to avoid collision with other examples/*/demo.py modules.
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "crewai_demo", os.path.join(os.path.dirname(__file__), "demo.py")
)
demo = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(demo)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


# ---------------------------------------------------------------------------
# Test 1: RememberTool._run() formats the correct POST body and returns
#         a confirmation string containing the memory id.
# ---------------------------------------------------------------------------


class TestRememberTool:
    def setup_method(self):
        self.tool = demo.RememberTool()

    def test_remember_posts_correct_body_and_returns_confirmation(self):
        fake_id = "mem-abc-123"
        response_body = {"id": fake_id, "memory_type": "episodic"}

        with patch("demo.httpx.post", return_value=_mock_response(201, response_body)) as mock_post:
            result = self.tool._run(
                agent_id="researcher",
                memory_type="episodic",
                content={"finding": "CrewAI is great"},
                importance=0.85,
                access_scope="workgroup",
            )

        # Verify the POST was called once
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args

        # URL must hit /memories
        assert call_kwargs[0][0].endswith("/memories") or "/memories" in call_kwargs[1].get(
            "url", call_kwargs[0][0]
        )

        # JSON body must contain required fields
        sent_json = call_kwargs[1].get("json") or call_kwargs[0][1]
        assert sent_json["agent_id"] == "researcher"
        assert sent_json["memory_type"] == "episodic"
        assert sent_json["importance"] == 0.85
        assert sent_json["access_scope"] == "workgroup"
        assert sent_json["content"] == {"finding": "CrewAI is great"}

        # Confirmation string must include the id
        assert fake_id in result

    def test_remember_includes_api_key_header(self):
        with patch("demo.httpx.post", return_value=_mock_response(201, {"id": "x"})) as mock_post:
            with patch.dict(os.environ, {"PONDDB_API_KEY": "test-key-999"}):
                # Re-read the key the way the module does (via _headers())
                self.tool._run(
                    agent_id="writer",
                    memory_type="semantic",
                    content={"fact": "hello"},
                    importance=0.5,
                    access_scope="private",
                )

        headers_sent = mock_post.call_args[1].get("headers", {})
        # The header must be present; value depends on env at import time,
        # so just check the key exists.
        assert "X-API-Key" in headers_sent


# ---------------------------------------------------------------------------
# Test 2: RecallTool._run() formats correct GET params and returns formatted
#         results when the API returns memories.
# ---------------------------------------------------------------------------


class TestRecallToolWithResults:
    def setup_method(self):
        self.tool = demo.RecallTool()

    def test_recall_formats_get_params_and_returns_results(self):
        memories = [
            {
                "id": "m1",
                "agent_id": "researcher",
                "memory_type": "episodic",
                "importance": 0.9,
                "content": {"finding": "PondDB scales well"},
            },
            {
                "id": "m2",
                "agent_id": "analyst",
                "memory_type": "semantic",
                "importance": 0.7,
                "content": {"fact": "CrewAI supports tool calling"},
            },
        ]

        with patch(
            "demo.httpx.get", return_value=_mock_response(200, memories)
        ) as mock_get:
            result = self.tool._run(
                content_contains="PondDB",
                memory_type="episodic",
                min_importance=0.6,
                limit=10,
            )

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args

        # URL must hit /memories/search
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        assert "/memories/search" in url

        # Params must include the search parameters
        params = call_kwargs[1].get("params", {})
        assert params.get("content_contains") == "PondDB"
        assert params.get("memory_type") == "episodic"
        assert params.get("min_importance") == 0.6
        assert params.get("limit") == 10

        # Result must contain agent ids and content
        assert "researcher" in result or "m1" in result
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Test 3: RecallTool._run() returns a specific message when no memories found.
# ---------------------------------------------------------------------------


class TestRecallToolEmpty:
    def setup_method(self):
        self.tool = demo.RecallTool()

    def test_recall_empty_results_returns_no_memories_message(self):
        with patch("demo.httpx.get", return_value=_mock_response(200, [])):
            result = self.tool._run(
                content_contains="nonexistent topic",
                memory_type="working",
                min_importance=0.9,
                limit=5,
            )

        assert result == "No relevant memories found."


# ---------------------------------------------------------------------------
# Test 4: Analytics query formats the SQL POST body correctly.
# ---------------------------------------------------------------------------


class TestAnalyticsQuery:
    def test_run_analytics_posts_correct_sql(self):
        fake_rows = [
            {"agent_id": "researcher", "memory_type": "episodic", "count": 3},
        ]
        response_body = {"rows": fake_rows, "status": "complete"}

        with patch(
            "demo.httpx.post", return_value=_mock_response(200, response_body)
        ) as mock_post:
            result = demo.run_analytics(session_id="sess-xyz")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        sent_json = call_kwargs[1].get("json") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})

        # URL must target /pondapi/execute
        assert "/pondapi/execute" in url

        # Must include session_id and sql
        assert sent_json.get("session_id") == "sess-xyz"
        assert "sql" in sent_json
        assert len(sent_json["sql"]) > 0

        # Result must contain the row data
        assert "researcher" in result or "episodic" in result


# ---------------------------------------------------------------------------
# Test 5: RememberTool._run() handles API errors gracefully.
# ---------------------------------------------------------------------------


class TestRememberToolErrorHandling:
    def setup_method(self):
        self.tool = demo.RememberTool()

    def test_remember_returns_error_message_on_api_failure(self):
        error_body = {"detail": "Unauthorized"}

        with patch("demo.httpx.post", return_value=_mock_response(401, error_body)):
            result = self.tool._run(
                agent_id="rogue",
                memory_type="working",
                content={"note": "secret"},
                importance=0.5,
                access_scope="private",
            )

        # Must not raise — must return an error description string
        assert isinstance(result, str)
        assert len(result) > 0
        # Should mention the failure in some way
        assert "401" in result or "error" in result.lower() or "fail" in result.lower()
