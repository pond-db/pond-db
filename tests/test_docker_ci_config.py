"""Tests for CI configuration and production Docker finishing touches.

These tests define the DESIRED state before implementation.
They should FAIL until the CI workflow and remaining production
hardening items are added.

Covers:
- .github/workflows/ci.yml exists and runs tests
- .env.example documents all required secrets and env vars
- Dockerfile sets PYTHONUNBUFFERED and PYTHONDONTWRITEBYTECODE
- Dockerfile has a HEALTHCHECK instruction (standalone, without compose)
- Dockerfile has OCI image labels (org.opencontainers.image.*)
- docker-compose sets POND_LOG_LEVEL env var
- docker-compose has a container_name for deterministic naming
- docker-compose has a logging driver config
"""

import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    assert DOCKERFILE.exists(), "Dockerfile not found"
    return DOCKERFILE.read_text()


@pytest.fixture(scope="module")
def dockerfile_lines(dockerfile_text: str) -> list[str]:
    return dockerfile_text.splitlines()


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.exists(), "docker-compose.yml not found"
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def ponddb_service(compose: dict) -> dict:
    return compose["services"]["ponddb"]


@pytest.fixture(scope="module")
def ci_workflow() -> dict:
    assert CI_WORKFLOW.exists(), (
        f"CI workflow not found at {CI_WORKFLOW} — "
        "create .github/workflows/ci.yml to enable automated testing on push/PR"
    )
    with CI_WORKFLOW.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def env_example_text() -> str:
    assert ENV_EXAMPLE.exists(), (
        ".env.example not found at repo root — "
        "create this file to document required environment variables for operators"
    )
    return ENV_EXAMPLE.read_text()


# ---------------------------------------------------------------------------
# CI workflow
# ---------------------------------------------------------------------------


def test_ci_workflow_file_exists() -> None:
    """CI workflow must exist to run tests on every push and PR."""
    assert CI_WORKFLOW.exists(), (
        f"Missing {CI_WORKFLOW} — add a GitHub Actions workflow that runs pytest "
        "on push and pull_request events."
    )


def test_ci_workflow_triggers_on_push(ci_workflow: dict) -> None:
    """CI must run on push events."""
    on = ci_workflow.get("on", ci_workflow.get(True, {}))
    triggers = set(on.keys()) if isinstance(on, dict) else set()
    assert "push" in triggers, (
        f"CI workflow must trigger on 'push' events, got triggers: {triggers}"
    )


def test_ci_workflow_triggers_on_pull_request(ci_workflow: dict) -> None:
    """CI must run on pull_request events."""
    on = ci_workflow.get("on", ci_workflow.get(True, {}))
    triggers = set(on.keys()) if isinstance(on, dict) else set()
    assert "pull_request" in triggers, (
        f"CI workflow must trigger on 'pull_request' events, got triggers: {triggers}"
    )


def test_ci_workflow_has_test_job(ci_workflow: dict) -> None:
    """CI must have a job that runs pytest."""
    jobs = ci_workflow.get("jobs", {})
    assert len(jobs) >= 1, "CI workflow must have at least one job"
    # Find any job that runs pytest
    workflow_text = CI_WORKFLOW.read_text()
    assert "pytest" in workflow_text, (
        "CI workflow must run pytest — add a step like: run: pytest tests/"
    )


def test_ci_workflow_uses_python_312(ci_workflow: dict) -> None:
    """CI must test against Python 3.12 to match the Docker base image."""
    workflow_text = CI_WORKFLOW.read_text()
    assert "3.12" in workflow_text, (
        "CI workflow must specify Python 3.12 to match the Dockerfile base image"
    )


def test_ci_workflow_installs_dependencies(ci_workflow: dict) -> None:
    """CI must install project dependencies before running tests."""
    workflow_text = CI_WORKFLOW.read_text()
    assert "pip install" in workflow_text or "uv" in workflow_text, (
        "CI workflow must install project dependencies (e.g. pip install -e '.[dev]')"
    )


# ---------------------------------------------------------------------------
# .env.example — operator documentation
# ---------------------------------------------------------------------------


def test_env_example_exists() -> None:
    """Operators need .env.example to know which variables to configure."""
    assert ENV_EXAMPLE.exists(), (
        ".env.example missing — create it to document all required and optional "
        "environment variables (copy .env.example → .env and fill in values)"
    )


def test_env_example_documents_jwt_secret(env_example_text: str) -> None:
    """POND_JWT_SECRET is required for auth — must appear in .env.example."""
    assert "POND_JWT_SECRET" in env_example_text, (
        ".env.example must document POND_JWT_SECRET — it is required for JWT auth "
        "and the server will not start without it"
    )


