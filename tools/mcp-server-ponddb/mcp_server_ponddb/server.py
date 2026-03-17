"""MCP server implementation for PondDB."""

import json

from mcp.server import Server
from mcp.types import TextContent, Tool

from .ponddb_client import PondDBClient, PondDBError


def create_server(ponddb_url: str, api_key: str) -> Server:
    server = Server("mcp-server-ponddb")
    client = PondDBClient(ponddb_url, api_key)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="ponddb_query",
                description=(
                    "Execute a SQL query against PondDB and return results as JSON. "
                    "Use this for any data analysis or reporting question."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "SQL query to execute",
                        }
                    },
                    "required": ["sql"],
                },
            ),
            Tool(
                name="ponddb_list_datasets",
                description=(
                    "List all available tables/datasets in PondDB with their schemas "
                    "and metadata. Call this first to discover what data is available."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="ponddb_describe_table",
                description=(
                    "Get column names, types, and sample rows for a specific table. "
                    "Use before writing queries to understand the data shape."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": "Name of the table to describe",
                        }
                    },
                    "required": ["table_name"],
                },
            ),
            Tool(
                name="ponddb_upload_csv",
                description=(
                    "Upload a CSV file as a new queryable dataset in PondDB. "
                    "The CSV content is passed as a string."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Dataset name (becomes the table name)",
                        },
                        "csv_content": {
                            "type": "string",
                            "description": "Raw CSV content including header row",
                        },
                    },
                    "required": ["name", "csv_content"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "ponddb_query":
            return await _handle_query(client, arguments)
        if name == "ponddb_list_datasets":
            return await _handle_list_datasets(client)
        if name == "ponddb_describe_table":
            return await _handle_describe_table(client, arguments)
        if name == "ponddb_upload_csv":
            return await _handle_upload_csv(client, arguments)
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _handle_query(client: PondDBClient, arguments: dict) -> list[TextContent]:
    sql = arguments["sql"]
    try:
        data = client.execute(sql)
        rows = data.get("rows", [])
        row_count = data.get("rows_returned", len(rows))
        elapsed_ms = data.get("elapsed_ms", "?")
        text = (
            f"Query returned {row_count} row(s) in {elapsed_ms}ms:\n"
            + json.dumps(rows, indent=2)
        )
    except TimeoutError:
        text = "Query timed out after 30 seconds"
    except PondDBError as exc:
        text = f"Query failed: {exc}"
    return [TextContent(type="text", text=text)]


async def _handle_list_datasets(client: PondDBClient) -> list[TextContent]:
    datasets = client.list_datasets()
    schema = client.get_schema()
    text = json.dumps({"datasets": datasets, "schema": schema}, indent=2)
    return [TextContent(type="text", text=text)]


async def _handle_describe_table(
    client: PondDBClient, arguments: dict
) -> list[TextContent]:
    table = arguments["table_name"]
    schema = client.get_schema()
    table_schema = [s for s in schema if s.get("table") == table]

    try:
        sample_data = client.execute(f'SELECT * FROM "{table}" LIMIT 5')
        sample_rows = sample_data.get("rows", [])
        total_rows = sample_data.get("rows_returned", len(sample_rows))
    except (PondDBError, TimeoutError) as exc:
        sample_rows = []
        total_rows = 0

    text = json.dumps(
        {"columns": table_schema, "sample_rows": sample_rows, "total_rows": total_rows},
        indent=2,
    )
    return [TextContent(type="text", text=text)]


async def _handle_upload_csv(
    client: PondDBClient, arguments: dict
) -> list[TextContent]:
    name = arguments["name"]
    csv_content = arguments["csv_content"]
    try:
        result = client.upload_csv(name, csv_content)
        text = json.dumps(
            {
                "success": True,
                "table_name": result.get("name", name),
                "row_count": result.get("row_count", 0),
            },
            indent=2,
        )
    except Exception as exc:
        text = json.dumps({"success": False, "error": str(exc)}, indent=2)
    return [TextContent(type="text", text=text)]
