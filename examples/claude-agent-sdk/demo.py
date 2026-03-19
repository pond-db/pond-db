#!/usr/bin/env python3
# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Claude Agent SDK + PondDB MCP Demo

Two subagents — Researcher and Analyst — collaborate through PondDB's shared
memory via the Model Context Protocol (MCP).

MCP flow for each tool call:
  1. Claude Agent SDK calls the subagent with a prompt.
  2. The subagent's LLM decides to call a tool (e.g. ponddb_remember).
  3. The SDK serialises the tool call as JSON-RPC 2.0 and pipes it to the
     mcp-server-ponddb subprocess over stdio.
  4. mcp-server-ponddb forwards the call as an HTTP request to PondDB's REST API.
  5. PondDB stores/retrieves the memory in its SQLite database.
  6. The JSON result travels back: PondDB → MCP server → SDK → LLM context.

Both subagents share the same PONDDB_WORKGROUP, so memories the researcher
stores with access_scope="workgroup" are immediately visible to the analyst.
"""

from __future__ import annotations

import os
from typing import Any

# The SDK is imported at module level so tests can verify the import path.
# When running tests, claude_agent_sdk is mocked via sys.modules before import.
from claude_agent_sdk import Agent, Subagent  # type: ignore[import]

# ---------------------------------------------------------------------------
# MCP server config block — passed to each subagent so they get their own
# long-lived mcp-server-ponddb subprocess connected to the same workgroup.
# ---------------------------------------------------------------------------

def _mcp_server_config(workgroup: str = "demo") -> dict[str, Any]:
    """Build the MCP server config for a PondDB-connected subagent.

    The command is the entrypoint installed by: pip install mcp-server-ponddb
    It reads PONDDB_URL and PONDDB_API_KEY from the environment block below.
    Both agents share the same PONDDB_WORKGROUP so workgroup-scoped memories
    are visible across both without any explicit grant.
    """
    return {
        # 'command' is the MCP server executable (runs as a subprocess, stdio transport).
        "command": "mcp-server-ponddb",
        "args": [],
        "env": {
            "PONDDB_URL": os.environ.get("PONDDB_URL", "http://localhost:8432"),
            "PONDDB_API_KEY": os.environ.get("PONDDB_API_KEY", ""),
            # Same workgroup = shared memory namespace without needing explicit grants.
            "PONDDB_WORKGROUP": os.environ.get("PONDDB_WORKGROUP", "demo"),
        },
    }


# ---------------------------------------------------------------------------
# Subagent configs — exported at module level so tests can inspect them.
# ---------------------------------------------------------------------------

RESEARCHER_CONFIG: dict[str, Any] = {
    "name": "researcher",
    # Haiku is fast and cheap for tool-heavy agents that don't need deep reasoning.
    "model": "claude-haiku-4-5",
    "mcp_servers": [_mcp_server_config()],
    # Restrict to only the tools this role needs.
    # ponddb_recall lets the researcher check for duplicate findings before storing.
    "allowed_tools": ["ponddb_remember", "ponddb_recall"],
    "system_prompt": (
        "You are a research agent. Your job is to gather business insights and "
        "store them as durable shared memories using ponddb_remember. "
        "Use access_scope='workgroup' and importance >= 0.8 for key findings so "
        "your analyst teammate can discover them. Before storing, use ponddb_recall "
        "to avoid duplicate entries."
    ),
}

ANALYST_CONFIG: dict[str, Any] = {
    "name": "analyst",
    "model": "claude-haiku-4-5",
    "mcp_servers": [_mcp_server_config()],
    # Analyst reads research memories, runs SQL analytics, and rates memory quality.
    # It does NOT need ponddb_remember — it synthesises, not stores raw findings.
    "allowed_tools": ["ponddb_recall", "ponddb_query", "ponddb_feedback"],
    "system_prompt": (
        "You are a data analyst. Use ponddb_recall to read research findings stored "
        "by the researcher agent. Use ponddb_query for SQL analytics against the "
        "PondDB memory store (e.g. utility trends, access counts). Use ponddb_feedback "
        "to rate the usefulness of memories you read — this improves future retrieval "
        "ranking. Synthesise findings into a concise business recommendation."
    ),
}


# ---------------------------------------------------------------------------
# Demo orchestration
# ---------------------------------------------------------------------------

def run_demo() -> None:
    """Run the multi-agent demo: researcher stores findings, analyst analyses them.

    What happens under the hood:
      Researcher subagent:
        1. Receives the prompt below.
        2. Calls ponddb_remember (MCP tool call → JSON-RPC over stdio → HTTP POST /memories).
        3. PondDB stores the memory in SQLite with workgroup scope.
        4. Returns a summary to the orchestrating Agent.

      Analyst subagent:
        1. Receives the prompt below.
        2. Calls ponddb_recall to fetch the researcher's findings
           (MCP tool call → GET /memories/search with same workgroup header).
        3. Calls ponddb_query to run SQL against PondDB's memory tables.
        4. Calls ponddb_feedback to rate the memories it found useful.
        5. Returns a business recommendation.
    """
    print("=== Claude Agent SDK + PondDB MCP Demo ===\n")

    # The orchestrating Agent routes prompts to the right subagent and assembles
    # the final response. It does not have direct MCP access itself.
    orchestrator = Agent(
        model="claude-sonnet-4-5",
        subagents=[
            Subagent(**RESEARCHER_CONFIG),
            Subagent(**ANALYST_CONFIG),
        ],
        system_prompt=(
            "You are the Chief of Staff. Coordinate the researcher and analyst "
            "subagents to produce a business intelligence report. "
            "First, instruct the researcher to store key findings. "
            "Then, instruct the analyst to read those findings and produce a recommendation."
        ),
    )

    # Phase 1: Researcher stores findings in PondDB via MCP.
    print("Phase 1: Researcher storing findings via ponddb_remember ...\n")
    orchestrator.run(
        agent="researcher",
        prompt=(
            "Store these market research findings in PondDB as shared workgroup memories "
            "with importance=0.9:\n"
            "1. Top customers by ARR: Acme ($500K), Beta ($350K), Gamma ($200K).\n"
            "2. Acme is evaluating two competitors — highest churn risk.\n"
            "3. Beta just renewed for 2 years — very healthy.\n"
            "Use memory_type='semantic', agent_id='researcher', access_scope='workgroup'."
        ),
    )

    # Phase 2: Analyst reads the findings and runs SQL analytics via MCP.
    print("\nPhase 2: Analyst reading findings via ponddb_recall and ponddb_query ...\n")
    result = orchestrator.run(
        agent="analyst",
        prompt=(
            "Use ponddb_recall to fetch all workgroup memories with min_importance=0.8. "
            "Then use ponddb_query to run:\n"
            "  SELECT agent_id, memory_type, COUNT(*) as n, "
            "         ROUND(AVG(importance),2) as avg_imp "
            "  FROM agent_memories GROUP BY 1,2 ORDER BY avg_imp DESC;\n"
            "Rate each memory you found useful with ponddb_feedback reward=0.8. "
            "Finally, write a 3-bullet business recommendation."
        ),
    )

    print("\n=== Analyst Recommendation ===")
    print(result)
    print("\n=== Demo complete. All operations logged in PondDB's memory_access_log. ===")


if __name__ == "__main__":
    run_demo()
