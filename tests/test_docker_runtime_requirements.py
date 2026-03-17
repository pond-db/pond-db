"""Production-readiness tests for Docker runtime correctness.

Tests are intentionally failing: they define the DESIRED behavior before
implementation changes are made.

Covers:
- curl must be installed in the runtime stage (needed by compose healthcheck)
- /app/data directory created with correct ownership before USER switch
- docker-compose healthcheck has start_period (boot grace time)
- POND_SQLITE_PATH points into the data volume (persistence across restarts)
- POND_DATA_ROOT points into the data volume (session DuckDB files persist)
- Runtime stage should NOT redundantly copy raw src/ (package installed via wheel)
- Uvicorn CMD should include --workers or at least --log-level for production
"""

import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


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
def runtime_stage_lines(dockerfile_lines: list[str]) -> list[str]:
    """Return only lines belonging to the final (runtime) stage."""
    from_indices = [
        i for i, l in enumerate(dockerfile_lines) if l.strip().upper().startswith("FROM")
    ]
    assert len(from_indices) >= 2, "Need at least 2 FROM stages"
    # Runtime stage starts at the second FROM
    return dockerfile_lines[from_indices[1] :]


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.exists(), "docker-compose.yml not found"
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def ponddb_service(compose: dict) -> dict:
    return compose["services"]["ponddb"]


# ---------------------------------------------------------------------------
# curl must be installed in the runtime image
# ---------------------------------------------------------------------------


def test_dockerfile_installs_curl_in_runtime(runtime_stage_lines: list[str]) -> None:
    """docker-compose healthcheck uses 'curl -f ...' — curl must be present in the image.

    python:3.12-slim does NOT ship with curl. The runtime stage must run
    'apt-get install -y curl' (or equivalent) before the USER directive.
    """
    runtime_text = "\n".join(runtime_stage_lines)
    assert "curl" in runtime_text, (
        "Runtime stage must install curl (e.g. RUN apt-get install -y curl) — "
        "the docker-compose healthcheck calls 'curl -f http://localhost:8432/health' "
        "but python:3.12-slim does not include curl."
    )


def test_dockerfile_apt_install_curl(runtime_stage_lines: list[str]) -> None:
    """apt-get install must explicitly list curl."""
    runtime_text = "\n".join(runtime_stage_lines)
    assert re.search(r"apt.*(install|get).*curl", runtime_text, re.IGNORECASE), (
        "Dockerfile runtime stage must run 'apt-get install ... curl' "
        "so the healthcheck binary is available inside the container."
    )


def test_dockerfile_apt_get_no_install_recommends(runtime_stage_lines: list[str]) -> None:
    """apt-get install should use --no-install-recommends to keep the image slim."""
    runtime_text = "\n".join(runtime_stage_lines)
    assert "--no-install-recommends" in runtime_text, (
        "apt-get install in runtime stage should include --no-install-recommends "
        "to avoid pulling in unnecessary packages and keep the image size down."
    )


def test_dockerfile_apt_rm_lists(runtime_stage_lines: list[str]) -> None:
    """apt cache must be cleaned up to avoid layer bloat."""
    runtime_text = "\n".join(runtime_stage_lines)
    assert "rm -rf /var/lib/apt/lists" in runtime_text, (
        "After apt-get install, run 'rm -rf /var/lib/apt/lists/*' "
        "to clean the apt cache and keep image layers lean."
    )


# ---------------------------------------------------------------------------
# /app/data directory must be created before USER switch
# ---------------------------------------------------------------------------


def test_dockerfile_creates_data_directory(runtime_stage_lines: list[str]) -> None:
    """The /app/data mount point must be pre-created inside the image.

    Without 'RUN mkdir -p /app/data', the directory is created by Docker at
    mount time as root:root — the non-root 'appuser' cannot write to it.
    """
    runtime_text = "\n".join(runtime_stage_lines)
    assert re.search(r"mkdir.*(/app/data|data)", runtime_text), (
        "Runtime stage must 'RUN mkdir -p /app/data' so the volume mount point "
        "exists and is writable by the non-root appuser."
    )


def test_dockerfile_data_dir_owned_by_appuser(runtime_stage_lines: list[str]) -> None:
    """chown appuser on /app/data ensures the non-root user can write session files."""
    runtime_text = "\n".join(runtime_stage_lines)
    assert re.search(r"chown.*appuser.*(/app/data|data)", runtime_text) or re.search(
        r"mkdir.*-p.*data.*&&.*chown", runtime_text
    ), (
        "Runtime stage must chown /app/data to appuser so session DuckDB files "
        "and SQLite metadata can be written by the non-root process."
    )


def test_dockerfile_mkdir_before_user(runtime_stage_lines: list[str]) -> None:
    """mkdir/chown must execute before the USER directive (while still root)."""
    mkdir_idx = None
    user_idx = None
    for i, line in enumerate(runtime_stage_lines):
        stripped = line.strip()
        if re.search(r"mkdir.*data", stripped, re.IGNORECASE):
            mkdir_idx = i
        if stripped.upper().startswith("USER "):
            user_idx = i

    assert mkdir_idx is not None, "No 'mkdir ... data' found in runtime stage"
    assert user_idx is not None, "No USER directive found in runtime stage"
    assert mkdir_idx < user_idx, (
        f"'mkdir /app/data' (line {mkdir_idx}) must come before 'USER' (line {user_idx}) — "
        "directory must be created as root before switching to non-root user."
    )


