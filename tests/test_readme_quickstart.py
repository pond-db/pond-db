"""Tests defining the expected state of the PondDB README.

These tests read README.md and verify it contains all required sections
and content for the agent memory database positioning.
"""

import re
from pathlib import Path

import pytest

README_PATH = Path(__file__).parent.parent / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    """Read the README content once for the entire module."""
    assert README_PATH.exists(), f"README.md not found at {README_PATH}"
    return README_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Core positioning
# ---------------------------------------------------------------------------


def test_readme_has_description(readme: str) -> None:
    """README must describe PondDB as a memory database for AI agents."""
    assert "DuckDB" in readme, "README must mention DuckDB"
    assert re.search(r"self.hosted", readme, re.IGNORECASE), "README must mention self-hosted"
    assert re.search(r"agent|memory", readme, re.IGNORECASE), "README must mention agent or memory"


# ---------------------------------------------------------------------------
# Quickstart
# ---------------------------------------------------------------------------


def test_readme_has_docker_quickstart(readme: str) -> None:
    """README must have a Docker quickstart section."""
    assert re.search(r"##.*[Qq]uickstart", readme), "README must have a ## Quickstart section"
    assert "docker compose up" in readme.lower() or "docker-compose up" in readme.lower(), (
        "README must show `docker compose up` command"
    )


def test_readme_quickstart_shows_clone(readme: str) -> None:
    """Quickstart must show git clone."""
    assert "git clone" in readme, "README quickstart must show git clone"


# ---------------------------------------------------------------------------
# Agent Memory section
# ---------------------------------------------------------------------------


def test_readme_has_agent_memory_section(readme: str) -> None:
    """README must have an Agent Memory section."""
    assert re.search(r"##.*[Aa]gent [Mm]emory", readme), "README must have an Agent Memory section"


def test_readme_shows_memory_store_example(readme: str) -> None:
    """README must show how to store a memory."""
    assert "/memories" in readme, "README must show /memories endpoint"
    assert "memory_type" in readme, "README must show memory_type field"


def test_readme_shows_memory_search_example(readme: str) -> None:
    """README must show how to search memories."""
    assert "memories/search" in readme, "README must show memories/search endpoint"


def test_readme_shows_feedback_example(readme: str) -> None:
    """README must show the feedback/utility scoring flow."""
    assert "feedback" in readme, "README must show feedback endpoint"
    assert "reward" in readme, "README must show reward parameter"


def test_readme_shows_sql_debug_query(readme: str) -> None:
    """README must show the SQL debugging query."""
    assert "memory_access_log" in readme, "README must show memory_access_log table"


# ---------------------------------------------------------------------------
# Memory Types
# ---------------------------------------------------------------------------


def test_readme_documents_memory_types(readme: str) -> None:
    """README must document all 5 memory types."""
    for mt in ["working", "episodic", "semantic", "procedural", "shared"]:
        assert mt in readme, f"README must document memory type: {mt}"


# ---------------------------------------------------------------------------
# Isolation and Grants
# ---------------------------------------------------------------------------


def test_readme_documents_isolation(readme: str) -> None:
    """README must explain workgroup isolation."""
    assert re.search(r"[Ii]solation", readme), "README must discuss isolation"
    assert "0 leaks" in readme or "0 cross-workgroup" in readme, (
        "README must state isolation test results"
    )


def test_readme_documents_grants(readme: str) -> None:
    """README must explain cross-workgroup grants."""
    assert "memory-grants" in readme or "memory_grants" in readme, "README must document grants API"


# ---------------------------------------------------------------------------
# MCP / Claude Code
# ---------------------------------------------------------------------------


def test_readme_has_mcp_section(readme: str) -> None:
    """README must have an MCP / Claude Code section."""
    assert "mcp-server-ponddb" in readme, "README must mention mcp-server-ponddb package"
    assert "Claude Code" in readme or "claude" in readme.lower(), "README must mention Claude Code"


# ---------------------------------------------------------------------------
# Multi-agent demo
# ---------------------------------------------------------------------------


def test_readme_has_multi_agent_demo(readme: str) -> None:
    """README must have a multi-agent demo section."""
    assert re.search(r"##.*[Mm]ulti.*[Aa]gent", readme), (
        "README must have a Multi-Agent Demo section"
    )


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------


