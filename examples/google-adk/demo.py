"""Google ADK + PondDB: Persistent Memory Agent Demo.

An agent that stores and retrieves memories across sessions using PondDB's
memory API.  Four tools are exposed as FunctionTools:

  remember          — store a memory (POST /memories)
  recall            — search memories (GET /memories/search)
  rate_memory       — give feedback on a memory (POST /memories/{id}/feedback)
  memory_analytics  — run SQL analytics via PondDB (POST /pondapi/execute)

Usage:
    PONDDB_URL=http://localhost:8432 PONDDB_API_KEY=pk_... python demo.py
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PONDDB_URL: str = os.getenv("PONDDB_URL", "http://localhost:8432")
PONDDB_API_KEY: str = os.getenv("PONDDB_API_KEY", "")


def _headers() -> dict[str, str]:
    """Return auth headers required by every PondDB request."""
    return {"X-API-Key": PONDDB_API_KEY, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Tool: remember
# ---------------------------------------------------------------------------


def remember(
    agent_id: str,
    memory_type: str,
    content: dict[str, Any],
    importance: float,
    access_scope: str,
) -> str:
    """Store a memory in PondDB.

    Args:
        agent_id: Unique identifier for the agent creating this memory.
        memory_type: One of working, episodic, semantic, procedural, shared.
        content: Arbitrary JSON payload (the actual memory data).
        importance: Float 0-1 rating how important this memory is.
        access_scope: One of private, workgroup, namespace.

    Returns:
        Confirmation string containing the new memory id.
    """
    resp = httpx.post(
        f"{PONDDB_URL}/memories",
        json={
            "agent_id": agent_id,
            "memory_type": memory_type,
            "content": content,
            "importance": importance,
            "access_scope": access_scope,
        },
        headers=_headers(),
        timeout=15,
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        return f"Memory stored successfully. id={data.get('id')} type={data.get('memory_type')}"
    return f"Failed to store memory ({resp.status_code}): {resp.text}"


# ---------------------------------------------------------------------------
# Tool: recall
# ---------------------------------------------------------------------------


def recall(
    content_contains: str = "",
    memory_type: str = "",
    min_importance: float = 0.0,
    limit: int = 10,
) -> str:
    """Search memories in PondDB.

    Args:
        content_contains: Substring to search for inside memory content.
        memory_type: Filter by type (working/episodic/semantic/procedural/shared).
        min_importance: Only return memories at or above this importance.
        limit: Maximum number of results to return.

    Returns:
        Formatted string with matching memories, or a message when none found.
    """
    params: dict[str, Any] = {
        "content_contains": content_contains,
        "memory_type": memory_type,
        "min_importance": min_importance,
        "limit": limit,
    }
    resp = httpx.get(
        f"{PONDDB_URL}/memories/search",
        params=params,
        headers=_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        return f"Search failed ({resp.status_code}): {resp.text}"

    memories = resp.json()
    if not memories:
        return "No memories found matching your search criteria."

    lines = [f"Found {len(memories)} memory/memories:"]
    for mem in memories:
        lines.append(
            f"  [{mem.get('id')}] ({mem.get('memory_type')}, "
            f"importance={mem.get('importance')}) "
            f"{json.dumps(mem.get('content', {}))}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: rate_memory
# ---------------------------------------------------------------------------


def rate_memory(memory_id: str, reward: float) -> str:
    """Provide reinforcement feedback on a stored memory.

    Args:
        memory_id: The id of the memory to rate.
        reward: Float in [-1, 1]. Positive rewards reinforce, negative penalise.

    Returns:
        Confirmation string.
    """
    resp = httpx.post(
        f"{PONDDB_URL}/memories/{memory_id}/feedback",
        json={"reward": reward},
        headers=_headers(),
        timeout=15,
    )
    if resp.status_code == 200:
        return f"Feedback recorded for memory {memory_id} (reward={reward})"
    return f"Feedback failed ({resp.status_code}): {resp.text}"


# ---------------------------------------------------------------------------
# Tool: memory_analytics
# ---------------------------------------------------------------------------


def memory_analytics(session_id: str) -> str:
    """Run SQL analytics over agent memories via PondDB.

    Queries a summary of memory counts and average importance grouped by type.

    Args:
        session_id: PondDB session identifier for the analytics query.

    Returns:
        Formatted analytics string.
    """
    sql = (
        "SELECT memory_type, COUNT(*) AS count, "
        "ROUND(AVG(importance), 3) AS avg_importance "
        "FROM agent_memories "
        "GROUP BY memory_type "
        "ORDER BY count DESC"
    )
    resp = httpx.post(
        f"{PONDDB_URL}/pondapi/execute",
        json={"session_id": session_id, "sql": sql},
        headers=_headers(),
        timeout=30,
    )
    if resp.status_code != 200:
        return f"Analytics query failed ({resp.status_code}): {resp.text}"

    data = resp.json()
    rows = data.get("rows", [])
    if not rows:
        return "No memory analytics data available yet."

    lines = [f"Memory analytics ({data.get('rows_returned', len(rows))} rows):"]
    for row in rows:
        lines.append(f"  {row}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent assembly
# ---------------------------------------------------------------------------

memory_agent = Agent(
    name="ponddb-memory-agent",
    model="gemini-2.0-flash",
    description=(
        "An AI agent that uses PondDB as its persistent memory store. "
        "It can remember facts across sessions, search past memories, "
        "rate memories by usefulness, and run SQL analytics."
    ),
    instruction=(
        "You are a helpful assistant with persistent memory powered by PondDB.\n"
        "- Use `remember` to store important facts, decisions, or observations.\n"
        "- Use `recall` to search your stored memories before answering questions.\n"
        "- Use `rate_memory` after using a memory to signal whether it was helpful.\n"
        "- Use `memory_analytics` to understand what types of memories you have.\n"
        "Always check your memories before claiming you don't know something."
    ),
    tools=[
        FunctionTool(remember),
        FunctionTool(recall),
        FunctionTool(rate_memory),
        FunctionTool(memory_analytics),
    ],
)

if __name__ == "__main__":
    print("PondDB memory agent ready.")
    print(f"  PONDDB_URL     = {PONDDB_URL}")
    print(f"  PONDDB_API_KEY = {'set' if PONDDB_API_KEY else 'NOT SET'}")
    print("\nRun with `adk run demo.py` or import `memory_agent` in your own script.")
