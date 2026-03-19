# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Integration tests for the Claude Agent SDK + PondDB MCP demo.

These tests do NOT require API keys or a running PondDB instance.
They validate config structure, tool availability, and import correctness.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mcp_server_config(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return the canonical MCP server config dict for a PondDB-connected agent."""
    config: dict[str, Any] = {
        "command": "mcp-server-ponddb",
        "args": [],
        "env": env or {
            "PONDDB_URL": "http://localhost:8432",
            "PONDDB_API_KEY": "test-key",
            "PONDDB_WORKGROUP": "demo",
        },
    }
    return config


def _tool_names_from_config(config: dict[str, Any]) -> list[str]:
    """Extract allowed tool names from an agent config dict."""
    return config.get("allowed_tools", [])


# ---------------------------------------------------------------------------
# Test 1: MCP server config is correctly structured
# ---------------------------------------------------------------------------

class TestMCPServerConfig:
    def test_config_has_required_keys(self) -> None:
        """MCP server config must have command, args, and env keys."""
        config = _make_mcp_server_config()
        assert "command" in config, "config must have 'command'"
        assert "args" in config, "config must have 'args'"
        assert "env" in config, "config must have 'env'"

    def test_config_command_is_mcp_server(self) -> None:
        """The command must point to the mcp-server-ponddb entrypoint."""
        config = _make_mcp_server_config()
        assert config["command"] == "mcp-server-ponddb"

    def test_config_env_has_ponddb_url(self) -> None:
        """The env block must include PONDDB_URL."""
        config = _make_mcp_server_config()
        assert "PONDDB_URL" in config["env"]

    def test_config_env_has_ponddb_api_key(self) -> None:
        """The env block must include PONDDB_API_KEY."""
        config = _make_mcp_server_config()
        assert "PONDDB_API_KEY" in config["env"]

    def test_config_env_has_workgroup(self) -> None:
        """The env block must include PONDDB_WORKGROUP so agents share memory."""
        config = _make_mcp_server_config()
        assert "PONDDB_WORKGROUP" in config["env"]


# ---------------------------------------------------------------------------
# Test 2: Researcher agent config has remember tool available
# ---------------------------------------------------------------------------

class TestResearcherAgentConfig:
    """The researcher subagent must be allowed to use ponddb_remember."""

    def _researcher_config(self) -> dict[str, Any]:
        return {
            "name": "researcher",
            "model": "claude-haiku-4-5",
            "mcp_servers": [_make_mcp_server_config()],
            "allowed_tools": ["ponddb_remember", "ponddb_recall"],
            "system_prompt": "You are a research agent. Store findings with ponddb_remember.",
        }

    def test_researcher_has_remember_tool(self) -> None:
        config = self._researcher_config()
        assert "ponddb_remember" in _tool_names_from_config(config)

    def test_researcher_has_recall_tool(self) -> None:
        """Researcher should also be able to recall to avoid duplicates."""
        config = self._researcher_config()
        assert "ponddb_recall" in _tool_names_from_config(config)

    def test_researcher_mcp_server_present(self) -> None:
        config = self._researcher_config()
        assert len(config["mcp_servers"]) >= 1

    def test_researcher_does_not_have_query_tool(self) -> None:
        """SQL analytics (ponddb_query) is the analyst's job, not researcher's."""
        config = self._researcher_config()
        assert "ponddb_query" not in _tool_names_from_config(config)

    def test_researcher_system_prompt_mentions_remember(self) -> None:
        config = self._researcher_config()
        assert "ponddb_remember" in config["system_prompt"]


# ---------------------------------------------------------------------------
# Test 3: Analyst agent config has recall and query tools
# ---------------------------------------------------------------------------

class TestAnalystAgentConfig:
    """The analyst subagent must be allowed to use ponddb_recall and ponddb_query."""

    def _analyst_config(self) -> dict[str, Any]:
        return {
            "name": "analyst",
            "model": "claude-haiku-4-5",
            "mcp_servers": [_make_mcp_server_config()],
            "allowed_tools": ["ponddb_recall", "ponddb_query", "ponddb_feedback"],
            "system_prompt": (
                "You are an analyst. Use ponddb_recall to read research findings, "
                "ponddb_query for SQL analytics, and ponddb_feedback to rate memory quality."
            ),
        }

    def test_analyst_has_recall_tool(self) -> None:
        config = self._analyst_config()
        assert "ponddb_recall" in _tool_names_from_config(config)

    def test_analyst_has_query_tool(self) -> None:
        config = self._analyst_config()
        assert "ponddb_query" in _tool_names_from_config(config)

    def test_analyst_has_feedback_tool(self) -> None:
        """Analyst rates the usefulness of memories it reads."""
        config = self._analyst_config()
        assert "ponddb_feedback" in _tool_names_from_config(config)

    def test_analyst_mcp_server_uses_same_workgroup(self) -> None:
        """Analyst must share the same workgroup as researcher for shared access."""
        config = self._analyst_config()
        wg = config["mcp_servers"][0]["env"]["PONDDB_WORKGROUP"]
        researcher_wg = _make_mcp_server_config()["env"]["PONDDB_WORKGROUP"]
        assert wg == researcher_wg, (
            f"Analyst workgroup '{wg}' must match researcher workgroup '{researcher_wg}'"
        )

    def test_analyst_system_prompt_mentions_sql(self) -> None:
        config = self._analyst_config()
        assert "ponddb_query" in config["system_prompt"] or "SQL" in config["system_prompt"]


# ---------------------------------------------------------------------------
# Test 4: Demo script imports correctly (mock the SDK)
# ---------------------------------------------------------------------------

class TestDemoImports:
    """Verify demo.py imports correctly when claude_agent_sdk is mocked."""

    def test_demo_imports_with_mocked_sdk(self) -> None:
        """demo.py must import without error when claude_agent_sdk is available."""
        # Build a minimal mock for the SDK so demo.py can be imported
        mock_sdk = MagicMock(name="claude_agent_sdk")
        mock_sdk.Agent = MagicMock()
        mock_sdk.Subagent = MagicMock()

        with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
            # Remove demo from sys.modules if it was loaded in a previous test run
            sys.modules.pop("demo", None)
            import importlib.util
            import os

            demo_path = os.path.join(os.path.dirname(__file__), "demo.py")
            spec = importlib.util.spec_from_file_location("demo", demo_path)
            assert spec is not None, "demo.py must exist"
            module = importlib.util.module_from_spec(spec)
            # Loading the module should not raise
            # (top-level code is intentionally kept in functions, not executed at import)
            spec.loader.exec_module(module)  # type: ignore[union-attr]

    def test_demo_exports_agent_configs(self) -> None:
        """demo.py must expose RESEARCHER_CONFIG and ANALYST_CONFIG at module level."""
        mock_sdk = MagicMock(name="claude_agent_sdk")
        mock_sdk.Agent = MagicMock()
        mock_sdk.Subagent = MagicMock()

        with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
            sys.modules.pop("demo", None)
            import importlib.util
            import os

            demo_path = os.path.join(os.path.dirname(__file__), "demo.py")
            spec = importlib.util.spec_from_file_location("demo", demo_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            assert hasattr(module, "RESEARCHER_CONFIG"), (
                "demo.py must define RESEARCHER_CONFIG at module level"
            )
            assert hasattr(module, "ANALYST_CONFIG"), (
                "demo.py must define ANALYST_CONFIG at module level"
            )

    def test_demo_exports_run_demo_function(self) -> None:
        """demo.py must expose a run_demo() callable."""
        mock_sdk = MagicMock(name="claude_agent_sdk")
        mock_sdk.Agent = MagicMock()
        mock_sdk.Subagent = MagicMock()

        with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
            sys.modules.pop("demo", None)
            import importlib.util
            import os

            demo_path = os.path.join(os.path.dirname(__file__), "demo.py")
            spec = importlib.util.spec_from_file_location("demo", demo_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            assert callable(getattr(module, "run_demo", None)), (
                "demo.py must define a callable run_demo()"
            )


# ---------------------------------------------------------------------------
# Test 5: MCP tool schema validation
# ---------------------------------------------------------------------------

class TestMCPToolSchemas:
    """Validate that the 5 PondDB MCP tools have the required schema fields."""

    @pytest.fixture()
    def ponddb_tools(self) -> list[dict[str, Any]]:
        """Load TOOLS list directly from mcp_server_ponddb.server (no network needed)."""
        # Dynamically resolve the package path so this test works from any cwd
        import importlib.util
        import os

        server_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "tools", "mcp-server-ponddb",
            "mcp_server_ponddb", "server.py",
        )
        server_path = os.path.normpath(server_path)
        spec = importlib.util.spec_from_file_location("mcp_server_ponddb.server", server_path)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)

        # Patch the client import to avoid network calls
        mock_client_mod = MagicMock()
        with patch.dict(sys.modules, {"mcp_server_ponddb.client": mock_client_mod,
                                       "mcp_server_ponddb": MagicMock()}):
            spec.loader.exec_module(mod)  # type: ignore[union-attr]

        return mod.TOOLS  # type: ignore[attr-defined]

    def test_five_tools_exposed(self, ponddb_tools: list[dict[str, Any]]) -> None:
        assert len(ponddb_tools) == 5, f"Expected 5 tools, got {len(ponddb_tools)}"

    def test_all_tools_have_name_and_description(self, ponddb_tools: list[dict[str, Any]]) -> None:
        for tool in ponddb_tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool {tool['name']} missing 'description'"

    def test_all_tools_have_input_schema(self, ponddb_tools: list[dict[str, Any]]) -> None:
        for tool in ponddb_tools:
            assert "inputSchema" in tool, f"Tool {tool['name']} missing 'inputSchema'"

    def test_expected_tool_names_present(self, ponddb_tools: list[dict[str, Any]]) -> None:
        names = {t["name"] for t in ponddb_tools}
        expected = {
            "ponddb_remember",
            "ponddb_recall",
            "ponddb_query",
            "ponddb_forget",
            "ponddb_feedback",
        }
        assert names == expected, f"Tool names mismatch. Got: {names}"

    def test_remember_tool_requires_agent_id_and_content(
        self, ponddb_tools: list[dict[str, Any]]
    ) -> None:
        remember = next(t for t in ponddb_tools if t["name"] == "ponddb_remember")
        required = remember["inputSchema"].get("required", [])
        assert "agent_id" in required
        assert "content" in required

    def test_query_tool_requires_sql(self, ponddb_tools: list[dict[str, Any]]) -> None:
        query = next(t for t in ponddb_tools if t["name"] == "ponddb_query")
        required = query["inputSchema"].get("required", [])
        assert "sql" in required

    def test_forget_tool_requires_memory_id(self, ponddb_tools: list[dict[str, Any]]) -> None:
        forget = next(t for t in ponddb_tools if t["name"] == "ponddb_forget")
        required = forget["inputSchema"].get("required", [])
        assert "memory_id" in required

    def test_feedback_tool_requires_reward(self, ponddb_tools: list[dict[str, Any]]) -> None:
        feedback = next(t for t in ponddb_tools if t["name"] == "ponddb_feedback")
        required = feedback["inputSchema"].get("required", [])
        assert "reward" in required
