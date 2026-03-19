#!/usr/bin/env python3
"""PondDB + OpenAI Agents SDK demo.

Three function tools (remember, recall, memory_stats) let an OpenAI Agent
persist and retrieve memories through PondDB's HTTP API.

Run:
    python examples/openai-agents-sdk/demo.py

Requires env vars:
    PONDDB_URL       — default http://localhost:8432
    PONDDB_API_KEY   — your PondDB API key
    OPENAI_API_KEY   — your OpenAI API key
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
from agents import Agent, Runner, function_tool

PONDDB_URL = os.getenv("PONDDB_URL", "http://localhost:8432")
PONDDB_API_KEY = os.getenv("PONDDB_API_KEY", "")

VALID_MEMORY_TYPES = {"working", "episodic", "semantic", "procedural", "shared"}
VALID_ACCESS_SCOPES = {"private", "workgroup", "namespace"}


def _headers() -> dict[str, str]:
    return {"X-API-Key": PONDDB_API_KEY, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@function_tool
def remember(
    agent_id: str,
    memory_type: str,
    content: dict,
    importance: float = 0.5,
    access_scope: str = "private",
) -> str:
    """Store a memory in PondDB for later retrieval.

    Args:
        agent_id: Identifier for the agent creating the memory.
        memory_type: One of working, episodic, semantic, procedural, shared.
        content: Arbitrary JSON dict with the memory payload.
        importance: Float 0-1 indicating how important this memory is.
        access_scope: One of private, workgroup, namespace.

    Returns:
        Confirmation string with the new memory ID, or an error message.
    """
    if memory_type not in VALID_MEMORY_TYPES:
        return f"Error: memory_type must be one of {sorted(VALID_MEMORY_TYPES)}"
    if access_scope not in VALID_ACCESS_SCOPES:
        return f"Error: access_scope must be one of {sorted(VALID_ACCESS_SCOPES)}"

    body = {
        "agent_id": agent_id,
        "memory_type": memory_type,
        "content": content,
        "importance": float(importance),
        "access_scope": access_scope,
    }

    try:
        resp = httpx.post(
            f"{PONDDB_URL}/memories",
            json=body,
            headers=_headers(),
            timeout=15,
        )
    except httpx.RequestError as exc:
        return f"Error: could not reach PondDB — {exc}"

    if resp.status_code in (200, 201):
        memory_id = resp.json().get("id", "unknown")
        return f"Memory stored successfully. ID: {memory_id}"

    return f"Failed to store memory ({resp.status_code}): {resp.text}"


@function_tool
def recall(
    content_contains: str = "",
    memory_type: str = "",
    min_importance: float = 0.0,
    limit: int = 10,
) -> str:
    """Search PondDB memories and return matching results.

    Args:
        content_contains: Keyword to search for inside memory content.
        memory_type: Filter to a specific memory type (optional).
        min_importance: Only return memories at or above this importance.
        limit: Maximum number of results to return.

    Returns:
        Formatted list of matching memories, or "No memories found."
    """
    params: dict[str, str | int | float] = {"limit": limit}
    if content_contains:
        params["content_contains"] = content_contains
    if memory_type:
        params["memory_type"] = memory_type
    if min_importance > 0.0:
        params["min_importance"] = min_importance

    try:
        resp = httpx.get(
            f"{PONDDB_URL}/memories/search",
            params=params,
            headers=_headers(),
            timeout=15,
        )
    except httpx.RequestError as exc:
        return f"Error: could not reach PondDB — {exc}"

    if resp.status_code != 200:
        return f"Failed to search memories ({resp.status_code}): {resp.text}"

    memories = resp.json()
    if not memories:
        return "No memories found."

    lines = [f"Found {len(memories)} memory/memories:\n"]
    for mem in memories:
        mid = mem.get("id", "?")
        mtype = mem.get("memory_type", "?")
        importance = mem.get("importance", 0)
        content = mem.get("content", {})
        content_str = json.dumps(content, ensure_ascii=False)
        lines.append(f"  [{mid}] type={mtype} importance={importance}\n    {content_str}")

    return "\n".join(lines)


@function_tool
def memory_stats(session_id: str = "") -> str:
    """Query PondDB analytics for a summary of stored memories.

    Args:
        session_id: Optional session identifier for the SQL execution context.

    Returns:
        Formatted table of memory counts and average importance by type.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    sql = (
        "SELECT memory_type, COUNT(*) AS count, "
        "ROUND(AVG(importance), 3) AS avg_importance "
        "FROM agent_memories "
        "GROUP BY memory_type "
        "ORDER BY count DESC"
    )

    try:
        resp = httpx.post(
            f"{PONDDB_URL}/pondapi/execute",
            json={"session_id": session_id, "sql": sql},
            headers=_headers(),
            timeout=30,
        )
    except httpx.RequestError as exc:
        return f"Error: could not reach PondDB — {exc}"

    if resp.status_code != 200:
        return f"Failed to run stats query ({resp.status_code}): {resp.text}"

    data = resp.json()
    rows = data.get("rows", [])
    if not rows:
        return "No memory statistics available (no memories stored yet)."

    header = f"{'memory_type':<15} {'count':>8} {'avg_importance':>15}"
    separator = "-" * len(header)
    lines = [header, separator]
    for row in rows:
        mtype = row.get("memory_type", "?")
        count = row.get("count", 0)
        avg_imp = row.get("avg_importance", 0)
        lines.append(f"{mtype:<15} {count:>8} {avg_imp:>15}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

memory_agent = Agent(
    name="PondDB Memory Agent",
    instructions=(
        "You are a research assistant with persistent memory via PondDB. "
        "ALWAYS call recall() before answering any question — context from "
        "past interactions improves your answers. "
        "ALWAYS call remember() after discovering something important so "
        "future interactions can benefit. "
        "Use memory_stats() when asked about what you know or how your "
        "memory is organized."
    ),
    tools=[remember, recall, memory_stats],
)


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== PondDB + OpenAI Agents SDK Demo ===\n")

    # Interaction 1: store a finding and then query for related memories
    print("--- Interaction 1: Storing a market finding ---")
    result1 = Runner.run_sync(
        memory_agent,
        (
            "I just learned that 78% of enterprise customers prefer SQL-based "
            "agent memory over vector stores for auditability. Store this as a "
            "semantic memory and then tell me what you know about enterprise customers."
        ),
    )
    print(result1.final_output)
    print()

    # Interaction 2: ask the agent to summarise what it knows
    print("--- Interaction 2: Memory statistics ---")
    result2 = Runner.run_sync(
        memory_agent,
        "Give me a breakdown of everything you have stored in memory so far.",
    )
    print(result2.final_output)


if __name__ == "__main__":
    main()
