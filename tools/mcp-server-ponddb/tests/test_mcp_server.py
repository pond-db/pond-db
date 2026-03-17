"""Tests for the PondDB MCP server.

All PondDB API calls are mocked — no running PondDB server needed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_server_ponddb.server import (
    _handle_describe_table,
    _handle_list_datasets,
    _handle_query,
    _handle_upload_csv,
    create_server,
)
from mcp_server_ponddb.ponddb_client import PondDBClient, PondDBError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(
    execute_result=None,
    execute_side_effect=None,
    datasets=None,
    schema=None,
    upload_result=None,
) -> MagicMock:
    client = MagicMock(spec=PondDBClient)
    if execute_side_effect is not None:
        client.execute.side_effect = execute_side_effect
    else:
        client.execute.return_value = execute_result or {
            "status": "complete",
            "rows": [[1, "Alice"]],
            "rows_returned": 1,
            "elapsed_ms": 42,
        }
    client.list_datasets.return_value = datasets or [{"name": "sales", "rows": 1000}]
    client.get_schema.return_value = schema or [
        {"table": "sales", "column": "id", "type": "INTEGER"},
        {"table": "sales", "column": "name", "type": "VARCHAR"},
    ]
    client.upload_csv.return_value = upload_result or {"name": "uploads", "row_count": 5}
    return client


# ---------------------------------------------------------------------------
# test_query_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_tool_returns_rows() -> None:
    client = _mock_client(
        execute_result={
            "status": "complete",
            "rows": [["Alice", 100], ["Bob", 200]],
            "rows_returned": 2,
            "elapsed_ms": 12,
        }
    )
    result = await _handle_query(client, {"sql": "SELECT name, revenue FROM sales"})

    assert len(result) == 1
    text = result[0].text
    assert "2 row(s)" in text
    assert "12ms" in text
    assert "Alice" in text
    assert "Bob" in text


@pytest.mark.asyncio
async def test_query_tool_sends_correct_sql() -> None:
    client = _mock_client()
    sql = "SELECT COUNT(*) FROM orders"
    await _handle_query(client, {"sql": sql})
    client.execute.assert_called_once_with(sql)


@pytest.mark.asyncio
async def test_query_tool_empty_result() -> None:
    client = _mock_client(
        execute_result={"status": "complete", "rows": [], "rows_returned": 0, "elapsed_ms": 5}
    )
    result = await _handle_query(client, {"sql": "SELECT 1 WHERE 1=0"})
    assert "0 row(s)" in result[0].text


# ---------------------------------------------------------------------------
# test_query_timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_timeout_returns_message() -> None:
    client = _mock_client(execute_side_effect=TimeoutError("timed out"))
    result = await _handle_query(client, {"sql": "SELECT sleep(999)"})
    assert "timed out" in result[0].text.lower()


# ---------------------------------------------------------------------------
# test_query_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_failed_returns_error_message() -> None:
    client = _mock_client(execute_side_effect=PondDBError("table not found"))
    result = await _handle_query(client, {"sql": "SELECT * FROM ghost_table"})
    text = result[0].text
    assert "failed" in text.lower()
    assert "table not found" in text


# ---------------------------------------------------------------------------
# test_list_datasets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_datasets_returns_structured_output() -> None:
    client = _mock_client(
        datasets=[{"name": "sales", "rows": 500}, {"name": "orders", "rows": 200}],
        schema=[
            {"table": "sales", "column": "id", "type": "INTEGER"},
            {"table": "orders", "column": "order_id", "type": "VARCHAR"},
        ],
    )
    result = await _handle_list_datasets(client)

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert "datasets" in data
    assert "schema" in data
    dataset_names = [d["name"] for d in data["datasets"]]
    assert "sales" in dataset_names
    assert "orders" in dataset_names


@pytest.mark.asyncio
async def test_list_datasets_calls_both_endpoints() -> None:
    client = _mock_client()
    await _handle_list_datasets(client)
    client.list_datasets.assert_called_once()
    client.get_schema.assert_called_once()


# ---------------------------------------------------------------------------
# test_describe_table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_table_combines_schema_and_sample() -> None:
    schema = [
        {"table": "sales", "column": "id", "type": "INTEGER"},
        {"table": "sales", "column": "name", "type": "VARCHAR"},
        {"table": "other", "column": "x", "type": "FLOAT"},  # should be excluded
    ]
    client = _mock_client(
        schema=schema,
        execute_result={
            "status": "complete",
            "rows": [[1, "Alice"], [2, "Bob"]],
            "rows_returned": 2,
            "elapsed_ms": 8,
        },
    )
    result = await _handle_describe_table(client, {"table_name": "sales"})

    data = json.loads(result[0].text)
    assert "columns" in data
    assert "sample_rows" in data
    # Only sales columns, not other
    col_names = [c["column"] for c in data["columns"]]
    assert "id" in col_names
    assert "x" not in col_names
    assert data["sample_rows"] == [[1, "Alice"], [2, "Bob"]]


@pytest.mark.asyncio
async def test_describe_table_query_uses_correct_table() -> None:
    client = _mock_client()
    await _handle_describe_table(client, {"table_name": "my_table"})
    call_args = client.execute.call_args[0][0]
    assert "my_table" in call_args
    assert "LIMIT 5" in call_args


@pytest.mark.asyncio
async def test_describe_table_handles_query_error_gracefully() -> None:
    client = _mock_client(execute_side_effect=PondDBError("permission denied"))
    # Should not raise — returns empty sample
    result = await _handle_describe_table(client, {"table_name": "secret_table"})
    data = json.loads(result[0].text)
    assert data["sample_rows"] == []


# ---------------------------------------------------------------------------
# test_upload_csv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_csv_returns_success() -> None:
    client = _mock_client(upload_result={"name": "mydata", "row_count": 42})
    result = await _handle_upload_csv(
        client, {"name": "mydata", "csv_content": "a,b\n1,2\n3,4"}
    )
    data = json.loads(result[0].text)
    assert data["success"] is True
    assert data["table_name"] == "mydata"
    assert data["row_count"] == 42


@pytest.mark.asyncio
async def test_upload_csv_sends_name_and_content() -> None:
    client = _mock_client()
    await _handle_upload_csv(
        client, {"name": "test_table", "csv_content": "x,y\n1,2"}
    )
    client.upload_csv.assert_called_once_with("test_table", "x,y\n1,2")


@pytest.mark.asyncio
async def test_upload_csv_returns_failure_on_error() -> None:
    client = _mock_client()
    client.upload_csv.side_effect = Exception("storage full")
    result = await _handle_upload_csv(
        client, {"name": "bad", "csv_content": "a\n1"}
    )
    data = json.loads(result[0].text)
    assert data["success"] is False
    assert "storage full" in data["error"]


# ---------------------------------------------------------------------------
# test_create_server
# ---------------------------------------------------------------------------


def test_create_server_returns_server_instance() -> None:
    """create_server should return an MCP Server without raising."""
    with patch("mcp_server_ponddb.server.PondDBClient"):
        srv = create_server("http://localhost:8432", "pk_test")
    assert srv is not None
    assert srv.name == "mcp-server-ponddb"
