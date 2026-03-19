"""LangGraph + PondDB Memory API: Research Workflow Demo.

Two AI agents collaborate through PondDB's memory API:
  1. Researcher  — stores findings via store_finding tool
  2. Analyst     — reads stored findings via search_findings tool,
                   synthesizes a summary

After the workflow completes, post-run analytics are fetched from PondDB
using a raw SQL query against the agent_memories table.

All PondDB calls are made via httpx. No data passes through the LLM context
between agents — findings travel through PondDB memory.

Prerequisites:
  1. PondDB running: docker compose up -d
  2. pip install -r requirements.txt
  3. Set env vars: PONDDB_URL, PONDDB_API_KEY, ANTHROPIC_API_KEY

Usage:
  python demo.py
"""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx

# LangGraph imports are deferred so tests can import tools without LangGraph
# installed in every environment. The main() function triggers the real imports.
if TYPE_CHECKING:
    pass

PONDDB_URL: str = os.getenv("PONDDB_URL", "http://localhost:8432")
PONDDB_API_KEY: str | None = os.getenv("PONDDB_API_KEY")


def _headers() -> dict[str, str]:
    """Return PondDB auth headers."""
    return {
        "X-API-Key": PONDDB_API_KEY or "",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# PondDB Memory Tools  (no LangGraph imports here — importable standalone)
# ---------------------------------------------------------------------------

# We import @tool lazily so that the tools can also be wrapped in tests without
# requiring the full langchain stack.  We call _make_tool() at the bottom of
# this file so that `from demo import store_finding` works normally at runtime.


def _store_finding_impl(agent_id: str, content: str, importance: float) -> str:
    """Implementation of store_finding — httpx only, no framework deps."""
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "memory_type": "episodic",
        "content": {"text": content},
        "importance": importance,
        "access_scope": "workgroup",
    }

    resp = httpx.post(
        f"{PONDDB_URL}/memories",
        json=body,
        headers=_headers(),
        timeout=15,
    )

    if resp.status_code in (200, 201):
        memory_id: str = resp.json().get("id", "unknown")
        return f"Stored finding as memory {memory_id}."

    return f"Failed to store finding ({resp.status_code}): {resp.text}"


