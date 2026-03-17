"""Tests for docker-compose.yml structure and correctness.

Parses docker-compose.yml as YAML and asserts configuration requirements.
Does NOT start containers — validates the source file only.
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.exists(), "docker-compose.yml not found at repo root"
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def ponddb_service(compose: dict) -> dict:
    services = compose.get("services", {})
    assert "ponddb" in services, f"Expected 'ponddb' service, found: {list(services)}"
    return services["ponddb"]


# ---------------------------------------------------------------------------
# Service basics
# ---------------------------------------------------------------------------


def test_compose_has_ponddb_service(compose: dict) -> None:
    assert "ponddb" in compose.get("services", {})


def test_compose_ponddb_builds_from_context(ponddb_service: dict) -> None:
    """Service must build from the repo root (build: .)."""
    build = ponddb_service.get("build")
    assert build is not None, "ponddb service must have a 'build' key"
    # Can be "." or {"context": "."} form
    if isinstance(build, dict):
        assert build.get("context") == ".", f"build.context must be '.', got {build.get('context')!r}"
    else:
        assert build == ".", f"build must be '.', got {build!r}"


def test_compose_port_mapping(ponddb_service: dict) -> None:
    """Port 8432 is either published directly or proxied by nginx on port 80/8433."""
    ports = ponddb_service.get("ports", [])
    # Direct port mapping OR internal-only (nginx handles external ports)
    has_direct = any("8432" in str(p) for p in ports)
    # Check command exposes 8432 internally
    command = ponddb_service.get("command", [])
    has_internal = any("8432" in str(c) for c in command)
    assert has_direct or has_internal, (
        f"Expected port 8432 in ports or command, got ports={ports}, command={command}"
    )


def test_compose_restart_policy(ponddb_service: dict) -> None:
    """Service should restart automatically on failure."""
    restart = ponddb_service.get("restart")
    assert restart in ("unless-stopped", "always", "on-failure"), (
        f"restart policy should be 'unless-stopped', 'always', or 'on-failure', got {restart!r}"
    )


# ---------------------------------------------------------------------------
# Volume mounts
# ---------------------------------------------------------------------------


def test_compose_has_data_volume(compose: dict) -> None:
    """A named volume for persistent data must be declared."""
    volumes = compose.get("volumes", {})
    assert len(volumes) >= 1, "At least one named volume must be declared"


def test_compose_ponddb_mounts_data_volume(ponddb_service: dict, compose: dict) -> None:
    """ponddb service must mount the data volume into /app/data."""
    service_volumes = ponddb_service.get("volumes", [])
    assert len(service_volumes) >= 1, "ponddb service must have at least one volume mount"

    # Check /app/data target appears in one of the mounts
    targets = []
    for v in service_volumes:
        if isinstance(v, str):
            # "named-vol:/app/data" or "./host:/container"
            targets.append(v.split(":")[-1].split(":")[0] if ":" in v else v)
        elif isinstance(v, dict):
            targets.append(v.get("target", ""))

    assert any("/app/data" in t for t in targets), (
        f"Expected a volume mount targeting /app/data, got: {service_volumes}"
    )


# ---------------------------------------------------------------------------
# Health check — this is the NEW requirement
# ---------------------------------------------------------------------------


def test_compose_has_healthcheck(ponddb_service: dict) -> None:
    """ponddb service must define a healthcheck."""
    hc = ponddb_service.get("healthcheck")
    assert hc is not None, (
        "ponddb service must have a 'healthcheck' key — "
        "add healthcheck: {test: [CMD, curl, -f, http://localhost:8432/health]}"
    )


def test_compose_healthcheck_uses_health_endpoint(ponddb_service: dict) -> None:
    """Healthcheck must probe the /health endpoint."""
    hc = ponddb_service.get("healthcheck", {})
    test_cmd = hc.get("test", [])

    # test can be a list ["CMD", ...] or string "CMD curl ..."
    test_str = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
    assert "/health" in test_str, (
        f"healthcheck.test must reference /health endpoint, got: {test_str!r}"
    )


def test_compose_healthcheck_has_interval(ponddb_service: dict) -> None:
    """Healthcheck must specify an interval."""
    hc = ponddb_service.get("healthcheck", {})
    assert "interval" in hc, (
        "healthcheck must have an 'interval' key (e.g. interval: 30s)"
    )


def test_compose_healthcheck_has_timeout(ponddb_service: dict) -> None:
    """Healthcheck must specify a timeout."""
    hc = ponddb_service.get("healthcheck", {})
    assert "timeout" in hc, (
        "healthcheck must have a 'timeout' key (e.g. timeout: 5s)"
    )


def test_compose_healthcheck_has_retries(ponddb_service: dict) -> None:
    """Healthcheck must specify retry count."""
    hc = ponddb_service.get("healthcheck", {})
    assert "retries" in hc, (
        "healthcheck must have a 'retries' key (e.g. retries: 3)"
    )


def test_compose_healthcheck_uses_http(ponddb_service: dict) -> None:
    """Healthcheck must use HTTP (curl or wget)."""
    hc = ponddb_service.get("healthcheck", {})
    test_cmd = hc.get("test", [])
    test_str = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
    assert any(tool in test_str for tool in ("curl", "wget")), (
        f"healthcheck.test must use curl or wget, got: {test_str!r}"
    )


# ---------------------------------------------------------------------------
# .dockerignore completeness
# ---------------------------------------------------------------------------


def test_dockerignore_excludes_tests_dir() -> None:
    """tests/ must be excluded from build context — keeps image lean."""
    content = DOCKERIGNORE.read_text()
    assert "tests/" in content or "tests" in content, (
        ".dockerignore must exclude tests/ directory"
    )


def test_dockerignore_excludes_venv() -> None:
    """Virtual environment must never enter the image."""
    content = DOCKERIGNORE.read_text()
    assert ".venv" in content, ".dockerignore must exclude .venv/"


def test_dockerignore_excludes_pycache() -> None:
    """Compiled bytecode should not enter the build context."""
    content = DOCKERIGNORE.read_text()
    assert "__pycache__" in content, ".dockerignore must exclude __pycache__/"


def test_dockerignore_excludes_git_dir() -> None:
    """.git history must not be baked into the image."""
    content = DOCKERIGNORE.read_text()
    assert ".git" in content, ".dockerignore must exclude .git/"


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------


def test_compose_sets_pond_host(ponddb_service: dict) -> None:
    """POND_HOST env var should be configured."""
    env = ponddb_service.get("environment", [])
    env_str = " ".join(env) if isinstance(env, list) else str(env)
    assert "POND_HOST" in env_str, f"POND_HOST env var must be set, got: {env}"


def test_compose_sets_pond_port(ponddb_service: dict) -> None:
    """POND_PORT env var should be configured."""
    env = ponddb_service.get("environment", [])
    env_str = " ".join(env) if isinstance(env, list) else str(env)
    assert "POND_PORT" in env_str, f"POND_PORT env var must be set, got: {env}"
