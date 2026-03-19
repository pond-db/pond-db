# Claude Agent SDK + PondDB MCP Demo

Two AI subagents — Researcher and Analyst — collaborate through PondDB's shared
memory using the Model Context Protocol (MCP). The researcher stores business
findings; the analyst reads them, runs SQL analytics, and rates memory quality.

## What this demonstrates

- **MCP tool calling**: The Claude Agent SDK spawns `mcp-server-ponddb` as a subprocess
  and communicates over stdio using JSON-RPC 2.0. No REST client code needed in your agent.
- **Shared workgroup memory**: Both subagents use the same `PONDDB_WORKGROUP`, so
  workgroup-scoped memories the researcher stores are immediately visible to the analyst.
- **Tool-level access control**: Researcher is restricted to `[ponddb_remember, ponddb_recall]`.
  Analyst is restricted to `[ponddb_recall, ponddb_query, ponddb_feedback]`. The SDK enforces
  this at dispatch time.
- **SQL analytics over memory**: `ponddb_query` lets the analyst run arbitrary SQL against
  PondDB's `agent_memories` and `memory_access_log` tables — impossible with Mem0.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start PondDB
docker compose up -d          # from the db-engine root

# 3. Set environment variables
export PONDDB_URL="http://localhost:8432"
export PONDDB_API_KEY="your-api-key"
export PONDDB_WORKGROUP="demo"
export ANTHROPIC_API_KEY="sk-ant-..."

# 4. Run the demo
python demo.py
```

## MCP config JSON

When the Claude Agent SDK initialises a subagent, it passes an MCP server config
that looks like this:

```json
{
  "command": "mcp-server-ponddb",
  "args": [],
  "env": {
    "PONDDB_URL": "http://localhost:8432",
    "PONDDB_API_KEY": "your-api-key",
    "PONDDB_WORKGROUP": "demo"
  }
}
```

The SDK spawns `mcp-server-ponddb` as a subprocess and pipes JSON-RPC 2.0 messages
to/from it over stdin/stdout. Each subagent gets its own subprocess instance.

## Example prompts

Try these prompts against the researcher subagent:

```
Remember that Acme Corp is our highest-revenue customer at $500K ARR
and is currently evaluating two competitors.
```

```
Remember that Beta Inc just renewed for two years — low churn risk.
Use memory_type='semantic', importance=0.9, access_scope='workgroup'.
```

Try these against the analyst subagent (after the researcher has stored findings):

```
What do you remember about our customers' churn risk?
```

```
Run a SQL query to show the average importance of all memories grouped by agent.
```

```
Rate the most recent memory you read as highly useful (reward=0.9).
```

## What happens under the hood

```
Prompt → Claude LLM → tool call decision
                           │
                           ▼
              Claude Agent SDK (JSON-RPC client)
                           │  stdin/stdout pipe
                           ▼
              mcp-server-ponddb (subprocess)
                           │  HTTP
                           ▼
              PondDB REST API (localhost:8432)
                           │
                           ▼
              SQLite database
              (agent_memories, memory_access_log tables)
                           │
                           ▼
              JSON result ← HTTP ← MCP server ← SDK ← LLM context
```

### Step-by-step for `ponddb_remember`

1. The researcher LLM decides to call `ponddb_remember` with arguments like
   `{"agent_id": "researcher", "memory_type": "semantic", "content": {...}, "importance": 0.9}`.
2. The SDK serialises this as a JSON-RPC 2.0 `tools/call` request and writes it to
   the MCP server's stdin.
3. `mcp-server-ponddb` receives the request, calls `PondDBClient.remember()`,
   which issues `POST /memories` to PondDB's HTTP API.
4. PondDB stores the memory in SQLite with workgroup scope.
5. The MCP server writes a JSON-RPC response to stdout.
6. The SDK forwards the result back to the LLM as a tool result message.

### Step-by-step for `ponddb_query`

1. The analyst LLM writes a SQL query and calls `ponddb_query {"sql": "SELECT ..."}`.
2. The SDK pipes the request to the MCP server.
3. `mcp-server-ponddb` calls `POST /pondapi/execute` with the SQL body.
4. PondDB runs the query against its SQLite store and returns rows as JSON.
5. The analyst LLM receives the tabular result in context and synthesises a recommendation.

## Running the tests

The integration tests do not require API keys or a running PondDB instance:

```bash
cd /path/to/db-engine
.venv/bin/pytest examples/claude-agent-sdk/test_integration.py -v
```

Tests cover:

- MCP server config structure (required keys, env vars)
- Researcher agent config (tools, workgroup)
- Analyst agent config (tools, workgroup match)
- demo.py imports correctly with a mocked SDK
- All 5 MCP tool schemas from `mcp-server-ponddb`
