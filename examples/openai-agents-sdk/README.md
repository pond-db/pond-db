# PondDB + OpenAI Agents SDK

Give any OpenAI agent persistent, queryable memory through PondDB. Three function tools (`remember`, `recall`, `memory_stats`) replace ephemeral context with a durable, SQL-accessible memory store.

## Why PondDB for agent memory?

| Problem | PondDB solution |
|---------|-----------------|
| Agent forgets between runs | `remember()` persists memories to Postgres |
| Agents can't share context | `access_scope: workgroup` makes memories visible across agents |
| No audit trail | Every read/write is logged in `memory_access_log` |
| Black-box retrieval | `memory_stats()` runs real SQL — inspect exactly what's stored |

## Setup

```bash
# 1. Install dependencies
pip install -r examples/openai-agents-sdk/requirements.txt

# 2. Set environment variables
export PONDDB_URL="http://localhost:8432"
export PONDDB_API_KEY="your-ponddb-api-key"
export OPENAI_API_KEY="your-openai-api-key"

# 3. Start PondDB (if running locally)
cd pond-db && docker compose up -d

# 4. Run the demo
python examples/openai-agents-sdk/demo.py
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PONDDB_URL` | No | `http://localhost:8432` | PondDB server URL |
| `PONDDB_API_KEY` | Yes | — | API key (set in PondDB admin) |
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for the agent |

## The three tools

### `remember(agent_id, memory_type, content, importance, access_scope)`

Stores a memory via `POST /memories`.

- `memory_type`: `working` | `episodic` | `semantic` | `procedural` | `shared`
- `access_scope`: `private` | `workgroup` | `namespace`
- `importance`: float 0–1 (controls retrieval priority and decay rate)

### `recall(content_contains, memory_type, min_importance, limit)`

Searches memories via `GET /memories/search`. Returns formatted results or `"No memories found."` when the store is empty.

### `memory_stats(session_id)`

Runs a `GROUP BY memory_type` SQL query via `POST /pondapi/execute`. Returns a formatted table of counts and average importance scores.

## Full example

```python
from agents import Agent, Runner, function_tool
import httpx, os, json

PONDDB_URL = os.getenv("PONDDB_URL", "http://localhost:8432")
PONDDB_API_KEY = os.getenv("PONDDB_API_KEY", "")

def _headers():
    return {"X-API-Key": PONDDB_API_KEY, "Content-Type": "application/json"}

@function_tool
def remember(agent_id: str, memory_type: str, content: dict,
             importance: float = 0.5, access_scope: str = "private") -> str:
    resp = httpx.post(
        f"{PONDDB_URL}/memories",
        json={"agent_id": agent_id, "memory_type": memory_type,
              "content": content, "importance": importance,
              "access_scope": access_scope},
        headers=_headers(), timeout=15,
    )
    if resp.status_code in (200, 201):
        return f"Memory stored. ID: {resp.json()['id']}"
    return f"Failed ({resp.status_code}): {resp.text}"

@function_tool
def recall(content_contains: str = "", memory_type: str = "",
           min_importance: float = 0.0, limit: int = 10) -> str:
    params = {"limit": limit}
    if content_contains: params["content_contains"] = content_contains
    if memory_type:       params["memory_type"] = memory_type
    if min_importance:    params["min_importance"] = min_importance
    resp = httpx.get(f"{PONDDB_URL}/memories/search",
                     params=params, headers=_headers(), timeout=15)
    memories = resp.json()
    if not memories:
        return "No memories found."
    return "\n".join(
        f"[{m['id']}] {m['memory_type']} — {json.dumps(m['content'])}"
        for m in memories
    )

@function_tool
def memory_stats(session_id: str = "demo") -> str:
    resp = httpx.post(
        f"{PONDDB_URL}/pondapi/execute",
        json={"session_id": session_id,
              "sql": "SELECT memory_type, COUNT(*) AS count FROM agent_memories GROUP BY 1"},
        headers=_headers(), timeout=30,
    )
    rows = resp.json().get("rows", [])
    return "\n".join(f"{r['memory_type']}: {r['count']}" for r in rows)

agent = Agent(
    name="PondDB Memory Agent",
    instructions=(
        "Always call recall() before answering. "
        "Always call remember() after finding something important."
    ),
    tools=[remember, recall, memory_stats],
)

result = Runner.run_sync(agent, "What do you know about our top customers?")
print(result.final_output)
```

## Memory types

| Type | Use case |
|------|----------|
| `working` | Temporary scratchpad — high decay, auto-pruned |
| `episodic` | Events and interactions ("User asked about X on Tuesday") |
| `semantic` | Facts and knowledge ("Enterprise customers prefer SQL") |
| `procedural` | How-to knowledge ("Always check renewal date before outreach") |
| `shared` | Cross-agent communication within a workgroup |

## Running the tests

Tests mock all HTTP calls — no PondDB server or OpenAI key required.

```bash
pytest examples/openai-agents-sdk/test_integration.py -v
```

## Memory API reference

All endpoints require `X-API-Key` header.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/memories` | Create a memory |
| `GET` | `/memories/search` | Search memories |
| `GET` | `/memories/{id}` | Get one memory |
| `PUT` | `/memories/{id}` | Update content or importance |
| `DELETE` | `/memories/{id}` | Soft-delete a memory |
| `POST` | `/memories/{id}/feedback` | Submit reward signal (–1 to 1) |
| `POST` | `/pondapi/execute` | Run analytics SQL |
