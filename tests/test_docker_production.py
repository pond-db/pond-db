"""Additional production-readiness tests for Docker artifacts.

Covers gaps in test_dockerfile.py and test_docker_compose.py:
- Dockerfile copies pyproject.toml + README.md (required by hatchling)
- Multi-stage artifact copy pattern (COPY --from=builder)
- pip install --no-cache-dir and --prefix flags
- Non-root user directive follows COPY
- docker-compose.yml data volume is a named volume (not host bind mount)
- .dockerignore completeness for production secrets and build artifacts
- pyproject.toml exposes pond CLI entrypoint and correct package path
"""

import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
PYPROJECT = REPO_ROOT / "pyproject.toml"


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
def dockerignore_text() -> str:
    assert DOCKERIGNORE.exists(), ".dockerignore not found"
    return DOCKERIGNORE.read_text()


# ---------------------------------------------------------------------------
# Dockerfile — build inputs
# ---------------------------------------------------------------------------


def test_dockerfile_copies_pyproject_toml(dockerfile_text: str) -> None:
    """pyproject.toml must be copied — hatchling needs it to build the wheel."""
    assert "pyproject.toml" in dockerfile_text, (
        "Dockerfile must COPY pyproject.toml so 'pip install .' can read project metadata"
    )


def test_dockerfile_copies_readme(dockerfile_text: str) -> None:
    """README.md must be copied — pyproject.toml declares readme = 'README.md'."""
    assert "README.md" in dockerfile_text, (
        "Dockerfile must COPY README.md; hatchling reads it from pyproject.toml readme field"
    )


def test_dockerfile_copies_src_directory(dockerfile_text: str) -> None:
    """src/ must be explicitly copied — that's where the package lives."""
    assert re.search(r"COPY\s+src[/\s]", dockerfile_text), (
        "Dockerfile must COPY src/ directory into the build context"
    )


# ---------------------------------------------------------------------------
# Dockerfile — multi-stage artifact copy
# ---------------------------------------------------------------------------


def test_dockerfile_runtime_copies_from_builder(dockerfile_text: str) -> None:
    """Runtime stage must use COPY --from=builder to pull installed packages."""
    assert re.search(r"COPY\s+--from=builder", dockerfile_text, re.IGNORECASE), (
        "Runtime stage must use 'COPY --from=builder' to pull the installed packages"
    )


def test_dockerfile_pip_install_no_cache(dockerfile_text: str) -> None:
    """pip install should use --no-cache-dir to keep the image lean."""
    assert "--no-cache-dir" in dockerfile_text, (
        "pip install must use --no-cache-dir to avoid storing the pip cache in the image layer"
    )


# ---------------------------------------------------------------------------
# Dockerfile — runtime stage ordering (USER comes after COPY)
# ---------------------------------------------------------------------------


def test_dockerfile_user_after_copy(dockerfile_lines: list[str]) -> None:
    """USER directive must come after all COPY directives — enforces least-privilege."""
    user_idx = None
    last_copy_idx = None
    for i, line in enumerate(dockerfile_lines):
        stripped = line.strip()
        if stripped.upper().startswith("USER "):
            user_idx = i
        if stripped.upper().startswith("COPY "):
            last_copy_idx = i

    assert user_idx is not None, "Dockerfile must have a USER directive"
    assert last_copy_idx is not None, "Dockerfile must have at least one COPY directive"
    assert user_idx > last_copy_idx, (
        f"USER directive (line {user_idx + 1}) must come after last COPY (line {last_copy_idx + 1})"
    )


def test_dockerfile_cmd_is_exec_form(dockerfile_text: str) -> None:
    """CMD must use exec form (JSON array) for proper signal handling."""
    assert re.search(r'CMD\s*\[', dockerfile_text), (
        "CMD must use exec form (e.g. CMD [\"uvicorn\", ...]) not shell form"
    )


# ---------------------------------------------------------------------------
# docker-compose.yml — named volume (not host bind mount)
# ---------------------------------------------------------------------------


def test_compose_data_volume_is_named(compose: dict, ponddb_service: dict) -> None:
    """data volume must be a named volume, not a host bind mount (./data:...)."""
    named_volumes = set(compose.get("volumes", {}).keys())
    service_volumes = ponddb_service.get("volumes", [])

    # Find the volume targeting /app/data
    data_volume_source = None
    for v in service_volumes:
        if isinstance(v, str) and "/app/data" in v:
            parts = v.split(":")
            if len(parts) >= 2:
                data_volume_source = parts[0]
        elif isinstance(v, dict) and "/app/data" in v.get("target", ""):
            data_volume_source = v.get("source", "")

    assert data_volume_source is not None, "No volume mount targeting /app/data found"
    assert data_volume_source in named_volumes, (
        f"Volume source '{data_volume_source}' must be a named volume declared in the top-level "
        f"volumes section {named_volumes}, not a host path like './data'"
    )


