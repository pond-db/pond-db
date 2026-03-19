# PondDB + LangGraph

LangGraph (34M+ monthly downloads) is the enterprise standard for multi-step agent workflows. PondDB plugs in as tools that graph nodes call to share findings across steps.

## Setup

```bash
pip install langgraph langchain-anthropic httpx
# PondDB running at localhost:8432
export ANTHROPIC_API_KEY=your-key
```

## Define Tools

```python
from langchain_core.tools import tool
import httpx

PONDDB = "http://localhost:8432"
headers = {"X-API-Key": "your-ponddb-api-key", "Content-Type": "application/json"}

@tool
def store_finding(content: str, importance: float = 0.7):
    """Store a research finding in shared team memory."""
    resp = httpx.post(f"{PONDDB}/memories", json={
        "agent_id": "langgraph-researcher",
        "memory_type": "shared",
        "content": {"text": content, "source": "langgraph"},
        "importance": importance,
        "access_scope": "workgroup"
    }, headers=headers)
    return f"Stored: {resp.json()['id'][:8]}..."

@tool
def search_findings(query: str):
    """Search team memory for past findings."""
    resp = httpx.get(f"{PONDDB}/memories/search", params={
        "content_contains": query, "memory_type": "shared", "limit": 10
    }, headers=headers)
    memories = resp.json()
    if not memories:
        return "No prior findings found."
    return "\n".join(f"- {m['content']['text']}" for m in memories)
```

## Build the Graph

```python
from langgraph.graph import StateGraph, MessagesState, START, END
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(model="claude-sonnet-4-20250514")

def researcher(state: MessagesState):
    model = llm.bind_tools([store_finding, search_findings])
    return {"messages": [model.invoke(state["messages"])]}

def analyst(state: MessagesState):
    model = llm.bind_tools([search_findings])
    return {"messages": [model.invoke(state["messages"])]}

workflow = StateGraph(MessagesState)
workflow.add_node("researcher", researcher)
workflow.add_node("analyst", analyst)
workflow.add_edge(START, "researcher")
workflow.add_edge("researcher", "analyst")
workflow.add_edge("analyst", END)

graph = workflow.compile()
```

## Run

```python
result = graph.invoke({
    "messages": [("user", "Research PondDB and analyze its competitive position")]
})
print(result["messages"][-1].content)
```

## Key Features

- **Cross-node memory**: Researcher stores → analyst reads within the same graph run
- **Persistent across runs**: Memories accumulate across multiple `graph.invoke()` calls
- **SQL analytics**: Query agent activity after the workflow completes

## Post-Run Analytics

```python
analytics = httpx.post(f"{PONDDB}/pondapi/execute", json={
    "sql": """SELECT agent_id, action, COUNT(*) as ops
              FROM memory_access_log
              WHERE created_at > datetime('now', '-1 hour')
              GROUP BY agent_id, action ORDER BY ops DESC"""
}, headers=headers)
```

## Full Demo

See [`examples/langgraph/demo.py`](../../examples/langgraph/demo.py)
