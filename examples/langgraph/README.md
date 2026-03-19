# LangGraph + PondDB Memory API: Research Workflow

Two AI agents collaborate through PondDB's memory API to research a topic.
No findings pass through the LLM context between agents — all data flows
through PondDB episodic memory.

## Why PondDB memory for agents?

Multi-agent systems need shared state that persists across turns and agents.
PondDB's memory API gives agent teams:

- **Typed memory** (episodic, semantic, procedural, working, shared)
- **Importance scoring** — surface the most critical findings first
- **Access scopes** — private / workgroup / namespace isolation
- **SQL analytics** — query the memory store directly via `pondapi/execute`

## The Agents

| Agent      | Role                                      | PondDB Tools Used    |
|------------|-------------------------------------------|----------------------|
| Researcher | Researches topic, stores key findings     | `store_finding`      |
| Analyst    | Reads all findings, writes synthesis      | `search_findings`    |

## Architecture

```
Researcher                     Analyst
    |                              |
    |-- store_finding("finding1") -->|
    |         PondDB Memory         |
    |-- store_finding("finding2") -->|
    |         PondDB Memory         |
    |-- store_finding("finding3") -->|
    |         PondDB Memory         |
    |                              |
    |                    search_findings("*")
    |                              |<-- PondDB Memory
    |                              |
    |                    writes synthesis grounded
    |                    in retrieved findings
    |                              |
    +------------------------------+
              PondDB Memory
         (shared episodic store)

Post-run: run_analytics() queries agent_memories via pondapi/execute
```

Findings travel through PondDB memory, not through the LLM context window.

## Setup

### 1. Install dependencies

```bash
cd examples/langgraph
pip install -r requirements.txt
```

### 2. Set environment variables

```bash
export PONDDB_URL=http://localhost:8432
export PONDDB_API_KEY=your-ponddb-api-key
export ANTHROPIC_API_KEY=your-anthropic-api-key
```

Or create a `.env` file in this directory:

```
PONDDB_URL=http://localhost:8432
PONDDB_API_KEY=your-ponddb-api-key
ANTHROPIC_API_KEY=your-anthropic-api-key
```

### 3. Start PondDB

```bash
# From the pond-db repo root
docker compose up -d
```

### 4. Run the demo

```bash
python demo.py
```

## PondDB Memory API Reference

All endpoints require the `X-API-Key` header.

| Method | Endpoint                    | Description                        |
|--------|-----------------------------|------------------------------------|
| POST   | `/memories`                 | Store a new memory                 |
| GET    | `/memories/search`          | Search memories by content/type    |
| GET    | `/memories/{id}`            | Fetch a single memory by ID        |
| POST   | `/memories/{id}/feedback`   | Submit reward signal (-1.0 to 1.0) |
| POST   | `/pondapi/execute`          | Run SQL against the memory store   |

### Store a memory

```python
import httpx

resp = httpx.post(
    "http://localhost:8432/memories",
    json={
        "agent_id": "researcher-1",
        "memory_type": "episodic",       # working | episodic | semantic | procedural | shared
        "content": {"text": "Deep-sea nodules contain critical rare-earth metals."},
        "importance": 0.85,
        "access_scope": "workgroup",     # private | workgroup | namespace
    },
    headers={"X-API-Key": "your-key", "Content-Type": "application/json"},
)
memory_id = resp.json()["id"]
```

### Search memories

```python
resp = httpx.get(
    "http://localhost:8432/memories/search",
    params={
        "content_contains": "rare-earth",
        "memory_type": "episodic",
        "min_importance": 0.5,
        "limit": 10,
    },
    headers={"X-API-Key": "your-key"},
)
memories = resp.json()   # list of memory objects
```

### Analytics via SQL

```python
resp = httpx.post(
    "http://localhost:8432/pondapi/execute",
    json={
        "session_id": "my-session",
        "sql": "SELECT memory_type, COUNT(*) FROM agent_memories GROUP BY memory_type",
    },
    headers={"X-API-Key": "your-key", "Content-Type": "application/json"},
)
rows = resp.json()["rows"]
```

## Running the tests

The test suite mocks all httpx calls and requires no API keys or running PondDB.

```bash
cd examples/langgraph
pytest test_integration.py -v
```

## Full code example

```python
import httpx
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

PONDDB_URL = "http://localhost:8432"
PONDDB_API_KEY = "your-key"

def _headers():
    return {"X-API-Key": PONDDB_API_KEY, "Content-Type": "application/json"}

@tool
def store_finding(agent_id: str, content: str, importance: float) -> str:
    """Store a research finding in PondDB episodic memory."""
    resp = httpx.post(
        f"{PONDDB_URL}/memories",
        json={
            "agent_id": agent_id,
            "memory_type": "episodic",
            "content": {"text": content},
            "importance": importance,
            "access_scope": "workgroup",
        },
        headers=_headers(),
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return f"Stored as memory {resp.json()['id']}."
    return f"Error ({resp.status_code}): {resp.text}"

@tool
def search_findings(query: str, min_importance: float, limit: int) -> str:
    """Search stored findings in PondDB episodic memory."""
    resp = httpx.get(
        f"{PONDDB_URL}/memories/search",
        params={"content_contains": query, "memory_type": "episodic",
                "min_importance": min_importance, "limit": limit},
        headers=_headers(),
        timeout=15,
    )
    memories = resp.json()
    if not memories:
        return "No prior findings found."
    return "\n".join(
        f"[{m['id']}] (importance={m['importance']:.2f}) {m['content']['text']}"
        for m in memories
    )

llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)

researcher = create_react_agent(llm, tools=[store_finding],
    state_modifier="You are a Researcher. Store 3+ findings via store_finding.")

analyst = create_react_agent(llm, tools=[search_findings],
    state_modifier="You are an Analyst. Search findings and write a synthesis.")

workflow = StateGraph(MessagesState)
workflow.add_node("researcher", researcher)
workflow.add_node("analyst", analyst)
workflow.add_edge(START, "researcher")
workflow.add_edge("researcher", "analyst")
workflow.add_edge("analyst", END)
graph = workflow.compile()

from langchain_core.messages import HumanMessage
result = graph.invoke({"messages": [HumanMessage(content="Research deep-sea mining impacts.")]})
print(result["messages"][-1].content)
```

## Requirements

- PondDB running (Docker or local)
- Python 3.10+
- Anthropic API key
- PondDB API key