def test_env_example_documents_pond_port(env_example_text: str) -> None:
    """POND_PORT is a common override — must appear in .env.example."""
    assert "POND_PORT" in env_example_text, (
        ".env.example must document POND_PORT so operators can change the listen port"
    )


def test_env_example_documents_pond_data_root(env_example_text: str) -> None:
    """POND_DATA_ROOT controls where DuckDB session files are stored."""
    assert "POND_DATA_ROOT" in env_example_text, (
        ".env.example must document POND_DATA_ROOT — controls session file persistence"
    )


def test_env_example_documents_pond_sqlite_path(env_example_text: str) -> None:
    """POND_SQLITE_PATH must be documented — wrong value causes metadata loss on restart."""
    assert "POND_SQLITE_PATH" in env_example_text, (
        ".env.example must document POND_SQLITE_PATH so operators point it at the volume"
    )


def test_env_example_has_no_real_secrets(env_example_text: str) -> None:
    """Example values must be placeholders, not real secrets."""
    # A real JWT secret would be a long random string; catch obvious non-placeholder patterns
    lines = env_example_text.splitlines()
    for line in lines:
        if "POND_JWT_SECRET" in line and "=" in line:
            value = line.split("=", 1)[1].strip()
            # Reject anything that looks like a real 32+ char random string that isn't a placeholder
            assert len(value) < 32 or value.startswith("<") or value.startswith("your-"), (
                f"POND_JWT_SECRET in .env.example must be a placeholder (e.g. 'your-secret-here'), "
                f"not a real secret: {line!r}"
            )


# ---------------------------------------------------------------------------
# Dockerfile — Python runtime env vars
# ---------------------------------------------------------------------------


def test_dockerfile_sets_pythonunbuffered(dockerfile_text: str) -> None:
    """PYTHONUNBUFFERED=1 ensures stdout/stderr are flushed immediately.

    Without this, container logs appear with a delay because Python's stdout
    is line-buffered by default. Docker log drivers depend on real-time output.
    """
    assert "PYTHONUNBUFFERED" in dockerfile_text, (
        "Dockerfile must set ENV PYTHONUNBUFFERED=1 so Python logs appear "
        "in docker logs without buffering delay"
    )


def test_dockerfile_sets_pythondontwritebytecode(dockerfile_text: str) -> None:
    """PYTHONDONTWRITEBYTECODE=1 prevents .pyc files cluttering the image layer.

    In a container there is no benefit from bytecode caching — the process
    starts fresh every time. This keeps the image clean and slightly smaller.
    """
    assert "PYTHONDONTWRITEBYTECODE" in dockerfile_text, (
        "Dockerfile must set ENV PYTHONDONTWRITEBYTECODE=1 to prevent .pyc "
        "files from accumulating inside the container filesystem"
    )


def test_dockerfile_pythonunbuffered_value(dockerfile_text: str) -> None:
    """PYTHONUNBUFFERED must be set to 1 (truthy disables buffering)."""
    assert re.search(r"PYTHONUNBUFFERED\s*=\s*1", dockerfile_text), (
        "PYTHONUNBUFFERED must equal '1' to disable Python output buffering"
    )


def test_dockerfile_pythondontwritebytecode_value(dockerfile_text: str) -> None:
    """PYTHONDONTWRITEBYTECODE must be set to 1."""
    assert re.search(r"PYTHONDONTWRITEBYTECODE\s*=\s*1", dockerfile_text), (
        "PYTHONDONTWRITEBYTECODE must equal '1' to skip .pyc generation"
    )


# ---------------------------------------------------------------------------
# Dockerfile — HEALTHCHECK instruction
# ---------------------------------------------------------------------------


def test_dockerfile_has_healthcheck_instruction(dockerfile_text: str) -> None:
    """Dockerfile must include a HEALTHCHECK so 'docker run' without compose works.

    The docker-compose healthcheck is only active when using compose.
    A Dockerfile HEALTHCHECK allows standalone 'docker run' and Kubernetes
    to detect unhealthy containers without requiring compose.
    """
    assert re.search(r"^\s*HEALTHCHECK\b", dockerfile_text, re.MULTILINE), (
        "Dockerfile must include a HEALTHCHECK instruction "
        "(e.g. HEALTHCHECK CMD curl -f http://localhost:8432/health || exit 1) "
        "so standalone 'docker run' can detect container health without docker-compose."
    )