def test_compose_pond_jwt_secret_env_documented(ponddb_service: dict) -> None:
    """Service should reference POND_JWT_SECRET via env or Docker secrets (not hardcoded)."""
    env = ponddb_service.get("environment", [])
    secrets = ponddb_service.get("secrets", [])
    env_str = " ".join(env) if isinstance(env, list) else str(env)
    # JWT secret can be provided via:
    # 1. POND_JWT_SECRET env var (empty or env-var reference)
    # 2. POND_JWT_SECRET_FILE env var pointing to a Docker secret
    # 3. Docker secrets section
    has_jwt_secret_file = "POND_JWT_SECRET_FILE" in env_str
    has_jwt_secret = "POND_JWT_SECRET" in env_str and "POND_JWT_SECRET_FILE" not in env_str
    has_docker_secret = any("jwt" in str(s).lower() for s in secrets)
    assert has_jwt_secret_file or has_jwt_secret or has_docker_secret, (
        "JWT secret must be configured via env var, _FILE env var, or Docker secrets"
    )
    # If using plain POND_JWT_SECRET env var (not _FILE), verify not hardcoded
    if has_jwt_secret and not has_jwt_secret_file:
        for item in (env if isinstance(env, list) else []):
            if "POND_JWT_SECRET" in str(item):
                assert "=" not in str(item) or str(item).endswith("=") or "$" in str(item), (
                    "POND_JWT_SECRET must not be hardcoded — use an env var reference or leave blank"
                )


# ---------------------------------------------------------------------------
# .dockerignore — production completeness
# ---------------------------------------------------------------------------


def test_dockerignore_excludes_egg_info(dockerignore_text: str) -> None:
    """*.egg-info must be excluded — build artifacts from local installs."""
    assert ".egg-info" in dockerignore_text or "*.egg-info" in dockerignore_text, (
        ".dockerignore must exclude *.egg-info/ directories"
    )


def test_dockerignore_excludes_pytest_cache(dockerignore_text: str) -> None:
    """pytest cache should not enter the build context."""
    assert ".pytest_cache" in dockerignore_text, (
        ".dockerignore must exclude .pytest_cache/"
    )


def test_dockerignore_excludes_ruff_cache(dockerignore_text: str) -> None:
    """Linter cache should not enter the build context."""
    assert ".ruff_cache" in dockerignore_text, (
        ".dockerignore must exclude .ruff_cache/"
    )


def test_dockerignore_excludes_pyc_files(dockerignore_text: str) -> None:
    """Compiled .pyc files must not enter the build context."""
    assert ".pyc" in dockerignore_text or "*.py[cod]" in dockerignore_text, (
        ".dockerignore must exclude compiled Python bytecode (*.pyc or *.py[cod])"
    )


# ---------------------------------------------------------------------------
# pyproject.toml — packaging correctness
# ---------------------------------------------------------------------------


def test_pyproject_declares_ponddb_package() -> None:
    """pyproject.toml must declare the src/ponddb package path."""
    import tomllib  # Python 3.11+

    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)

    hatch_build = data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    packages = hatch_build.get("packages", [])
    assert any("ponddb" in p for p in packages), (
        f"pyproject.toml hatch wheel packages must include 'src/ponddb', got: {packages}"
    )


def test_pyproject_pond_entrypoint() -> None:
    """pyproject.toml must expose the 'pond' CLI entrypoint."""
    import tomllib

    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)

    scripts = data.get("project", {}).get("scripts", {})
    assert "pond" in scripts, (
        f"pyproject.toml must declare 'pond' script entrypoint, got: {scripts}"
    )
    assert "ponddb" in scripts["pond"], (
        f"'pond' entrypoint must point to ponddb module, got: {scripts['pond']!r}"
    )


def test_pyproject_requires_python_312() -> None:
    """Package must require Python 3.12+ to match the Dockerfile base image."""
    import tomllib

    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)

    requires = data.get("project", {}).get("requires-python", "")
    assert "3.12" in requires, (
        f"pyproject.toml requires-python must target 3.12+, got: {requires!r}"
    )
