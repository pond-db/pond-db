# PondDB + OpenAI Agents SDK

The OpenAI Agents SDK (10M+ monthly downloads) is the simplest way to build agents. PondDB plugs in as function tools the agent can call.

## Setup

```bash
pip install openai-agents httpx
# PondDB running at localhost:8432
export OPENAI_API_KEY=your-key
```

## Define Tools

```python
from agents import Agent, Runner, function_tool
import httpx

PONDDB = "http://localhost:8432"
headers = {"X-API-Key": "your-ponddb-api-key", "Content-Type": "application/json"}

@function_tool
def remember(content: str, memory_type: str = "semantic", importance: float = 0.7):
    """Store a fact or finding in persistent memory."""
    resp = httpx.post(f"{PONDDB}/memories", json={
        "agent_id": "research-agent",
        "memory_type": memory_type,
        "content": {"text": content},
        "importance": importance,
        "access_scope": "workgroup"
    }, headers=headers)
    return f"Stored memory: {resp.json()['id'][:8]}..."

@function_tool
def recall(query: str, limit: int = 5):
    """Search persistent memory for relevant past findings."""
    resp = httpx.get(f"{PONDDB}/memories/search", params={
        "content_contains": query, "limit": limit
    }, headers=headers)
    memories = resp.json()
    if not memories:
        return "No memories found."
    return "\n".join(f"[{m['utility']:.2f}] {m['content']['text']}" for m in memories)
```

## Create Agent

```python
agent = Agent(
    name="Research Agent",
    instructions="""You are a research agent with persistent memory.
    ALWAYS use recall() before answering questions.
    ALWAYS use remember() to store important findings.""",
    tools=[remember, recall]
)
```

## Run

```python
result = Runner.run_sync(agent, "What is DuckDB? Remember the key facts.")
print(result.final_output)

# New session — agent recalls what it learned:
result2 = Runner.run_sync(agent, "What do you remember about DuckDB?")
print(result2.final_output)
```

## Key Features

- **Persistent memory**: Memories survive across `Runner.run_sync` calls
- **Utility scoring**: Call `/memories/{id}/feedback` to rank memories by usefulness
- **SQL analytics**: Query agent behavior with SQL after the run

## Post-Run Analytics

```python
resp = httpx.post(f"{PONDDB}/pondapi/execute", json={
    "sql": """SELECT memory_type, COUNT(*) as count,
              ROUND(AVG(utility), 2) as avg_utility
              FROM agent_memories WHERE deleted_at IS NULL
              GROUP BY memory_type ORDER BY count DESC"""
}, headers=headers)
```

## Full Demo

See [`examples/openai-agents-sdk/demo.py`](../../examples/openai-agents-sdk/demo.py)
