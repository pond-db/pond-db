"""Integration tests for the LangGraph + PondDB memory research workflow.

These tests do NOT require API keys or a running PondDB instance.
All httpx calls are mocked.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure we import from THIS directory's demo.py, not another example's.
sys.path.insert(0, os.path.dirname(__file__))

# Stub langchain_core so demo.py can be imported without it installed.
import types as _t

_lc = _t.ModuleType("langchain_core")
_lc_tools = _t.ModuleType("langchain_core.tools")
_lc_tools.tool = lambda f: f  # type: ignore[attr-defined]
sys.modules.setdefault("langchain_core", _lc)
sys.modules.setdefault("langchain_core.tools", _lc_tools)

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "langgraph_demo", os.path.join(os.path.dirname(__file__), "demo.py")
)
demo = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(demo)  # type: ignore[union-attr]


def _resp(status: int, body: dict | list) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


class TestStoreFinding:
    def test_post_body_fields(self) -> None:
        fake = _resp(201, {"id": "mem-abc"})
        with patch.object(demo.httpx, "post", return_value=fake) as mp:
            demo._store_finding_impl("researcher-1", "Climate data", 0.9)
        body = mp.call_args.kwargs.get("json", {})
        assert body["agent_id"] == "researcher-1"
        assert body["memory_type"] == "episodic"
        assert body["importance"] == 0.9
        assert "Climate" in body["content"]["text"]

    def test_returns_memory_id(self) -> None:
        fake = _resp(201, {"id": "mem-xyz"})
        with patch.object(demo.httpx, "post", return_value=fake):
            result = demo._store_finding_impl("a", "note", 0.5)
        assert "mem-xyz" in result

    def test_handles_error(self) -> None:
        fake = _resp(500, {"detail": "fail"})
        with patch.object(demo.httpx, "post", return_value=fake):
            result = demo._store_finding_impl("a", "note", 0.5)
        assert "Failed" in result


class TestSearchFindings:
    def test_formats_params(self) -> None:
        fake = _resp(200, [{"id": "m1", "content": {"text": "data"}, "importance": 0.8}])
        with patch.object(demo.httpx, "get", return_value=fake) as mg:
            demo._search_findings_impl("ocean", 0.5, 10)
        params = mg.call_args.kwargs.get("params", {})
        assert params["content_contains"] == "ocean"
        assert params["limit"] == 10

    def test_returns_formatted_results(self) -> None:
        fake = _resp(200, [{"id": "m1", "content": {"text": "Ice melting"}, "importance": 0.75}])
        with patch.object(demo.httpx, "get", return_value=fake):
            result = demo._search_findings_impl("ice", 0.0, 5)
        assert "Ice melting" in result

    def test_empty_returns_message(self) -> None:
        fake = _resp(200, [])
        with patch.object(demo.httpx, "get", return_value=fake):
            result = demo._search_findings_impl("nothing", 0.0, 5)
        assert result == "No prior findings found."


class TestAnalytics:
    def test_sends_sql(self) -> None:
        fake = _resp(200, {"rows": [{"memory_type": "episodic", "count": 3, "avg_importance": 0.7}]})
        with patch.object(demo.httpx, "post", return_value=fake) as mp:
            demo.run_analytics(session_id="sess-1")
        body = mp.call_args.kwargs.get("json", {})
        assert body["session_id"] == "sess-1"
        assert "agent_memories" in body["sql"]
