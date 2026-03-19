# PondDB + Claude Code / Agent SDK

PondDB has a native MCP server. Claude Code and the Claude Agent SDK connect to it directly — no HTTP client code needed.

## Setup

```bash
pip install mcp-server-ponddb
# PondDB running at localhost:8432
```

## Configure MCP

Add to your Claude Code or Agent SDK config:

```json
{
  "mcpServers": {
    "ponddb": {
      "command": "python",
      "args": ["-m", "mcp_server_ponddb"],
      "env": {
        "PONDDB_URL": "http://localhost:8432",
        "PONDDB_API_KEY": "your-api-key"
      }
    }
  }
}
```

This gives your agent 5 tools automatically:

| Tool | What it does |
|------|-------------|
| `ponddb_remember` | Store a memory |
| `ponddb_recall` | Search memories |
| `ponddb_query` | Run SQL analytics |
| `ponddb_forget` | Delete a memory |
| `ponddb_feedback` | Rate a memory's usefulness |

## Try It

```
You: "Remember that our deployment uses Kubernetes with 3 replicas"
Agent: → calls ponddb_remember → stored as semantic memory

You: "What do you remember about our deployment?"
Agent: → calls ponddb_recall → finds the Kubernetes memory

You: "How many memories do you have?"
Agent: → calls ponddb_query with SQL → returns count by type
```

## Multi-Agent with Agent SDK

```python
from claude_agent_sdk import Agent, Subagent

researcher = Subagent(
    name="researcher",
    model="claude-sonnet-4-20250514",
    mcp_servers=[{
        "command": "python",
        "args": ["-m", "mcp_server_ponddb"],
        "env": {
            "PONDDB_URL": "http://localhost:8432",
            "PONDDB_API_KEY": "pk_researcher"
        }
    }],
    instructions="Store all findings using ponddb_remember."
)

analyst = Subagent(
    name="analyst",
    model="claude-sonnet-4-20250514",
    mcp_servers=[{
        "command": "python",
        "args": ["-m", "mcp_server_ponddb"],
        "env": {
            "PONDDB_URL": "http://localhost:8432",
            "PONDDB_API_KEY": "pk_analyst"  # same workgroup = shared memory
        }
    }],
    instructions="Use ponddb_recall to read the researcher's findings."
)
```

The analyst can read the researcher's memories because they share a workgroup in PondDB. No extra config needed.

## What Happens Under the Hood

```
Claude Code sends "Remember that Q1 revenue was $2.1M"
  → LLM decides to call ponddb_remember
  → MCP server receives JSON-RPC call
  → MCP server calls POST /memories on PondDB
  → PondDB stores in SQLite with agent_id, memory_type, content
  → PondDB writes to memory_access_log (async)
  → MCP server returns success to Claude
  → Claude confirms to user
```

Every step is logged. Query it later:

```sql
SELECT agent_id, action, COUNT(*)
FROM memory_access_log
WHERE created_at > datetime('now', '-1 hour')
GROUP BY agent_id, action;
```

## Full Demo

See [`examples/claude-agent-sdk/demo.py`](../../examples/claude-agent-sdk/demo.py)
