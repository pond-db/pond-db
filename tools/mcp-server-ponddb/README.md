# mcp-server-ponddb

MCP server exposing PondDB agent memory operations as 5 tools.

## Tools

| Tool | Maps to | Description |
|------|---------|-------------|
| `ponddb_remember` | POST /memories | Store a memory |
| `ponddb_recall` | GET /memories/search | Search memories |
| `ponddb_query` | POST /pondapi/execute | Run SQL |
| `ponddb_forget` | DELETE /memories/{id} | Soft-delete a memory |
| `ponddb_feedback` | POST /memories/{id}/feedback | Rate usefulness |

## Configuration

```bash
export PONDDB_URL=http://localhost:8432
export PONDDB_API_KEY=pk_your_key
export PONDDB_WORKGROUP=default
```

## Claude Desktop

```json
{
  "mcpServers": {
    "ponddb": {
      "command": "mcp-server-ponddb",
      "env": {
        "PONDDB_URL": "http://localhost:8432",
        "PONDDB_API_KEY": "pk_your_key"
      }
    }
  }
}
```