def test_readme_has_comparison_table(readme: str) -> None:
    """README must compare PondDB to competitors."""
    assert "Mem0" in readme, "README must compare against Mem0"
    assert "Zep" in readme, "README must compare against Zep"


# ---------------------------------------------------------------------------
# API reference
# ---------------------------------------------------------------------------


def test_readme_has_api_reference_section(readme: str) -> None:
    """README must have an API reference section."""
    assert re.search(r"##.*[Aa][Pp][Ii]", readme), "README must have an ## API section"


def test_readme_api_table_has_method_column(readme: str) -> None:
    """API reference table must include a Method column."""
    assert re.search(r"\|\s*Method\s*\|", readme, re.IGNORECASE), (
        "API table must have a Method column"
    )


def test_readme_api_table_has_description_column(readme: str) -> None:
    """API reference table must include a Description column."""
    assert re.search(r"\|\s*[Dd]escription\s*\|", readme), (
        "API table must have a Description column"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "/memories",
        "/memories/search",
        "/memories/{id}",
        "/memories/{id}/feedback",
        "/memory-grants",
        "/pondapi/execute",
    ],
)
def test_readme_api_table_has_endpoint(readme: str, endpoint: str) -> None:
    """Key API endpoints must appear in the API reference table."""
    assert endpoint in readme, f"README API table must document the {endpoint!r} endpoint"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "PUT"])
def test_readme_api_table_has_http_method(readme: str, method: str) -> None:
    """API table must list all HTTP methods used by the API."""
    assert method in readme, f"README API table must include HTTP method {method!r}"


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------


def test_readme_has_architecture_section(readme: str) -> None:
    """README must have an architecture section."""
    assert re.search(r"##.*[Aa]rchitect", readme), "README must have an ## Architecture section"


def test_readme_architecture_mentions_duckdb(readme: str) -> None:
    """Architecture section must reference DuckDB."""
    assert "DuckDB" in readme, "Architecture must reference DuckDB"


def test_readme_architecture_mentions_sqlite(readme: str) -> None:
    """Architecture section must reference SQLite."""
    assert "SQLite" in readme, "Architecture must reference SQLite"


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def test_readme_has_benchmarks_section(readme: str) -> None:
    """README must have a benchmarks section with real numbers."""
    assert re.search(r"##.*[Bb]enchmark", readme), "README must have a ## Benchmarks section"
    assert re.search(r"\d+(\.\d+)?ms", readme), "Benchmarks must contain actual latency numbers"


# ---------------------------------------------------------------------------
# SDK importability
# ---------------------------------------------------------------------------


def test_ponddb_package_is_importable() -> None:
    """The ponddb package must be importable."""
    import ponddb  # noqa: F401


def test_ponddb_has_version() -> None:
    """ponddb package must expose __version__."""
    import ponddb

    assert hasattr(ponddb, "__version__"), "ponddb must expose __version__"
    assert isinstance(ponddb.__version__, str)
    assert ponddb.__version__, "ponddb.__version__ must not be empty"


def test_ponddb_exports_client_class() -> None:
    """ponddb package must export a client class (PondDB or PondClient)."""
    import ponddb

    assert hasattr(ponddb, "PondDB"), (
        "ponddb must export PondDB class for use in: from ponddb import PondDB"
    )


def test_ponddb_client_can_be_instantiated() -> None:
    """PondDB client class must be instantiable (basic import smoke test)."""
    from ponddb import PondDB  # type: ignore[attr-defined]

    client = PondDB()
    assert client is not None


# ---------------------------------------------------------------------------
# Contributing / License
# ---------------------------------------------------------------------------


def test_readme_has_contributing_section(readme: str) -> None:
    """README must have a Contributing section."""
    assert re.search(r"##.*[Cc]ontribut", readme), "README must have a ## Contributing section"


def test_readme_has_license_section(readme: str) -> None:
    """README must have a License section."""
    assert re.search(r"##.*[Ll]icense", readme), "README must have a ## License section"


def test_readme_shows_license(readme: str) -> None:
    """README must state the project license."""
    assert "BSL" in readme or "Business Source License" in readme, (
        "README must state the BSL license"
    )
