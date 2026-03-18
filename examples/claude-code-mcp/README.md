# PondDB + Claude Code (MCP)

Use PondDB as persistent memory for Claude Code via the Model Context Protocol.

## Setup

1. **Start PondDB:**
   ```bash
   cd pond-db && docker compose up -d
   ```

2. **Install MCP server:**
   ```bash
   pip install mcp-server-ponddb
   ```

3. **Configure Claude Code** — add to your MCP config:
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

## Try it

Once configured, Claude Code can use these tools:

- **"Remember that our deployment uses Kubernetes with 3 replicas"**
  → Calls `ponddb_remember` → stored as semantic memory

- **"What do you remember about our deployment?"**
  → Calls `ponddb_recall` → searches memories with content_contains="deployment"

- **"Forget the deployment memory"**
  → Calls `ponddb_forget` → soft-deletes the memory

- **"That deployment info was really useful"**
  → Calls `ponddb_feedback` with reward=0.8 → boosts utility score

## What's happening under the hood

When you say "Remember that our deployment uses Kubernetes with 3 replicas":

1. Claude Code calls the `ponddb_remember` MCP tool
2. The MCP server sends `POST /memories` to PondDB:
   ```json
   {
     "agent_id": "claude-code",
     "memory_type": "semantic",
     "content": {"fact": "Our deployment uses Kubernetes with 3 replicas"},
     "access_scope": "workgroup"
   }
   ```
3. PondDB stores the memory and logs it in `memory_access_log`
4. You can query the log with SQL:
   ```sql
   SELECT agent_id, action, created_at
   FROM memory_access_log
   WHERE agent_id = 'claude-code'
   ORDER BY created_at DESC LIMIT 10;
   ```

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `ponddb_remember` | Store a memory (5 types: working, episodic, semantic, procedural, shared) |
| `ponddb_recall` | Search memories by type, content, importance |
| `ponddb_query` | Run SQL queries via PondAPI |
| `ponddb_forget` | Soft-delete a memory |
| `ponddb_feedback` | Rate a memory's usefulness (-1.0 to 1.0) |
