"""Integration tests for the schema browser sidebar feature.

Defines expected behavior for:
  GET /schema?session_id={id}  — introspect DuckDB session schema
  GET /editor                  — HTML editor page with sidebar elements

Tests will FAIL until:
  - GET /schema route is added to app.py
  - editor.html is updated with sidebar markup and JS
"""

import importlib

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-schema-browser-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)
    monkeypatch.setenv("POND_JWT_SECRET", "test-schema-jwt")
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", "test-schema-session")


@pytest.fixture
def client(_set_env) -> TestClient:
    import ponddb.app as app_module

    importlib.reload(app_module)
    from ponddb.app import app

    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def session_id(client: TestClient, auth_headers: dict) -> str:
    resp = client.post("/session", headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# GET /schema — happy path: empty session
# ---------------------------------------------------------------------------


def test_schema_returns_200_for_valid_session(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    assert resp.status_code == 200


def test_schema_returns_json_content_type(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    assert "application/json" in resp.headers["content-type"]


def test_schema_empty_session_returns_list(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Fresh DuckDB in-memory session with no user tables returns a list (possibly empty)."""
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    assert isinstance(data, list)


def test_schema_response_structure(client: TestClient, session_id: str, auth_headers: dict) -> None:
    """Each table entry has table_name (str) and columns (list of {name, type})."""
    # Create a table in the session first
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE test_tbl (id INTEGER, name VARCHAR)"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    assert isinstance(data, list)
    # Find our created table
    tables = {t["table_name"]: t for t in data}
    assert "test_tbl" in tables, f"Expected test_tbl in {list(tables.keys())}"
    tbl = tables["test_tbl"]
    assert "table_name" in tbl
    assert "columns" in tbl
    assert isinstance(tbl["columns"], list)


def test_schema_column_has_name_and_type(client: TestClient, session_id: str, auth_headers: dict) -> None:
    """Column entries must have both 'name' and 'type' keys."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE typed_tbl (id INTEGER, label VARCHAR, score DOUBLE)"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    tables = {t["table_name"]: t for t in data}
    assert "typed_tbl" in tables
    cols = tables["typed_tbl"]["columns"]
    assert len(cols) == 3
    for col in cols:
        assert "name" in col, f"Column entry missing 'name': {col}"
        assert "type" in col, f"Column entry missing 'type': {col}"


def test_schema_column_names_are_correct(client: TestClient, session_id: str, auth_headers: dict) -> None:
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE col_test (alpha INTEGER, beta VARCHAR)"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    tables = {t["table_name"]: t for t in data}
    col_names = [c["name"] for c in tables["col_test"]["columns"]]
    assert "alpha" in col_names
    assert "beta" in col_names


def test_schema_column_types_are_strings(client: TestClient, session_id: str, auth_headers: dict) -> None:
    """Type values must be non-empty strings (e.g. 'INTEGER', 'VARCHAR')."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE type_test (x INTEGER, y DOUBLE, z VARCHAR)"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    tables = {t["table_name"]: t for t in data}
    for col in tables["type_test"]["columns"]:
        assert isinstance(col["type"], str)
        assert len(col["type"]) > 0


def test_schema_includes_multiple_tables(client: TestClient, session_id: str, auth_headers: dict) -> None:
    """Multiple tables created in the same session all appear in schema."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE alpha_tbl (id INTEGER)"},
        headers=auth_headers,
    )
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE beta_tbl (val VARCHAR)"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    names = [t["table_name"] for t in data]
    assert "alpha_tbl" in names
    assert "beta_tbl" in names


def test_schema_only_returns_user_tables_not_system(client: TestClient, session_id: str, auth_headers: dict) -> None:
    """Schema must not include DuckDB internal system tables/schemas (e.g. information_schema tables)."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE TABLE user_visible (id INTEGER)"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    names = [t["table_name"] for t in data]
    # System schema entries should not appear directly
    assert "tables" not in names  # information_schema.tables internal name
    assert "columns" not in names  # information_schema.columns internal name


# ---------------------------------------------------------------------------
# GET /schema — error cases
# ---------------------------------------------------------------------------


def test_schema_missing_session_id_returns_422(client: TestClient, auth_headers: dict) -> None:
    """session_id is required; omitting it returns 422 Unprocessable Entity."""
    resp = client.get("/schema", headers=auth_headers)
    assert resp.status_code == 422


def test_schema_unknown_session_returns_404(client: TestClient, auth_headers: dict) -> None:
    """Non-existent session_id returns 404 Not Found."""
    resp = client.get("/schema?session_id=does-not-exist-0000", headers=auth_headers)
    assert resp.status_code == 404


def test_schema_response_detail_on_missing_session(client: TestClient, auth_headers: dict) -> None:
    """404 response should include a 'detail' key."""
    resp = client.get("/schema?session_id=ghost-session", headers=auth_headers)
    data = resp.json()
    assert "detail" in data


# ---------------------------------------------------------------------------
# GET /schema — schema isolation between sessions
# ---------------------------------------------------------------------------


def test_schema_is_session_scoped(client: TestClient, auth_headers: dict) -> None:
    """Tables created in session A must not appear in session B."""
    sid_a = client.post("/session", headers=auth_headers).json()["session_id"]
    sid_b = client.post("/session", headers=auth_headers).json()["session_id"]

    client.post(
        "/query",
        json={"session_id": sid_a, "sql": "CREATE TABLE session_a_table (x INTEGER)"},
        headers=auth_headers,
    )

    resp_b = client.get(f"/schema?session_id={sid_b}", headers=auth_headers)
    names_b = [t["table_name"] for t in resp_b.json()]
    assert "session_a_table" not in names_b


def test_schema_tables_are_independent_per_session(client: TestClient, auth_headers: dict) -> None:
    """Tables created in session B must appear in session B but not session A."""
    sid_a = client.post("/session", headers=auth_headers).json()["session_id"]
    sid_b = client.post("/session", headers=auth_headers).json()["session_id"]

    client.post(
        "/query",
        json={"session_id": sid_b, "sql": "CREATE TABLE only_in_b (z DOUBLE)"},
        headers=auth_headers,
    )

    resp_a = client.get(f"/schema?session_id={sid_a}", headers=auth_headers)
    names_a = [t["table_name"] for t in resp_a.json()]
    assert "only_in_b" not in names_a

    resp_b = client.get(f"/schema?session_id={sid_b}", headers=auth_headers)
    names_b = [t["table_name"] for t in resp_b.json()]
    assert "only_in_b" in names_b


# ---------------------------------------------------------------------------
# GET /schema — after session destroy
# ---------------------------------------------------------------------------


def test_schema_after_session_destroy_returns_404(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Destroyed session returns 404 for schema request."""
    client.delete(f"/session/{session_id}", headers=auth_headers)
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /editor — HTML structure for sidebar
# ---------------------------------------------------------------------------


def test_editor_page_returns_200(client: TestClient) -> None:
    resp = client.get("/editor")
    assert resp.status_code == 200


def test_editor_page_is_html(client: TestClient) -> None:
    resp = client.get("/editor")
    assert "text/html" in resp.headers["content-type"]


def test_editor_page_has_schema_sidebar_element(client: TestClient) -> None:
    """Editor HTML must include a sidebar container with id='schema-sidebar'."""
    resp = client.get("/editor")
    assert 'id="schema-sidebar"' in resp.text or "id='schema-sidebar'" in resp.text


def test_editor_page_has_sidebar_toggle_button(client: TestClient) -> None:
    """Editor HTML must include a button to collapse/expand the sidebar."""
    resp = client.get("/editor")
    html = resp.text
    # Must have a toggle or collapse button referencing the sidebar
    assert (
        "sidebar-toggle" in html
        or "toggle-sidebar" in html
        or "collapse-sidebar" in html
        or "id=\"sidebar-btn\"" in html
        or "id='sidebar-btn'" in html
    )


def test_editor_page_has_schema_fetch_js(client: TestClient) -> None:
    """Editor HTML must include JavaScript that fetches /schema endpoint."""
    resp = client.get("/editor")
    assert "/schema" in resp.text


def test_editor_page_has_table_list_container(client: TestClient) -> None:
    """Editor HTML must have a container for the list of tables (schema tree)."""
    resp = client.get("/editor")
    html = resp.text
    assert (
        "schema-tables" in html
        or "table-list" in html
        or "id=\"schema-tree\"" in html
        or "id='schema-tree'" in html
    )


def test_editor_page_sidebar_has_data_table_click_behavior(client: TestClient) -> None:
    """Editor HTML must include JS that inserts a table name into the editor on click."""
    resp = client.get("/editor")
    html = resp.text
    # The JS should handle click events to insert table names
    assert "insertText" in html or "insert" in html.lower()
    # And it should reference table names from schema
    assert "table_name" in html or "tableName" in html


def test_editor_page_layout_has_two_pane_structure(client: TestClient) -> None:
    """Editor page must use a two-pane layout (sidebar + main content area)."""
    resp = client.get("/editor")
    html = resp.text
    # Should have both sidebar and main/editor container
    assert "schema-sidebar" in html
    assert 'id="editor"' in html or "id='editor'" in html


def test_editor_page_sidebar_shows_column_types(client: TestClient) -> None:
    """Editor HTML must include template/JS that renders column name + type in the sidebar."""
    resp = client.get("/editor")
    html = resp.text
    # The JS rendering code should reference 'type' for columns
    assert "col.type" in html or ".type" in html or "column_type" in html or "colType" in html


def test_editor_page_sidebar_collapsible_css(client: TestClient) -> None:
    """Editor CSS must include styles for the sidebar collapsed state."""
    resp = client.get("/editor")
    html = resp.text
    # CSS or JS must handle the hidden/collapsed state
    assert "collapsed" in html or "sidebar-hidden" in html or "display: none" in html or "display:none" in html


# ---------------------------------------------------------------------------
# GET /schema — CTAS and views also appear
# ---------------------------------------------------------------------------


def test_schema_includes_view(client: TestClient, session_id: str, auth_headers: dict) -> None:
    """Views created in a session should appear in the schema."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE VIEW my_view AS SELECT 1 AS num"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    names = [t["table_name"] for t in data]
    assert "my_view" in names


def test_schema_view_has_columns(client: TestClient, session_id: str, auth_headers: dict) -> None:
    """Views should have column metadata in schema response."""
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "CREATE VIEW view_cols AS SELECT 42 AS answer, 'hello' AS greeting"},
        headers=auth_headers,
    )
    resp = client.get(f"/schema?session_id={session_id}", headers=auth_headers)
    data = resp.json()
    tables = {t["table_name"]: t for t in data}
    assert "view_cols" in tables
    col_names = [c["name"] for c in tables["view_cols"]["columns"]]
    assert "answer" in col_names
    assert "greeting" in col_names
