"""LangGraph tools that call PondDB's HTTP API.

These tools let LangGraph agents interact with PondDB — uploading data,
running SQL queries, saving queries, and browsing schemas. Agents use
PondDB as shared state instead of passing data through the LLM context.
"""

from __future__ import annotations

import json
import os
import time

import httpx
from langchain_core.tools import tool

PONDDB_URL = os.getenv("PONDDB_URL", "http://localhost:8432")
PONDDB_KEY = os.getenv("PONDDB_API_KEY")

_POLL_INTERVAL = 0.5
_POLL_MAX_ATTEMPTS = 60


def _headers() -> dict[str, str]:
    """Return auth headers for PondDB requests."""
    return {"Authorization": f"Bearer {PONDDB_KEY}"}


@tool
def query_ponddb(sql: str) -> str:
    """Execute a SQL query against PondDB and return results as JSON.

    Use this for any data analysis — don't try to calculate numbers yourself.
    PondDB runs DuckDB under the hood, so you can use DuckDB SQL syntax.
    """
    headers = _headers()

    # Submit async query
    resp = httpx.post(
        f"{PONDDB_URL}/pondapi/execute",
        json={"sql": sql},
        headers=headers,
        timeout=30,
    )
    if resp.status_code not in (200, 201, 202):
        return f"Query submission failed ({resp.status_code}): {resp.text}"

    eid = resp.json()["execution_id"]

    # Poll for results
    for _ in range(_POLL_MAX_ATTEMPTS):
        result = httpx.get(
            f"{PONDDB_URL}/pondapi/execute/{eid}/result",
            headers=headers,
            timeout=30,
        )
        data = result.json()

        if data["status"] == "complete":
            rows = data.get("rows", [])
            count = data.get("rows_returned", len(rows))
            preview = rows[:20]
            return (
                f"Query returned {count} rows:\n"
                f"{json.dumps(preview, indent=2, default=str)}"
            )

        if data["status"] == "failed":
            return f"Query failed: {data.get('error_message', 'unknown error')}"

        time.sleep(_POLL_INTERVAL)

    return "Query timed out after 30 seconds"


@tool
def upload_csv_to_ponddb(file_path: str, table_name: str) -> str:
    """Upload a CSV file to PondDB as a queryable table.

    Args:
        file_path: Path to the CSV file on disk.
        table_name: Name for the table in PondDB (used in SQL queries).
    """
    headers = _headers()

    with open(file_path, "rb") as f:
        resp = httpx.post(
            f"{PONDDB_URL}/datasets",
            files={"file": (f"{table_name}.csv", f, "text/csv")},
            headers=headers,
            timeout=60,
        )

    if resp.status_code in (200, 201):
        return (
            f"Uploaded '{table_name}' successfully. "
            f"You can now query it with: SELECT * FROM {table_name}"
        )

    return f"Upload failed ({resp.status_code}): {resp.text}"


@tool
def list_ponddb_tables() -> str:
    """List all available tables and their schemas in PondDB.

    Returns dataset info and column-level schema for each table.
    """
    headers = _headers()

    datasets_resp = httpx.get(
        f"{PONDDB_URL}/datasets", headers=headers, timeout=15,
    )
    schema_resp = httpx.get(
        f"{PONDDB_URL}/schema", headers=headers, timeout=15,
    )

    datasets = datasets_resp.json() if datasets_resp.status_code == 200 else []
    schema = schema_resp.json() if schema_resp.status_code == 200 else []

    return json.dumps({"datasets": datasets, "schema": schema}, indent=2)


@tool
def save_query(title: str, sql: str) -> str:
    """Save a named query to PondDB so other agents can find and reuse it.

    Args:
        title: Descriptive title for the query (e.g., "Top Regions by Revenue").
        sql: The SQL query to save.
    """
    headers = {**_headers(), "Content-Type": "application/json"}

    resp = httpx.post(
        f"{PONDDB_URL}/queries",
        json={"title": title, "sql": sql},
        headers=headers,
        timeout=15,
    )

    if resp.status_code in (200, 201):
        slug = resp.json().get("slug", title.lower().replace(" ", "-"))
        return f"Query saved as '{title}' (slug: {slug})"

    return f"Save failed ({resp.status_code}): {resp.text}"


@tool
def get_saved_queries() -> str:
    """List all saved queries in PondDB.

    Returns query titles, SQL, and slugs that other agents have saved.
    """
    headers = _headers()
    resp = httpx.get(
        f"{PONDDB_URL}/queries", headers=headers, timeout=15,
    )

    if resp.status_code == 200:
        return json.dumps(resp.json(), indent=2)

    return f"Failed to fetch queries ({resp.status_code}): {resp.text}"
