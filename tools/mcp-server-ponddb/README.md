# MCP Server for PondDB

Give any MCP-compatible AI agent (Claude Code, OpenClaw, LangChain) direct SQL access to your PondDB data.

## Install

```bash
pip install mcp-server-ponddb
# or run without installing:
uvx mcp-server-ponddb --url http://localhost:8432 --api-key pk_...
```

## Configure Claude Code

Add to your `~/.claude/settings.json` (or project `.claude/settings.json`):

```json
{
  "mcpServers": {
    "ponddb": {
      "command": "uvx",
      "args": ["mcp-server-ponddb", "--url", "http://localhost:8432", "--api-key", "pk_..."]
    }
  }
}
```

Then ask Claude: *"What tables are in my PondDB?"* or *"Show me top revenue by region."*

## Available Tools

| Tool | Description |
|------|-------------|
| `ponddb_query` | Execute SQL, get results as JSON |
| `ponddb_list_datasets` | List all tables with schemas and metadata |
| `ponddb_describe_table` | Column details + 5-row sample |
| `ponddb_upload_csv` | Upload CSV content as a queryable table |

## Example Usage

Once configured, agents can run queries like:

```
User: What's the total revenue by region in the sales table?

Claude uses ponddb_describe_table("sales") → sees columns: region, revenue
Claude uses ponddb_query("SELECT region, SUM(revenue) FROM sales GROUP BY region") → returns results
```

## Development

```bash
git clone ...
cd tools/mcp-server-ponddb
pip install -e ".[dev]"
pytest tests/ -v
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PONDDB_URL` | PondDB server URL (overrides `--url`) |
| `PONDDB_API_KEY` | API key (overrides `--api-key`) |
