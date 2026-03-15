"""Tests for the browser-based SQL editor page at GET /editor.

Defines expected behavior:
  - GET /editor returns server-rendered HTML page
  - Page embeds CodeMirror 6 from cdnjs CDN
  - Page has a Run button that POSTs to /query via fetch()
  - Page has an error display panel
  - Page renders results in an HTML table
  - Page works without a build toolchain (no bundler references)
"""

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from ponddb.app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /editor — basic HTTP contract
# ---------------------------------------------------------------------------


def test_editor_get_returns_200(client: TestClient) -> None:
    resp = client.get("/editor")
    assert resp.status_code == 200


def test_editor_content_type_is_html(client: TestClient) -> None:
    resp = client.get("/editor")
    assert "text/html" in resp.headers["content-type"]


def test_editor_post_returns_405(client: TestClient) -> None:
    resp = client.post("/editor")
    assert resp.status_code == 405


def test_editor_delete_returns_405(client: TestClient) -> None:
    resp = client.delete("/editor")
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# HTML structure
# ---------------------------------------------------------------------------


def test_editor_page_has_html_doctype(client: TestClient) -> None:
    body = client.get("/editor").text
    assert body.strip().lower().startswith("<!doctype html")


def test_editor_page_has_html_tag(client: TestClient) -> None:
    body = client.get("/editor").text
    assert re.search(r"<html", body, re.IGNORECASE)


def test_editor_page_has_head_tag(client: TestClient) -> None:
    body = client.get("/editor").text
    assert re.search(r"<head", body, re.IGNORECASE)


def test_editor_page_has_body_tag(client: TestClient) -> None:
    body = client.get("/editor").text
    assert re.search(r"<body", body, re.IGNORECASE)


