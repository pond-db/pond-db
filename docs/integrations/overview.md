# PondDB Integrations

PondDB works with every major agent framework. Each integration follows the same pattern: define tools that call PondDB's HTTP API, give them to your agent, and let the agent decide when to store and recall memories.

## Supported Frameworks

| Framework | Integration Type | Demo |
|-----------|-----------------|------|
| [OpenAI Agents SDK](openai-agents-sdk.md) | Function tools | `examples/openai-agents-sdk/` |
| [LangGraph](langgraph.md) | Tool nodes in graph | `examples/langgraph/` |
| [CrewAI](crewai.md) | BaseTool subclasses | `examples/crewai/` |
| [Google ADK](google-adk.md) | FunctionTool wrappers | `examples/google-adk/` |
| [Claude Code / Agent SDK](claude-agent-sdk.md) | MCP server (native) | `examples/claude-agent-sdk/` |

## How It Works

Every integration uses the same 3 PondDB endpoints:

```
POST /memories          — store a memory
GET  /memories/search   — recall memories
POST /memories/{id}/feedback — rate a memory
```

Your agent gets tools that wrap these endpoints. The agent decides when to call them based on its instructions.

## The Pattern

```python
# 1. Define tools that call PondDB
@tool
def remember(content: str, importance: float = 0.7):
    httpx.post(f"{PONDDB}/memories", json={...})

@tool
def recall(query: str):
    httpx.get(f"{PONDDB}/memories/search", params={...})

# 2. Give tools to your agent
agent = YourFrameworkAgent(tools=[remember, recall])

# 3. The agent uses them automatically
agent.run("Research DuckDB and remember the key facts")
```

## What Makes PondDB Different

Unlike Mem0 or Zep, PondDB stores everything in one database. After your agents run, you can query their behavior with SQL:

```sql
-- What did the agent know when it failed?
SELECT am.content, am.utility, mal.status
FROM memory_access_log mal
JOIN agent_memories am ON am.id IN (SELECT unnest(mal.memory_ids))
WHERE mal.status = 'error'
ORDER BY mal.created_at DESC;
```

No other memory system can answer this question.

## Quick Start

```bash
# Start PondDB
git clone https://github.com/pond-db/pond-db && cd pond-db
docker compose up -d

# Pick your framework
cd examples/openai-agents-sdk  # or langgraph, crewai, google-adk, claude-agent-sdk
pip install -r requirements.txt
python demo.py
```
