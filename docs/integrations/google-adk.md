# PondDB + Google ADK

Google ADK (Agent Development Kit) is Google's official agent framework. PondDB plugs in as FunctionTool wrappers the agent calls for persistent memory.

## Setup

```bash
pip install google-adk httpx
# PondDB running at localhost:8432
export GOOGLE_API_KEY=your-key
```

## Define Tools

```python
from google.adk.tools import FunctionTool
import httpx, json

PONDDB = "http://localhost:8432"
headers = {"X-API-Key": "your-ponddb-api-key", "Content-Type": "application/json"}

def remember(content: str, memory_type: str = "semantic", importance: float = 0.7) -> str:
    """Store information in persistent memory for future sessions."""
    resp = httpx.post(f"{PONDDB}/memories", json={
        "agent_id": "adk-agent",
        "memory_type": memory_type,
        "content": {"text": content},
        "importance": importance,
        "access_scope": "workgroup"
    }, headers=headers)
    return f"Remembered (id: {resp.json()['id'][:8]}...)"

def recall(query: str, limit: int = 5) -> str:
    """Search persistent memory for relevant information."""
    resp = httpx.get(f"{PONDDB}/memories/search", params={
        "content_contains": query, "limit": limit
    }, headers=headers)
    memories = resp.json()
    if not memories:
        return "No memories found matching your query."
    return "\n".join(
        f"[utility: {m['utility']:.2f}] {m['content']['text']}"
        for m in memories
    )

def rate_memory(memory_id: str, usefulness: float) -> str:
    """Rate how useful a memory was (helps rank future searches)."""
    resp = httpx.post(f"{PONDDB}/memories/{memory_id}/feedback",
                       json={"reward": usefulness}, headers=headers)
    return f"Rated memory {memory_id[:8]}... with {usefulness}"

def memory_analytics() -> str:
    """Show what's stored in memory and usage patterns."""
    resp = httpx.post(f"{PONDDB}/pondapi/execute", json={
        "sql": """SELECT memory_type, COUNT(*) as count,
                  ROUND(AVG(utility), 2) as avg_utility
                  FROM agent_memories WHERE deleted_at IS NULL
                  GROUP BY memory_type ORDER BY count DESC"""
    }, headers=headers)
    return json.dumps(resp.json(), indent=2)
```

## Create Agent

```python
from google.adk.agents import Agent

agent = Agent(
    name="adk_memory_agent",
    model="gemini-2.0-flash",
    description="An agent with persistent, queryable memory powered by PondDB.",
    instruction="""You have access to persistent memory via PondDB.
    ALWAYS check memory with recall() before answering questions.
    Store important facts with remember().
    After using a memory, rate it with rate_memory().""",
    tools=[
        FunctionTool(remember),
        FunctionTool(recall),
        FunctionTool(rate_memory),
        FunctionTool(memory_analytics)
    ]
)
```

## Key Features

- **Persistent cross-session memory**: Memories survive agent restarts
- **Utility scoring**: `rate_memory()` updates rankings — useful memories surface first
- **SQL analytics**: Query memory patterns after agent runs

## Full Demo

See [`examples/google-adk/demo.py`](../../examples/google-adk/demo.py)