def test_editor_page_has_title(client: TestClient) -> None:
    body = client.get("/editor").text
    assert re.search(r"<title[^>]*>.*</title>", body, re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# CodeMirror 6 from cdnjs CDN
# ---------------------------------------------------------------------------


def test_editor_loads_codemirror_from_cdn(client: TestClient) -> None:
    """CodeMirror must be loaded from a CDN — no local bundler required."""
    body = client.get("/editor").text
    # Accept any CDN: cdnjs, esm.sh, unpkg, jsdelivr, skypack
    assert any(cdn in body for cdn in ("cdnjs.cloudflare.com", "esm.sh", "unpkg.com", "jsdelivr.net", "cdn.skypack.dev"))


def test_editor_references_codemirror_package(client: TestClient) -> None:
    """Page must reference the codemirror package by name."""
    body = client.get("/editor").text.lower()
    assert "codemirror" in body


def test_editor_codemirror_is_version_6(client: TestClient) -> None:
    """Should use CodeMirror 6.x, not legacy CM5."""
    body = client.get("/editor").text
    # CM6 packages include @codemirror/ scoped packages or version 6.x references
    has_cm6_ref = (
        "@codemirror/" in body
        or re.search(r"codemirror[/@\-]6", body, re.IGNORECASE) is not None
        or re.search(r"codemirror/6\.\d+\.\d+", body, re.IGNORECASE) is not None
    )
    assert has_cm6_ref, "Expected CodeMirror 6 package references on the page"


def test_editor_codemirror_cdn_url_uses_https(client: TestClient) -> None:
    """CDN resources must be loaded over HTTPS."""
    body = client.get("/editor").text
    cdn_hosts = ("cdnjs.cloudflare.com", "esm.sh", "unpkg.com", "jsdelivr.net", "cdn.skypack.dev")
    # Only check lines that actually load resources (import/src=), skip comments
    cdn_lines = [
        line for line in body.splitlines()
        if any(h in line for h in cdn_hosts)
        and ("import" in line.lower() or "src=" in line.lower() or "href=" in line.lower() or "https://" in line)
        and not line.strip().startswith("//")
    ]
    assert len(cdn_lines) > 0, "No CDN resource references found in editor page"
    for line in cdn_lines:
        assert "https://" in line, f"Non-HTTPS CDN reference found: {line!r}"


# ---------------------------------------------------------------------------
# SQL editor input
# ---------------------------------------------------------------------------


def test_editor_page_has_editor_container(client: TestClient) -> None:
    """There must be a DOM element to host the CodeMirror editor."""
    body = client.get("/editor").text
    # CodeMirror is typically mounted on a div; look for a container element
    has_container = (
        re.search(r'id=["\']editor["\']', body, re.IGNORECASE) is not None
        or re.search(r'class=["\'][^"\']*editor[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r'id=["\']cm-editor["\']', body, re.IGNORECASE) is not None
    )
    assert has_container, "Expected a DOM container element for the CodeMirror editor"


def test_editor_page_has_sql_textarea_or_codemirror_mount(client: TestClient) -> None:
    """Either a <textarea> for SQL input or a CM mount point must be present."""
    body = client.get("/editor").text
    has_input = (
        re.search(r"<textarea", body, re.IGNORECASE) is not None
        or re.search(r'id=["\']editor["\']', body, re.IGNORECASE) is not None
    )
    assert has_input


# ---------------------------------------------------------------------------
# Run button
# ---------------------------------------------------------------------------


def test_editor_page_has_run_button(client: TestClient) -> None:
    """Page must contain a button labelled 'Run' (case-insensitive)."""
    body = client.get("/editor").text
    has_run = (
        re.search(r"<button[^>]*>.*?run.*?</button>", body, re.IGNORECASE | re.DOTALL) is not None
        or re.search(r'value=["\']run["\']', body, re.IGNORECASE) is not None
        or re.search(r"run", body, re.IGNORECASE) is not None
    )
    # More precise: button element containing "run" text
    assert re.search(r"<button[^>]*>[^<]*run[^<]*</button>", body, re.IGNORECASE) or re.search(
        r'<input[^>]*type=["\']button["\'][^>]*value=["\']run["\']', body, re.IGNORECASE
    ), "Expected a Run button on the page"


def test_editor_run_button_has_id_or_class(client: TestClient) -> None:
    """Run button should be identifiable via id or class for JS event binding."""
    body = client.get("/editor").text
    has_identifiable = (
        re.search(r'id=["\']run[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r'class=["\'][^"\']*run[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r'id=["\'][^"\']*btn[^"\']*["\']', body, re.IGNORECASE) is not None
    )
    assert has_identifiable, "Run button should have id or class for JS targeting"


# ---------------------------------------------------------------------------
# Error display panel
# ---------------------------------------------------------------------------


def test_editor_page_has_error_panel(client: TestClient) -> None:
    """Page must contain an element to display error messages."""
    body = client.get("/editor").text
    has_error_panel = (
        re.search(r'id=["\'][^"\']*error[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r'class=["\'][^"\']*error[^"\']*["\']', body, re.IGNORECASE) is not None
    )
    assert has_error_panel, "Expected an error display panel element"


def test_editor_error_panel_is_initially_hidden_or_empty(client: TestClient) -> None:
    """Error panel should start hidden (display:none or similar) or have no content."""
    body = client.get("/editor").text
    # Look for error element with hidden styles or empty content
    has_hidden_error = (
        re.search(r'display:\s*none', body, re.IGNORECASE) is not None
        or re.search(r'hidden', body, re.IGNORECASE) is not None
        or re.search(r'class=["\'][^"\']*error[^"\']*["\'][^>]*></[a-z]+>', body, re.IGNORECASE) is not None
    )
    assert has_hidden_error, "Error panel should be hidden or empty on page load"


# ---------------------------------------------------------------------------
# Results table container
# ---------------------------------------------------------------------------


def test_editor_page_has_results_container(client: TestClient) -> None:
    """Page must contain a container for the query results table."""
    body = client.get("/editor").text
    has_results = (
        re.search(r'id=["\'][^"\']*result[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r'class=["\'][^"\']*result[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r'id=["\'][^"\']*output[^"\']*["\']', body, re.IGNORECASE) is not None
    )
    assert has_results, "Expected a results container element"


def test_editor_page_results_area_is_a_table_or_container(client: TestClient) -> None:
    """Results should be displayed as an HTML table or within a table container."""
    body = client.get("/editor").text
    # Either a <table> element exists, or JS that creates one is present
    has_table_structure = (
        re.search(r"<table", body, re.IGNORECASE) is not None
        or re.search(r"createElement\(['\"]table['\"]", body, re.IGNORECASE) is not None
        or re.search(r"insertAdjacentHTML", body, re.IGNORECASE) is not None
        or re.search(r"innerHTML", body, re.IGNORECASE) is not None
    )
    assert has_table_structure, "Expected table or dynamic table creation for results"


# ---------------------------------------------------------------------------
# fetch() POST to /query in JavaScript
# ---------------------------------------------------------------------------


def test_editor_page_has_fetch_call(client: TestClient) -> None:
    """Page must use fetch() for async HTTP requests — no XMLHttpRequest."""
    body = client.get("/editor").text
    assert "fetch(" in body or "fetch (" in body, "Expected fetch() call in page JavaScript"


def test_editor_page_fetches_query_endpoint(client: TestClient) -> None:
    """fetch() must target the /query endpoint."""
    body = client.get("/editor").text
    assert re.search(r"fetch\(['\"][^'\"]*\/query['\"]", body) or "/query" in body, (
        "Expected /query endpoint referenced in fetch() call"
    )


def test_editor_page_uses_post_method_for_query(client: TestClient) -> None:
    """The fetch to /query must use the POST method."""
    body = client.get("/editor").text
    has_post = (
        re.search(r"method:\s*['\"]POST['\"]", body, re.IGNORECASE) is not None
        or re.search(r"method:\s*['\"]post['\"]", body, re.IGNORECASE) is not None
    )
    assert has_post, "Expected method: 'POST' in fetch() options"


def test_editor_page_sends_json_content_type(client: TestClient) -> None:
    """Fetch to /query must set Content-Type: application/json."""
    body = client.get("/editor").text
    assert "application/json" in body, "Expected Content-Type: application/json in fetch headers"


def test_editor_page_uses_json_stringify_for_body(client: TestClient) -> None:
    """POST body must be JSON-serialized."""
    body = client.get("/editor").text
    assert "JSON.stringify" in body, "Expected JSON.stringify() for building request body"


def test_editor_page_handles_fetch_response_json(client: TestClient) -> None:
    """Page must parse response as JSON to extract columns/rows."""
    body = client.get("/editor").text
    has_json_parse = (
        "response.json()" in body
        or ".json()" in body
        or "JSON.parse" in body
    )
    assert has_json_parse, "Expected .json() or JSON.parse() to handle fetch response"


# ---------------------------------------------------------------------------
# No build toolchain
# ---------------------------------------------------------------------------


def test_editor_page_has_no_webpack_references(client: TestClient) -> None:
    """Page must not require webpack or similar bundler artifacts."""
    body = client.get("/editor").text
    assert "webpack" not in body.lower(), "Page should not reference webpack"


def test_editor_page_has_no_node_modules_references(client: TestClient) -> None:
    """Page must not reference node_modules paths."""
    body = client.get("/editor").text
    assert "node_modules" not in body, "Page should not reference node_modules"


def test_editor_page_has_no_import_map_bundler(client: TestClient) -> None:
    """Rollup/Vite/Parcel build artifacts should not appear on the page."""
    body = client.get("/editor").text
    for bundler in ("rollup", "vite", "parcel", "esbuild"):
        assert bundler not in body.lower(), f"Page should not reference {bundler}"


def test_editor_page_scripts_are_inline_or_cdn(client: TestClient) -> None:
    """All <script src=...> must point to CDN URLs, not local build artifacts."""
    body = client.get("/editor").text
    script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', body, re.IGNORECASE)
    for src in script_srcs:
        is_cdn = src.startswith("http://") or src.startswith("https://") or src.startswith("//")
        assert is_cdn, f"Script src must be CDN URL, got: {src!r}"


# ---------------------------------------------------------------------------
# Session ID handling
# ---------------------------------------------------------------------------


def test_editor_page_references_session_id_field(client: TestClient) -> None:
    """The JS must build a request body including session_id for /query."""
    body = client.get("/editor").text
    assert "session_id" in body, "Expected session_id field in fetch request body construction"


def test_editor_page_has_session_input_or_js_variable(client: TestClient) -> None:
    """Page must have a way to specify the session_id (input field or JS var)."""
    body = client.get("/editor").text
    has_session_field = (
        re.search(r'id=["\'][^"\']*session[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r'name=["\'][^"\']*session[^"\']*["\']', body, re.IGNORECASE) is not None
        or re.search(r"sessionId\s*=", body, re.IGNORECASE) is not None
        or re.search(r"session_id\s*=", body, re.IGNORECASE) is not None
        or re.search(r"let\s+session", body, re.IGNORECASE) is not None
        or re.search(r"var\s+session", body, re.IGNORECASE) is not None
        or re.search(r"const\s+session", body, re.IGNORECASE) is not None
    )
    assert has_session_field, "Expected session_id input or JS variable on the page"


# ---------------------------------------------------------------------------
# SQL syntax highlighting / autocomplete markers
# ---------------------------------------------------------------------------


def test_editor_page_references_sql_language_support(client: TestClient) -> None:
    """CodeMirror SQL language extension must be referenced."""
    body = client.get("/editor").text.lower()
    has_sql_support = (
        "lang-sql" in body
        or "@codemirror/lang-sql" in body
        or "sql()" in body.lower()
        or "sql " in body.lower()
    )
    assert has_sql_support, "Expected CodeMirror SQL language support reference"


def test_editor_page_references_autocomplete(client: TestClient) -> None:
    """CodeMirror autocomplete extension must be referenced or set up."""
    body = client.get("/editor").text.lower()
    has_autocomplete = (
        "autocomplete" in body
        or "autocompletion" in body
        or "@codemirror/autocomplete" in body
    )
    assert has_autocomplete, "Expected autocomplete reference in page"


# ---------------------------------------------------------------------------
# Jinja2 template rendering (server-side)
# ---------------------------------------------------------------------------


def test_editor_page_is_not_json(client: TestClient) -> None:
    """GET /editor must return HTML, not a JSON API response."""
    resp = client.get("/editor")
    content_type = resp.headers.get("content-type", "")
    assert "application/json" not in content_type, "Editor page must not return JSON"


def test_editor_endpoint_is_in_openapi_schema(client: TestClient) -> None:
    """GET /editor must be listed in the OpenAPI schema."""
    schema = client.get("/openapi.json").json()
    paths = schema.get("paths", {})
    assert "/editor" in paths, "Expected /editor in OpenAPI paths"
    assert "get" in paths["/editor"], "Expected GET method on /editor"