# ---------------------------------------------------------------------------
# docker-compose healthcheck: start_period
# ---------------------------------------------------------------------------


def test_compose_healthcheck_has_start_period(ponddb_service: dict) -> None:
    """Healthcheck must have a start_period to give the app time to boot.

    Without start_period, Docker will immediately start counting retries on
    container start, potentially marking the service unhealthy before uvicorn
    has even finished loading the app.
    """
    hc = ponddb_service.get("healthcheck", {})
    assert "start_period" in hc, (
        "docker-compose healthcheck must include 'start_period' (e.g. start_period: 10s) "
        "to allow the FastAPI app time to initialize before health checks begin."
    )


# ---------------------------------------------------------------------------
# POND_SQLITE_PATH must point into the data volume
# ---------------------------------------------------------------------------


def test_compose_sets_pond_sqlite_path(ponddb_service: dict) -> None:
    """POND_SQLITE_PATH must direct the SQLite metadata DB into the data volume.

    Without this, the SQLite DB lives in the working directory (/app/ponddb.db)
    which is NOT mounted — the database is lost on every container restart.
    Sessions, catalog mounts, and compute logs are wiped on each restart.
    """
    env = ponddb_service.get("environment", [])
    env_str = " ".join(str(e) for e in env) if isinstance(env, list) else str(env)
    assert "POND_SQLITE_PATH" in env_str, (
        "docker-compose must set POND_SQLITE_PATH=/app/data/ponddb.db so the "
        "SQLite metadata database is stored on the persistent volume."
    )


def test_compose_pond_sqlite_path_in_data_dir(ponddb_service: dict) -> None:
    """POND_SQLITE_PATH value must reference /app/data/ (the mounted volume path)."""
    env = ponddb_service.get("environment", [])
    items = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
    sqlite_entry = next((str(e) for e in items if "POND_SQLITE_PATH" in str(e)), None)
    assert sqlite_entry is not None, "POND_SQLITE_PATH not found in environment"
    assert "/app/data" in sqlite_entry, (
        f"POND_SQLITE_PATH must point inside /app/data (the volume mount), got: {sqlite_entry!r}"
    )


# ---------------------------------------------------------------------------
# POND_DATA_ROOT must point into the data volume
# ---------------------------------------------------------------------------


def test_compose_sets_pond_data_root(ponddb_service: dict) -> None:
    """POND_DATA_ROOT must be set to /app/data so DuckDB session files persist.

    Each session uses a file-backed DuckDB connection in POND_DATA_ROOT.
    Without this env var, sessions default to ./data which is not on the volume.
    """
    env = ponddb_service.get("environment", [])
    env_str = " ".join(str(e) for e in env) if isinstance(env, list) else str(env)
    assert "POND_DATA_ROOT" in env_str, (
        "docker-compose must set POND_DATA_ROOT=/app/data so DuckDB session files "
        "are stored on the persistent volume (otherwise lost on container restart)."
    )


def test_compose_pond_data_root_in_data_dir(ponddb_service: dict) -> None:
    """POND_DATA_ROOT value must reference /app/data/."""
    env = ponddb_service.get("environment", [])
    items = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
    data_root_entry = next((str(e) for e in items if "POND_DATA_ROOT" in str(e)), None)
    assert data_root_entry is not None, "POND_DATA_ROOT not found in environment"
    assert "/app/data" in data_root_entry, (
        f"POND_DATA_ROOT must point inside /app/data, got: {data_root_entry!r}"
    )


# ---------------------------------------------------------------------------
# Runtime stage should NOT redundantly copy raw src/
# ---------------------------------------------------------------------------


def test_dockerfile_runtime_does_not_raw_copy_src(runtime_stage_lines: list[str]) -> None:
    """The runtime stage should NOT contain a bare 'COPY src/ ...' directive.

    The package is already installed into /usr/local via 'COPY --from=builder /install /usr/local'.
    A second raw COPY of src/ into the runtime stage is redundant, bloats the image,
    and creates an inconsistency (two copies of the source on different paths).
    """
    raw_src_copies = [
        line
        for line in runtime_stage_lines
        if re.match(r"\s*COPY\s+src[/\s]", line) and "--from=" not in line
    ]
    assert len(raw_src_copies) == 0, (
        f"Runtime stage must not raw-copy src/ — package is installed via wheel from builder stage. "
        f"Remove these COPY directives: {raw_src_copies}"
    )


# ---------------------------------------------------------------------------
# Uvicorn CMD: production log level
# ---------------------------------------------------------------------------


def test_dockerfile_cmd_specifies_log_level(dockerfile_text: str) -> None:
    """Uvicorn CMD should specify --log-level for predictable production logging.

    Without --log-level, uvicorn defaults to 'info' which is fine, but production
    deployments should be explicit so log verbosity can be tuned via the Dockerfile.
    """
    assert "--log-level" in dockerfile_text, (
        "Uvicorn CMD should include --log-level (e.g. --log-level warning) "
        "to make production log verbosity explicit and configurable."
    )
