"""CrewAI + PondDB Memory Demo

Three-agent content crew (Researcher, Writer, Editor) that share findings
through PondDB's memory API.  Agents call RememberTool to persist memories
and RecallTool to search what teammates have stored.

After the crew finishes, run_analytics() shows a per-agent memory summary
via PondDB SQL.

Usage::

    export PONDDB_URL=http://localhost:8432
    export PONDDB_API_KEY=your-key
    export OPENAI_API_KEY=your-key   # or ANTHROPIC_API_KEY
    python demo.py
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Type

import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PONDDB_URL: str = os.getenv("PONDDB_URL", "http://localhost:8432")
PONDDB_API_KEY: str = os.getenv("PONDDB_API_KEY", "")


def _headers() -> dict[str, str]:
    """Return auth headers for every PondDB request."""
    return {
        "X-API-Key": PONDDB_API_KEY,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Pydantic input schemas
# ---------------------------------------------------------------------------


class RememberInput(BaseModel):
    agent_id: str = Field(..., description="Unique identifier for the calling agent.")
    memory_type: str = Field(
        ...,
        description=(
            "One of: working, episodic, semantic, procedural, shared."
        ),
    )
    content: dict[str, Any] = Field(
        ..., description="JSONB payload — arbitrary key/value data to store."
    )
    importance: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Importance score between 0.0 and 1.0.",
    )
    access_scope: str = Field(
        default="workgroup",
        description="One of: private, workgroup, namespace.",
    )


class RecallInput(BaseModel):
    content_contains: Optional[str] = Field(
        default=None, description="Substring to search inside memory content."
    )
    memory_type: Optional[str] = Field(
        default=None, description="Filter by memory type (e.g. 'semantic')."
    )
    min_importance: float = Field(
        default=0.0, description="Only return memories at or above this importance."
    )
    limit: int = Field(default=10, description="Maximum number of results to return.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class RememberTool(BaseTool):
    """Store a memory in PondDB so other crew members can recall it later."""

    name: str = "remember"
    description: str = (
        "Persist a piece of information to PondDB memory. "
        "Use this whenever you want teammates to be able to recall your findings."
    )
    args_schema: Type[BaseModel] = RememberInput

    def _run(
        self,
        agent_id: str,
        memory_type: str,
        content: dict[str, Any],
        importance: float = 0.5,
        access_scope: str = "workgroup",
    ) -> str:
        """POST to /memories and return a confirmation string."""
        payload = {
            "agent_id": agent_id,
            "memory_type": memory_type,
            "content": content,
            "importance": importance,
            "access_scope": access_scope,
        }
        try:
            resp = httpx.post(
                f"{PONDDB_URL}/memories",
                json=payload,
                headers=_headers(),
                timeout=15,
            )
        except httpx.RequestError as exc:
            return f"Memory store failed (network error): {exc}"

        if resp.status_code in (200, 201):
            memory_id = resp.json().get("id", "unknown")
            return f"Memory stored successfully (id={memory_id})."

        return (
            f"Memory store failed ({resp.status_code}): {resp.text[:200]}"
        )


class RecallTool(BaseTool):
    """Search PondDB for memories matching the given criteria."""

    name: str = "recall"
    description: str = (
        "Search PondDB memory for stored findings. "
        "Returns matching memories as formatted text."
    )
    args_schema: Type[BaseModel] = RecallInput

    def _run(
        self,
        content_contains: Optional[str] = None,
        memory_type: Optional[str] = None,
        min_importance: float = 0.0,
        limit: int = 10,
    ) -> str:
        """GET /memories/search and return formatted results."""
        params: dict[str, Any] = {"min_importance": min_importance, "limit": limit}
        if content_contains:
            params["content_contains"] = content_contains
        if memory_type:
            params["memory_type"] = memory_type

        try:
            resp = httpx.get(
                f"{PONDDB_URL}/memories/search",
                params=params,
                headers=_headers(),
                timeout=15,
            )
        except httpx.RequestError as exc:
            return f"Memory recall failed (network error): {exc}"

        if resp.status_code != 200:
            return f"Memory recall failed ({resp.status_code}): {resp.text[:200]}"

        memories: list[dict[str, Any]] = resp.json()
        if not memories:
            return "No relevant memories found."

        lines: list[str] = []
        for mem in memories:
            agent = mem.get("agent_id", "unknown")
            mtype = mem.get("memory_type", "?")
            importance = mem.get("importance", 0.0)
            content = mem.get("content", {})
            lines.append(
                f"[{agent}/{mtype} importance={importance:.2f}] "
                f"{json.dumps(content)}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Analytics helper
# ---------------------------------------------------------------------------


def run_analytics(session_id: str = "demo-session") -> str:
    """Run a SQL query against PondDB and return a formatted summary string.

    Shows per-agent memory counts and average importance for the session.
    """
    sql = (
        "SELECT agent_id, memory_type, COUNT(*) AS memories, "
        "ROUND(AVG(importance), 2) AS avg_importance "
        "FROM agent_memories "
        "GROUP BY agent_id, memory_type "
        "ORDER BY avg_importance DESC"
    )
    try:
        resp = httpx.post(
            f"{PONDDB_URL}/pondapi/execute",
            json={"session_id": session_id, "sql": sql},
            headers=_headers(),
            timeout=30,
        )
    except httpx.RequestError as exc:
        return f"Analytics query failed (network error): {exc}"

    if resp.status_code not in (200, 201):
        return f"Analytics query failed ({resp.status_code}): {resp.text[:200]}"

    data = resp.json()
    rows: list[dict[str, Any]] = data.get("rows", [])
    if not rows:
        return "No analytics data found."

    lines = ["--- PondDB Memory Analytics ---"]
    for row in rows:
        lines.append(
            f"  {row.get('agent_id','?'):20s} "
            f"type={row.get('memory_type','?'):12s} "
            f"count={row.get('memories','?'):4}  "
            f"avg_importance={row.get('avg_importance','?')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Crew definition
# ---------------------------------------------------------------------------


def build_crew():  # type: ignore[return]
    """Build and return a 3-agent CrewAI crew.

    Import is deferred so the module can be imported in tests without crewai.
    """
    from crewai import Agent, Crew, Task  # noqa: PLC0415

    remember = RememberTool()
    recall = RecallTool()

    researcher = Agent(
        role="Researcher",
        goal=(
            "Find compelling facts about AI agent memory systems and store them "
            "in PondDB so the Writer can build on them."
        ),
        backstory=(
            "You are a meticulous researcher who always backs claims with sources "
            "and stores findings for your teammates."
        ),
        tools=[remember, recall],
        verbose=True,
    )

    writer = Agent(
        role="Writer",
        goal=(
            "Retrieve the Researcher's findings from PondDB and draft a concise "
            "blog post section."
        ),
        backstory=(
            "You are a clear technical writer who pulls facts from shared memory "
            "rather than guessing."
        ),
        tools=[remember, recall],
        verbose=True,
    )

    editor = Agent(
        role="Editor",
        goal=(
            "Recall both the Researcher's facts and the Writer's draft from PondDB, "
            "then produce a polished final version."
        ),
        backstory=(
            "You are a senior editor who checks every claim against stored facts "
            "before approving a piece."
        ),
        tools=[remember, recall],
        verbose=True,
    )

    research_task = Task(
        description=(
            "Research three key advantages of using persistent shared memory "
            "(like PondDB) in multi-agent AI systems.  Store each finding as a "
            "separate 'semantic' memory with access_scope='workgroup' and "
            "importance >= 0.8."
        ),
        expected_output="Three memories stored in PondDB, confirmed by memory IDs.",
        agent=researcher,
    )

    write_task = Task(
        description=(
            "Recall all semantic memories stored by the Researcher (min_importance=0.7). "
            "Draft a 150-word blog post paragraph that cites each finding. "
            "Store the draft as an 'episodic' memory with access_scope='workgroup'."
        ),
        expected_output="Draft paragraph stored in PondDB as an episodic memory.",
        agent=writer,
    )

    edit_task = Task(
        description=(
            "Recall the Writer's episodic draft and the Researcher's semantic facts. "
            "Polish the paragraph for clarity and accuracy.  Store the final version "
            "as a 'procedural' memory (a reusable content template) with "
            "importance=0.9 and access_scope='namespace'."
        ),
        expected_output="Final polished paragraph stored in PondDB.",
        agent=editor,
    )

    return Crew(
        agents=[researcher, writer, editor],
        tasks=[research_task, write_task, edit_task],
        verbose=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    print("Building crew...")
    crew = build_crew()

    print("\nRunning crew...\n")
    result = crew.kickoff()

    print("\n" + "=" * 60)
    print("Crew result:")
    print(result)

    print("\n" + "=" * 60)
    print("Post-crew analytics:")
    print(run_analytics())