def test_dockerfile_healthcheck_uses_health_endpoint(dockerfile_text: str) -> None:
    """Dockerfile HEALTHCHECK must probe /health."""
    # Only check if the HEALTHCHECK line references /health
    healthcheck_match = re.search(
        r"HEALTHCHECK.*\n?.*", dockerfile_text
    )
    assert healthcheck_match and "/health" in healthcheck_match.group(), (
        "Dockerfile HEALTHCHECK must probe /health endpoint"
    )


# ---------------------------------------------------------------------------
# Dockerfile — OCI image labels
# ---------------------------------------------------------------------------


def test_dockerfile_has_label_instruction(dockerfile_text: str) -> None:
    """Dockerfile must have LABEL metadata (OCI image annotations).

    Labels enable 'docker inspect' to show human-readable image metadata
    and are required by many registries for searchability.
    """
    assert re.search(r"^\s*LABEL\b", dockerfile_text, re.MULTILINE), (
        "Dockerfile must include at least one LABEL instruction with OCI image metadata "
        "(e.g. LABEL org.opencontainers.image.title=\"PondDB\")"
    )


def test_dockerfile_has_oci_title_label(dockerfile_text: str) -> None:
    """Dockerfile must set org.opencontainers.image.title."""
    assert "org.opencontainers.image.title" in dockerfile_text, (
        "Dockerfile must set LABEL org.opencontainers.image.title for OCI compliance"
    )


def test_dockerfile_has_oci_description_label(dockerfile_text: str) -> None:
    """Dockerfile must set org.opencontainers.image.description."""
    assert "org.opencontainers.image.description" in dockerfile_text, (
        "Dockerfile must set LABEL org.opencontainers.image.description"
    )


# ---------------------------------------------------------------------------
# docker-compose — container_name for deterministic naming
# ---------------------------------------------------------------------------


def test_compose_has_container_name(ponddb_service: dict) -> None:
    """ponddb service must set container_name for predictable 'docker exec' targeting.

    Without container_name, Docker generates a random suffix each run, making
    scripts like 'docker exec ponddb ...' fragile.
    """
    assert "container_name" in ponddb_service, (
        "docker-compose ponddb service must set 'container_name: ponddb' "
        "so that 'docker exec ponddb ...' works predictably across restarts"
    )


def test_compose_container_name_value(ponddb_service: dict) -> None:
    """container_name must be 'ponddb' (matches the service name)."""
    name = ponddb_service.get("container_name", "")
    assert name == "ponddb", (
        f"container_name should be 'ponddb', got {name!r}"
    )


# ---------------------------------------------------------------------------
# docker-compose — POND_LOG_LEVEL env var
# ---------------------------------------------------------------------------


def test_compose_sets_pond_log_level(ponddb_service: dict) -> None:
    """POND_LOG_LEVEL must be set in docker-compose for predictable production logging.

    Uvicorn's --log-level flag is set in the Dockerfile CMD, but the application's
    own logger (python-json-logger) reads POND_LOG_LEVEL. Without it, app-level
    logs may be unexpectedly verbose in production.
    """
    env = ponddb_service.get("environment", [])
    env_str = " ".join(str(e) for e in env) if isinstance(env, list) else str(env)
    assert "POND_LOG_LEVEL" in env_str, (
        "docker-compose must set POND_LOG_LEVEL (e.g. POND_LOG_LEVEL=warning) "
        "to control application log verbosity independent of uvicorn's --log-level"
    )


# ---------------------------------------------------------------------------
# docker-compose — logging driver
# ---------------------------------------------------------------------------


def test_compose_has_logging_config(ponddb_service: dict) -> None:
    """docker-compose should configure a logging driver to cap log file size.

    Without a logging config, Docker's default json-file driver accumulates
    logs unbounded on disk, which can fill the host volume in production.
    """
    assert "logging" in ponddb_service, (
        "docker-compose ponddb service must configure 'logging:' to cap log file size, e.g.:\n"
        "  logging:\n"
        "    driver: json-file\n"
        "    options:\n"
        "      max-size: '10m'\n"
        "      max-file: '3'"
    )


def test_compose_logging_has_max_size(ponddb_service: dict) -> None:
    """Logging config must set max-size to prevent unbounded disk usage."""
    logging_cfg = ponddb_service.get("logging", {})
    options = logging_cfg.get("options", {})
    assert "max-size" in options, (
        "docker-compose logging.options must include 'max-size' "
        "(e.g. max-size: '10m') to cap per-container log file size"
    )