def _search_findings_impl(query: str, min_importance: float, limit: int) -> str:
    """Implementation of search_findings — httpx only, no framework deps."""
    params: dict[str, Any] = {
        "content_contains": query,
        "memory_type": "episodic",
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

    memories: list[dict[str, Any]] = resp.json()

    if not memories:
        return "No prior findings found."

    lines: list[str] = []
    for mem in memories:
        mem_id = mem.get("id", "?")
        importance = mem.get("importance", 0.0)
        text = mem.get("content", {}).get("text", "")
        lines.append(f"[{mem_id}] (importance={importance:.2f}) {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-run analytics
# ---------------------------------------------------------------------------


def run_analytics(session_id: str) -> str:
    """Query PondDB for a memory usage summary via pondapi/execute.

    Args:
        session_id: PondDB session ID to use for the SQL query.

    Returns:
        Formatted analytics summary string.
    """
    sql = (
        "SELECT memory_type, COUNT(*) AS count, "
        "AVG(importance) AS avg_importance "
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

    if resp.status_code not in (200, 201):
        return f"Analytics query failed ({resp.status_code}): {resp.text}"

    data = resp.json()
    rows: list[dict[str, Any]] = data.get("rows", [])

    if not rows:
        return "No analytics data available."

    lines = ["Memory usage by type:"]
    for row in rows:
        lines.append(
            f"  {row['memory_type']}: {row['count']} memories "
            f"(avg importance: {float(row.get('avg_importance', 0)):.2f})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wrap implementations as LangChain tools.
# This is the only place langchain_core is imported, keeping it isolated.
# ---------------------------------------------------------------------------

try:
    from langchain_core.tools import tool as _lc_tool

    @_lc_tool
    def store_finding(agent_id: str, content: str, importance: float) -> str:
        """Store a research finding in PondDB episodic memory.

        Args:
            agent_id:   Identifier for the agent storing the memory.
            content:    The finding text to store.
            importance: Importance score from 0.0 to 1.0.
        """
        return _store_finding_impl(agent_id, content, importance)

    @_lc_tool
    def search_findings(query: str, min_importance: float, limit: int) -> str:
        """Search stored research findings in PondDB episodic memory.

        Args:
            query:          Text to search for in memory content.
            min_importance: Minimum importance threshold (0.0 to 1.0).
            limit:          Maximum number of results to return.
        """
        return _search_findings_impl(query, min_importance, limit)

except ImportError:
    # Fallback: expose plain callables so tests can still patch and invoke them.
    # Tests use .invoke() which is duck-typed via the _FallbackTool wrapper.

    class _FallbackTool:  # type: ignore[no-redef]
        """Minimal tool shim for environments without langchain installed."""

        def __init__(self, fn):  # type: ignore[no-untyped-def]
            self._fn = fn
            self.__name__ = fn.__name__
            self.__doc__ = fn.__doc__

        def invoke(self, input_dict: dict[str, Any]) -> str:  # type: ignore[override]
            return self._fn(**input_dict)

        def __call__(self, *args: Any, **kwargs: Any) -> str:
            return self._fn(*args, **kwargs)

    store_finding = _FallbackTool(_store_finding_impl)  # type: ignore[assignment]
    search_findings = _FallbackTool(_search_findings_impl)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Graph — only constructed when running as main script
# ---------------------------------------------------------------------------


def _build_graph():  # type: ignore[return]
    """Build and return the compiled LangGraph StateGraph."""
    from langchain_anthropic import ChatAnthropic
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import create_react_agent

    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)

    researcher = create_react_agent(
        llm,
        tools=[store_finding],
        state_modifier=(
            "You are a Research Agent. Your job:\n"
            "1. Research the topic given to you.\n"
            "2. For each key finding, call store_finding with:\n"
            "   - agent_id: 'researcher-1'\n"
            "   - content: the finding text\n"
            "   - importance: a score 0.0-1.0 reflecting significance\n"
            "3. Store at least 3 distinct findings.\n"
            "4. Report which findings you stored and their memory IDs.\n\n"
            "Do NOT write a final analysis — the Analyst agent does that."
        ),
    )

    analyst = create_react_agent(
        llm,
        tools=[search_findings],
        state_modifier=(
            "You are an Analyst Agent. Your job:\n"
            "1. Search PondDB for research findings using search_findings.\n"
            "   Use min_importance=0.0 and limit=20 to get all findings.\n"
            "2. Read the findings returned.\n"
            "3. Write a concise synthesis that:\n"
            "   - Summarizes the key themes\n"
            "   - Identifies the most important findings (highest importance)\n"
            "   - Notes any gaps or areas needing further research\n\n"
            "CRITICAL: Your synthesis must be grounded in the findings returned\n"
            "by search_findings. Do NOT fabricate data."
        ),
    )

    workflow = StateGraph(MessagesState)
    workflow.add_node("researcher", researcher)
    workflow.add_node("analyst", analyst)
    workflow.add_edge(START, "researcher")
    workflow.add_edge("researcher", "analyst")
    workflow.add_edge("analyst", END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    from dotenv import load_dotenv
    from langchain_core.messages import HumanMessage

    load_dotenv()

    topic = (
        "the impact of deep-sea mining on marine biodiversity, "
        "including key species affected, regulatory frameworks, and "
        "recent scientific findings"
    )

    print()
    print("  =============================================")
    print("  LangGraph + PondDB Memory: Research Workflow")
    print("  =============================================")
    print()
    print("  Two agents collaborating through PondDB memory:")
    print("    1. Researcher  — stores findings via PondDB memory API")
    print("    2. Analyst     — reads findings, writes synthesis")
    print()
    print(f"  Topic: {topic}")
    print()

    graph = _build_graph()
    start = time.time()

    result = graph.invoke(
        {"messages": [HumanMessage(content=f"Research this topic: {topic}")]}
    )

    elapsed = time.time() - start

    print()
    print("  =============================================")
    print("  RESEARCH SYNTHESIS")
    print("  =============================================")
    print()
    print(result["messages"][-1].content)
    print()

    print("  =============================================")
    print("  MEMORY ANALYTICS")
    print("  =============================================")
    print()
    analytics = run_analytics(session_id="demo-session-001")
    print(f"  {analytics}")
    print()
    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Total messages: {len(result['messages'])}")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
