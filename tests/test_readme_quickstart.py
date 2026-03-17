"""Tests defining the expected state of the PondDB README quickstart.

These tests read README.md and verify it contains all required sections,
content, and examples. They also verify that code examples in the README
are importable and functional.
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
# Badge
# ---------------------------------------------------------------------------


def test_readme_has_ci_badge(readme: str) -> None:
    """README must include a CI badge (markdown image with badge URL)."""
    assert re.search(r"!\[CI\]", readme), "README must have a CI badge: ![CI]"


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


def test_readme_has_description_paragraph(readme: str) -> None:
    """README must have a one-paragraph description of PondDB."""
    # Must mention DuckDB and self-hosted (or serverless) in the description
    assert "DuckDB" in readme, "README description must mention DuckDB"
    assert re.search(r"self.hosted", readme, re.IGNORECASE), (
        "README description must mention self-hosted"
    )


# ---------------------------------------------------------------------------
# Quickstart — Docker
# ---------------------------------------------------------------------------


def test_readme_has_docker_quickstart(readme: str) -> None:
    """README must have a Docker quickstart section."""
    assert re.search(r"##.*[Qq]uickstart", readme), "README must have a ## Quickstart section"
    assert "docker" in readme.lower(), "README must mention docker"
    assert "docker compose up" in readme.lower() or "docker-compose up" in readme.lower(), (
        "README must show `docker compose up` command"
    )


def test_readme_docker_quickstart_shows_health_check(readme: str) -> None:
    """Docker quickstart must show how to verify the server is running."""
    assert "localhost:8432/health" in readme or "8432/health" in readme, (
        "README quickstart must show health-check URL"
    )


def test_readme_docker_quickstart_shows_env_var_for_secret(readme: str) -> None:
    """Docker quickstart must mention setting POND_JWT_SECRET."""
    assert "POND_JWT_SECRET" in readme, (
        "README must mention POND_JWT_SECRET in the quickstart or configuration"
    )


# ---------------------------------------------------------------------------
# Quickstart — pip install
# ---------------------------------------------------------------------------


def test_readme_has_pip_install_quickstart(readme: str) -> None:
    """README must show how to install via pip."""
    assert "pip install ponddb" in readme or 'pip install "ponddb"' in readme, (
        "README must show `pip install ponddb`"
    )


def test_readme_pip_quickstart_shows_start_command(readme: str) -> None:
    """pip install quickstart must show how to start the server."""
    assert "uvicorn" in readme, "README must show uvicorn start command for pip install path"
    assert "8432" in readme, "README must reference port 8432 in start command"


# ---------------------------------------------------------------------------
# API reference table — presence
# ---------------------------------------------------------------------------


def test_readme_has_api_reference_section(readme: str) -> None:
    """README must have an API reference section."""
    assert re.search(r"##.*[Aa][Pp][Ii]", readme), "README must have an ## API section"


def test_readme_api_table_has_method_column(readme: str) -> None:
    """API reference table must include a Method column."""
    assert re.search(r"\|\s*Method\s*\|", readme, re.IGNORECASE), (
        "API table must have a Method column"
    )


def test_readme_api_table_has_auth_column(readme: str) -> None:
    """API reference table must include an Auth column."""
    assert re.search(r"\|\s*Auth\s*\|", readme, re.IGNORECASE), (
        "API table must have an Auth column indicating which endpoints require authentication"
    )


def test_readme_api_table_has_description_column(readme: str) -> None:
    """API reference table must include a Description column."""
    assert re.search(r"\|\s*[Dd]escription\s*\|", readme), (
        "API table must have a Description column"
    )


# ---------------------------------------------------------------------------
# API reference table — all endpoints present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", [
    "/health",
    "/session",
    "/sessions",
    "/query",
    "/catalog/mount",
    "/metrics",
    "/auth/token",
    "/auth/refresh",
    "/history",
    "/schema",
    "/editor",
    "/datasets",
])
def test_readme_api_table_has_endpoint(readme: str, endpoint: str) -> None:
    """Every API endpoint must appear in the API reference table."""
    assert endpoint in readme, f"README API table must document the {endpoint!r} endpoint"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_readme_api_table_has_http_method(readme: str, method: str) -> None:
    """API table must list all HTTP methods used by the API."""
    assert method in readme, f"README API table must include HTTP method {method!r}"


# ---------------------------------------------------------------------------
# Architecture diagram (ASCII)
# ---------------------------------------------------------------------------


def test_readme_has_architecture_section(readme: str) -> None:
    """README must have an architecture section."""
    assert re.search(r"##.*[Aa]rchitect", readme), (
        "README must have an ## Architecture section"
    )


def test_readme_has_ascii_architecture_diagram(readme: str) -> None:
    """README must contain an ASCII architecture diagram in a code block."""
    # An ASCII diagram should be in a code block and contain box-drawing or arrow characters
    code_blocks = re.findall(r"```(?:.*?)\n(.*?)```", readme, re.DOTALL)
    has_ascii_diagram = False
    for block in code_blocks:
        # Look for ASCII art patterns: boxes (+-|), arrows (-->), or pipes
        if re.search(r"[-─]{3,}|[+┌┐└┘│├┤┬┴┼]|-->|→|<--|←|\+--", block):
            has_ascii_diagram = True
            break
    assert has_ascii_diagram, (
        "README must contain an ASCII architecture diagram inside a code block "
        "(using box-drawing chars or ASCII art arrows)"
    )


def test_readme_architecture_diagram_shows_key_components(readme: str) -> None:
    """Architecture diagram must reference the key system components."""
    # Each of these should appear somewhere in the architecture section
    arch_section = re.split(r"##\s+", readme)
    arch_text = ""
    for section in arch_section:
        if re.match(r"[Aa]rchitect", section):
            arch_text = section
            break

    assert arch_text, "Could not find Architecture section text"

    # Must mention at least FastAPI/HTTP and DuckDB
    assert re.search(r"FastAPI|HTTP|API", arch_text, re.IGNORECASE), (
        "Architecture diagram must reference FastAPI or HTTP layer"
    )
    assert re.search(r"DuckDB", arch_text, re.IGNORECASE), (
        "Architecture diagram must reference DuckDB"
    )


# ---------------------------------------------------------------------------
# Session lifecycle diagram
# ---------------------------------------------------------------------------


def test_readme_documents_session_lifecycle(readme: str) -> None:
    """README must document the session lifecycle states."""
    assert "COLD" in readme, "README must document the COLD session state"
    assert "ACTIVE" in readme, "README must document the ACTIVE session state"
    assert "SUSPENDED" in readme, "README must document the SUSPENDED session state"
    assert "DESTROYED" in readme, "README must document the DESTROYED session state"


# ---------------------------------------------------------------------------
# Configuration — all env vars
# ---------------------------------------------------------------------------


def test_readme_has_configuration_section(readme: str) -> None:
    """README must have a Configuration section."""
    assert re.search(r"##.*[Cc]onfig", readme), "README must have a ## Configuration section"


@pytest.mark.parametrize("env_var", [
    "POND_HOST",
    "POND_PORT",
    "POND_JWT_SECRET",
    "POND_IDLE_TIMEOUT",
    "POND_MAX_SESSION_AGE",
    "POND_DATA_ROOT",
    "POND_MAX_RESULT_MB",
    "POND_SESSION_MEMORY_LIMIT",
    "POND_LOG_LEVEL",
    "POND_SQLITE_PATH",
])
def test_readme_documents_env_var(readme: str, env_var: str) -> None:
    """Every configuration env var must be documented in the README."""
    assert env_var in readme, f"README must document environment variable {env_var!r}"


def test_readme_config_table_shows_defaults(readme: str) -> None:
    """Configuration table must include a Default column."""
    assert re.search(r"\|\s*[Dd]efault\s*\|", readme), (
        "Configuration table must have a Default column"
    )


# ---------------------------------------------------------------------------
# Python SDK usage example
# ---------------------------------------------------------------------------


def test_readme_has_sdk_section(readme: str) -> None:
    """README must have a Python SDK or Library section."""
    assert re.search(r"##.*(?:[Ss][Dd][Kk]|[Ll]ibrary|[Pp]ython)", readme), (
        "README must have a section for Python SDK or library usage"
    )


def test_readme_sdk_example_shows_import(readme: str) -> None:
    """README Python SDK example must show an import statement."""
    assert re.search(r"from ponddb import|import ponddb", readme), (
        "README SDK example must show how to import ponddb"
    )


def test_readme_sdk_example_shows_query(readme: str) -> None:
    """README Python SDK example must show how to run a query."""
    assert re.search(r"\.query\(", readme), (
        "README SDK example must show calling .query() to execute SQL"
    )


def test_readme_sdk_example_shows_authentication(readme: str) -> None:
    """README Python SDK example must show authentication setup."""
    # Should show api_key or token usage in the SDK example
    assert re.search(r"api_key|token|auth", readme, re.IGNORECASE), (
        "README SDK example must show how to authenticate"
    )


def test_readme_sdk_example_shows_session_lifecycle(readme: str) -> None:
    """README Python SDK example must demonstrate session or context manager usage."""
    assert re.search(r"session|with |connect", readme, re.IGNORECASE), (
        "README SDK example should show session lifecycle"
    )


# ---------------------------------------------------------------------------
# Python SDK importability
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
    """ponddb package must export a client class (PondDB or DuckCloudClient)."""
    import ponddb
    # README says: from ponddb import PondDB
    assert hasattr(ponddb, "PondDB"), (
        "ponddb must export PondDB class for use in: from ponddb import PondDB"
    )


def test_ponddb_client_can_be_instantiated() -> None:
    """PondDB client class must be instantiable (basic import smoke test)."""
    from ponddb import PondDB  # type: ignore[attr-defined]
    client = PondDB()
    assert client is not None


# ---------------------------------------------------------------------------
# Development setup
# ---------------------------------------------------------------------------


def test_readme_has_development_section(readme: str) -> None:
    """README must have a Development or Contributing section."""
    assert re.search(r"##.*(?:[Dd]evelop|[Cc]ontribut)", readme), (
        "README must have a ## Development or ## Contributing section"
    )


def test_readme_dev_setup_shows_clone(readme: str) -> None:
    """Development setup must show git clone step."""
    assert "git clone" in readme, (
        "README development setup must show: git clone <repo-url>"
    )


def test_readme_dev_setup_shows_venv(readme: str) -> None:
    """Development setup must show virtual environment creation."""
    assert re.search(r"venv|virtualenv|\.venv", readme), (
        "README development setup must show creating a virtual environment"
    )


def test_readme_dev_setup_shows_install(readme: str) -> None:
    """Development setup must show pip install of dev dependencies."""
    assert re.search(r'pip install.*\[dev\]|pip install.*-e', readme), (
        "README development setup must show pip install with dev extras"
    )


def test_readme_dev_setup_shows_pytest(readme: str) -> None:
    """Development setup must show how to run tests with pytest."""
    assert "pytest" in readme, "README development setup must mention pytest"


def test_readme_dev_setup_shows_linter(readme: str) -> None:
    """Development setup must show how to run the linter."""
    assert "ruff" in readme, "README development setup must mention ruff"


# ---------------------------------------------------------------------------
# License
# ---------------------------------------------------------------------------


def test_readme_has_license_section(readme: str) -> None:
    """README must have a License section."""
    assert re.search(r"##.*[Ll]icense", readme), "README must have a ## License section"


def test_readme_shows_license(readme: str) -> None:
    """README must state the project license (BSL 1.1 or Apache 2.0)."""
    assert "BSL" in readme or "Business Source License" in readme or "Apache" in readme, (
        "README must state the project license"
    )
