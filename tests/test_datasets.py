"""Integration tests for the dataset manager — POST/GET/DELETE /datasets.

Defines expected behavior for:
  - POST /datasets: multipart upload of CSV and Parquet files
  - GET /datasets: list registered datasets with metadata (size, row_count)
  - DELETE /datasets/{name}: remove a dataset
  - DuckDB auto-registration: uploaded files are queryable as tables
  - Configurable storage path via POND_DATA_ROOT env var
  - Auth: all dataset endpoints require valid X-API-Key
  - Error cases: unsupported file type, duplicate name, not-found delete
"""

import csv
import importlib
import io
import os

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-datasets-key"


# ---------------------------------------------------------------------------
# Helpers for creating test files in memory
# ---------------------------------------------------------------------------


def _make_csv_bytes(rows: list[list]) -> bytes:
    """Build an in-memory CSV file from header + rows."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode()


def _make_parquet_bytes() -> bytes:
    """Return a minimal valid Parquet file (magic bytes + empty content)."""
    # A real Parquet file starts and ends with b"PAR1"
    # We use pyarrow to build a tiny valid file if available,
    # otherwise fall back to a minimal stub that tests file-type detection.
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({"id": [1, 2, 3], "value": ["a", "b", "c"]})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        return buf.getvalue()
    except ImportError:
        # Minimal Parquet magic — enough to pass MIME/extension detection
        return b"PAR1" + b"\x00" * 20 + b"PAR1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_DATA_ROOT", str(tmp_path / "datasets"))


@pytest.fixture
def client(set_api_key) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


def _auth() -> dict:
    return {"X-API-Key": VALID_KEY}


def _upload_csv(
    client: TestClient,
    *,
    filename: str = "sales.csv",
    content: bytes | None = None,
    headers: dict | None = None,
) -> "requests.Response":
    if content is None:
        content = _make_csv_bytes(
            [["id", "name", "amount"], ["1", "Alice", "100"], ["2", "Bob", "200"]]
        )
    if headers is None:
        headers = _auth()
    return client.post(
        "/datasets",
        files={"file": (filename, content, "text/csv")},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /datasets — happy path (CSV)
# ---------------------------------------------------------------------------


def test_upload_csv_returns_201(client: TestClient) -> None:
    resp = _upload_csv(client, filename="test_upload.csv")
    assert resp.status_code == 201


def test_upload_csv_response_has_name(client: TestClient) -> None:
    resp = _upload_csv(client, filename="mydata.csv")
    body = resp.json()
    assert "name" in body


def test_upload_csv_response_name_matches_filename(client: TestClient) -> None:
    resp = _upload_csv(client, filename="revenues.csv")
    body = resp.json()
    # name should be derived from filename (without extension)
    assert body["name"] == "revenues"


def test_upload_csv_response_has_row_count(client: TestClient) -> None:
    content = _make_csv_bytes(
        [["x", "y"], ["1", "2"], ["3", "4"], ["5", "6"]]
    )
    resp = _upload_csv(client, filename="rows_check.csv", content=content)
    body = resp.json()
    assert "row_count" in body
    assert body["row_count"] == 3  # 3 data rows, 1 header


def test_upload_csv_response_has_size_bytes(client: TestClient) -> None:
    resp = _upload_csv(client, filename="size_check.csv")
    body = resp.json()
    assert "size_bytes" in body
    assert isinstance(body["size_bytes"], int)
    assert body["size_bytes"] > 0


def test_upload_csv_response_has_columns(client: TestClient) -> None:
    content = _make_csv_bytes([["alpha", "beta", "gamma"], ["1", "2", "3"]])
    resp = _upload_csv(client, filename="cols_check.csv", content=content)
    body = resp.json()
    assert "columns" in body
    assert set(body["columns"]) == {"alpha", "beta", "gamma"}


def test_upload_csv_response_has_format(client: TestClient) -> None:
    resp = _upload_csv(client, filename="fmt_check.csv")
    body = resp.json()
    assert "format" in body
    assert body["format"] == "csv"


# ---------------------------------------------------------------------------
# POST /datasets — happy path (Parquet)
# ---------------------------------------------------------------------------


def test_upload_parquet_returns_201(client: TestClient) -> None:
    content = _make_parquet_bytes()
    resp = client.post(
        "/datasets",
        files={"file": ("events.parquet", content, "application/octet-stream")},
        headers=_auth(),
    )
    assert resp.status_code == 201


def test_upload_parquet_response_format_is_parquet(client: TestClient) -> None:
    content = _make_parquet_bytes()
    resp = client.post(
        "/datasets",
        files={"file": ("events.parquet", content, "application/octet-stream")},
        headers=_auth(),
    )
    body = resp.json()
    assert body["format"] == "parquet"


def test_upload_parquet_name_derived_from_filename(client: TestClient) -> None:
    content = _make_parquet_bytes()
    resp = client.post(
        "/datasets",
        files={"file": ("log_events.parquet", content, "application/octet-stream")},
        headers=_auth(),
    )
    assert resp.json()["name"] == "log_events"


# ---------------------------------------------------------------------------
# POST /datasets — error cases
# ---------------------------------------------------------------------------


def test_upload_unsupported_type_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/datasets",
        files={"file": ("bad.txt", b"hello world", "text/plain")},
        headers=_auth(),
    )
    assert resp.status_code == 400


def test_upload_unsupported_type_has_detail(client: TestClient) -> None:
    resp = client.post(
        "/datasets",
        files={"file": ("bad.json", b'{"key":"val"}', "application/json")},
        headers=_auth(),
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_upload_without_auth_returns_401(client: TestClient) -> None:
    resp = _upload_csv(client, filename="no_auth.csv", headers={})
    assert resp.status_code == 401


def test_upload_wrong_api_key_returns_401(client: TestClient) -> None:
    resp = _upload_csv(
        client,
        filename="bad_key.csv",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


def test_upload_duplicate_name_returns_409(client: TestClient) -> None:
    _upload_csv(client, filename="dup.csv")
    resp = _upload_csv(client, filename="dup.csv")
    assert resp.status_code == 409


def test_upload_empty_file_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/datasets",
        files={"file": ("empty.csv", b"", "text/csv")},
        headers=_auth(),
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /datasets — list datasets
# ---------------------------------------------------------------------------


def test_get_datasets_returns_200(client: TestClient) -> None:
    resp = client.get("/datasets", headers=_auth())
    assert resp.status_code == 200


def test_get_datasets_returns_list(client: TestClient) -> None:
    resp = client.get("/datasets", headers=_auth())
    assert isinstance(resp.json(), list)


def test_get_datasets_empty_when_none_uploaded(client: TestClient) -> None:
    resp = client.get("/datasets", headers=_auth())
    assert resp.json() == []


def test_get_datasets_shows_uploaded_dataset(client: TestClient) -> None:
    _upload_csv(client, filename="visible.csv")
    resp = client.get("/datasets", headers=_auth())
    names = [d["name"] for d in resp.json()]
    assert "visible" in names


def test_get_datasets_includes_size_bytes(client: TestClient) -> None:
    _upload_csv(client, filename="size_list.csv")
    resp = client.get("/datasets", headers=_auth())
    datasets = resp.json()
    assert len(datasets) >= 1
    for d in datasets:
        assert "size_bytes" in d
        assert d["size_bytes"] > 0


def test_get_datasets_includes_row_count(client: TestClient) -> None:
    content = _make_csv_bytes([["a"], ["1"], ["2"], ["3"]])
    _upload_csv(client, filename="row_list.csv", content=content)
    resp = client.get("/datasets", headers=_auth())
    match = next(d for d in resp.json() if d["name"] == "row_list")
    assert "row_count" in match
    assert match["row_count"] == 3


def test_get_datasets_includes_format(client: TestClient) -> None:
    _upload_csv(client, filename="fmt_list.csv")
    resp = client.get("/datasets", headers=_auth())
    match = next(d for d in resp.json() if d["name"] == "fmt_list")
    assert match["format"] == "csv"


def test_get_datasets_includes_columns(client: TestClient) -> None:
    content = _make_csv_bytes([["col1", "col2"], ["x", "y"]])
    _upload_csv(client, filename="cols_list.csv", content=content)
    resp = client.get("/datasets", headers=_auth())
    match = next(d for d in resp.json() if d["name"] == "cols_list")
    assert "columns" in match


def test_get_datasets_includes_created_at(client: TestClient) -> None:
    _upload_csv(client, filename="ts_list.csv")
    resp = client.get("/datasets", headers=_auth())
    match = next(d for d in resp.json() if d["name"] == "ts_list")
    assert "created_at" in match


def test_get_datasets_multiple_datasets(client: TestClient) -> None:
    _upload_csv(client, filename="first.csv")
    _upload_csv(client, filename="second.csv")
    resp = client.get("/datasets", headers=_auth())
    names = {d["name"] for d in resp.json()}
    assert {"first", "second"}.issubset(names)


def test_get_datasets_without_auth_returns_401(client: TestClient) -> None:
    resp = client.get("/datasets")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /datasets/{name} — single dataset metadata
# ---------------------------------------------------------------------------


def test_get_single_dataset_returns_200(client: TestClient) -> None:
    _upload_csv(client, filename="single.csv")
    resp = client.get("/datasets/single", headers=_auth())
    assert resp.status_code == 200


def test_get_single_dataset_not_found_returns_404(client: TestClient) -> None:
    resp = client.get("/datasets/nonexistent_xyz", headers=_auth())
    assert resp.status_code == 404


def test_get_single_dataset_has_name(client: TestClient) -> None:
    _upload_csv(client, filename="named.csv")
    resp = client.get("/datasets/named", headers=_auth())
    assert resp.json()["name"] == "named"


def test_get_single_dataset_has_row_count(client: TestClient) -> None:
    content = _make_csv_bytes([["n"], ["1"], ["2"]])
    _upload_csv(client, filename="rc_single.csv", content=content)
    resp = client.get("/datasets/rc_single", headers=_auth())
    assert resp.json()["row_count"] == 2


# ---------------------------------------------------------------------------
# DELETE /datasets/{name}
# ---------------------------------------------------------------------------


def test_delete_dataset_returns_200(client: TestClient) -> None:
    _upload_csv(client, filename="to_delete.csv")
    resp = client.delete("/datasets/to_delete", headers=_auth())
    assert resp.status_code == 200


def test_delete_dataset_removes_from_list(client: TestClient) -> None:
    _upload_csv(client, filename="gone.csv")
    client.delete("/datasets/gone", headers=_auth())
    resp = client.get("/datasets", headers=_auth())
    names = [d["name"] for d in resp.json()]
    assert "gone" not in names


def test_delete_nonexistent_dataset_returns_404(client: TestClient) -> None:
    resp = client.delete("/datasets/does_not_exist", headers=_auth())
    assert resp.status_code == 404


def test_delete_dataset_without_auth_returns_401(client: TestClient) -> None:
    _upload_csv(client, filename="del_auth.csv")
    resp = client.delete("/datasets/del_auth")
    assert resp.status_code == 401


def test_delete_dataset_idempotent_second_delete_returns_404(client: TestClient) -> None:
    _upload_csv(client, filename="idem.csv")
    client.delete("/datasets/idem", headers=_auth())
    resp = client.delete("/datasets/idem", headers=_auth())
    assert resp.status_code == 404


def test_delete_response_has_detail(client: TestClient) -> None:
    _upload_csv(client, filename="del_detail.csv")
    resp = client.delete("/datasets/del_detail", headers=_auth())
    body = resp.json()
    assert "detail" in body or "name" in body  # some confirmation field


# ---------------------------------------------------------------------------
# DuckDB auto-registration — uploaded datasets are queryable
# ---------------------------------------------------------------------------


def test_uploaded_csv_queryable_via_session(client: TestClient) -> None:
    """After upload, the dataset should be queryable as a table via /query."""
    content = _make_csv_bytes(
        [["id", "val"], ["1", "hello"], ["2", "world"]]
    )
    _upload_csv(client, filename="queryable.csv", content=content)

    # Create a session
    sess_resp = client.post("/session")
    assert sess_resp.status_code == 201
    session_id = sess_resp.json()["session_id"]

    # Query the auto-registered table
    query_resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT COUNT(*) AS n FROM queryable"},
        headers=_auth(),
    )
    assert query_resp.status_code == 200
    data = query_resp.json()
    assert data["rows"][0][0] == 2  # 2 data rows


def test_uploaded_csv_columns_accessible_in_query(client: TestClient) -> None:
    content = _make_csv_bytes([["product", "price"], ["widget", "9.99"]])
    _upload_csv(client, filename="products.csv", content=content)

    sess_resp = client.post("/session")
    session_id = sess_resp.json()["session_id"]

    query_resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT product, price FROM products"},
        headers=_auth(),
    )
    assert query_resp.status_code == 200
    data = query_resp.json()
    assert "product" in data["columns"]
    assert "price" in data["columns"]


def test_deleted_dataset_no_longer_queryable(client: TestClient) -> None:
    """After DELETE /datasets/{name}, querying the table should fail."""
    content = _make_csv_bytes([["x"], ["1"]])
    _upload_csv(client, filename="temp_table.csv", content=content)
    client.delete("/datasets/temp_table", headers=_auth())

    sess_resp = client.post("/session")
    session_id = sess_resp.json()["session_id"]

    query_resp = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT * FROM temp_table"},
        headers=_auth(),
    )
    # Should fail — table no longer registered
    assert query_resp.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Configurable storage path (POND_DATA_ROOT)
# ---------------------------------------------------------------------------


def test_dataset_file_stored_in_data_root(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Files should be stored under POND_DATA_ROOT."""
    data_root = str(tmp_path / "datasets")
    monkeypatch.setenv("POND_DATA_ROOT", data_root)

    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    fresh_client = TestClient(app)
    _upload_csv(fresh_client, filename="stored.csv")

    # After upload the data root should contain the file
    assert os.path.isdir(data_root)
    files_in_root = os.listdir(data_root)
    assert any("stored" in f for f in files_in_root)


# ---------------------------------------------------------------------------
# File name → table name normalisation
# ---------------------------------------------------------------------------


def test_filename_with_spaces_normalized_to_underscores(client: TestClient) -> None:
    """Spaces in filenames should be converted to underscores for the table name."""
    content = _make_csv_bytes([["a"], ["1"]])
    resp = client.post(
        "/datasets",
        files={"file": ("my data.csv", content, "text/csv")},
        headers=_auth(),
    )
    assert resp.status_code == 201
    # name should be safe for SQL
    name = resp.json()["name"]
    assert " " not in name


def test_filename_uppercase_normalised(client: TestClient) -> None:
    """Table names should be lowercase / normalized."""
    content = _make_csv_bytes([["a"], ["1"]])
    resp = client.post(
        "/datasets",
        files={"file": ("UPPER.csv", content, "text/csv")},
        headers=_auth(),
    )
    assert resp.status_code == 201
    name = resp.json()["name"]
    assert name == name.lower()
