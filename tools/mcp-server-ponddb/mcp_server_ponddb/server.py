# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""MCP server exposing 5 PondDB memory tools via JSON-RPC over stdio."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp_server_ponddb.client import PondDBClient

# ── Tool definitions ──────────────────────────────────────────


TOOLS = [
    {
        "name": "ponddb_remember",
        "description": "Store a memory in PondDB. Types: working, episodic, semantic, procedural, shared.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "memory_type": {"type": "string", "enum": ["working", "episodic", "semantic", "procedural", "shared"]},
                "content": {"type": "object"},
                "access_scope": {"type": "string", "enum": ["private", "workgroup", "namespace"], "default": "private"},
                "importance": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "memory_key": {"type": "string"},
            },
            "required": ["agent_id", "memory_type", "content"],
        },
    },
    {
        "name": "ponddb_recall",
        "description": "Search memories in PondDB. Returns memories matching filters, sorted by utility.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "memory_type": {"type": "string"},
                "content_contains": {"type": "string"},
                "min_importance": {"type": "number"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "ponddb_query",
        "description": "Run a SQL query against PondDB via PondAPI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "ponddb_forget",
        "description": "Soft-delete a memory from PondDB.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "ponddb_feedback",
        "description": "Provide feedback on a memory's usefulness. Reward between -1.0 and 1.0.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "reward": {"type": "number", "minimum": -1, "maximum": 1},
            },
            "required": ["memory_id", "reward"],
        },
    },
]


def _get_client() -> PondDBClient:
    url = os.environ.get("PONDDB_URL", "http://localhost:8432")
    key = os.environ.get("PONDDB_API_KEY", "")
    wg = os.environ.get("PONDDB_WORKGROUP", "default")
    return PondDBClient(url, key, wg)


def handle_tool_call(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch a tool call to the PondDB client."""
    client = _get_client()

    if name == "ponddb_remember":
        return client.remember(
            agent_id=arguments["agent_id"],
            memory_type=arguments["memory_type"],
            content=arguments["content"],
            access_scope=arguments.get("access_scope", "private"),
            importance=arguments.get("importance", 0.5),
            memory_key=arguments.get("memory_key"),
        )
    elif name == "ponddb_recall":
        return client.recall(
            agent_id=arguments.get("agent_id"),
            memory_type=arguments.get("memory_type"),
            content_contains=arguments.get("content_contains"),
            min_importance=arguments.get("min_importance"),
            limit=arguments.get("limit", 20),
        )
    elif name == "ponddb_query":
        return client.query(sql=arguments["sql"])
    elif name == "ponddb_forget":
        return client.forget(memory_id=arguments["memory_id"])
    elif name == "ponddb_feedback":
        return client.feedback(
            memory_id=arguments["memory_id"],
            reward=arguments["reward"],
        )
    else:
        raise ValueError(f"Unknown tool: {name}")


def handle_jsonrpc(request: dict[str, Any]) -> dict[str, Any]:
    """Process a single JSON-RPC 2.0 request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "mcp-server-ponddb", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "notifications/initialized":
            return None  # No response for notifications
        elif method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = handle_tool_call(name, arguments)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, default=str)}]},
            }
        else:
            return {
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
    except Exception as exc:
        return {
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def main() -> None:
    """Run the MCP server over stdio (JSON-RPC 2.0)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_jsonrpc(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
