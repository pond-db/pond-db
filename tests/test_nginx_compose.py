"""Tests for docker-compose.yml nginx integration.

Validates that:
  - An 'nginx' service is present in docker-compose.yml
  - The nginx service exposes ports 80 and 8433
  - The nginx service depends_on (or otherwise references) ponddb
  - The ponddb service does NOT expose ports directly (nginx handles ingress)
  - The nginx service builds from ./nginx or uses an nginx image

Does NOT start containers — validates source YAML only.
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.exists(), "docker-compose.yml not found at repo root"
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def services(compose: dict) -> dict:
    return compose.get("services", {})


@pytest.fixture(scope="module")
def nginx_service(services: dict) -> dict:
    assert "nginx" in services, (
        f"docker-compose.yml must have an 'nginx' service — found: {list(services)}"
    )
    return services["nginx"]


@pytest.fixture(scope="module")
def ponddb_service(services: dict) -> dict:
    assert "ponddb" in services, (
        f"docker-compose.yml must have a 'ponddb' service — found: {list(services)}"
    )
    return services["ponddb"]


# ---------------------------------------------------------------------------
# nginx service presence and build
# ---------------------------------------------------------------------------


def test_compose_has_nginx_service(services: dict) -> None:
    """docker-compose.yml must define an nginx service."""
    assert "nginx" in services, "Expected 'nginx' service in docker-compose.yml"


def test_nginx_service_builds_or_uses_image(nginx_service: dict) -> None:
    """nginx service must either build from ./nginx or use an nginx image."""
    build = nginx_service.get("build")
    image = nginx_service.get("image", "")
    assert build is not None or "nginx" in str(image).lower(), (
        "nginx service must have 'build: ./nginx' or use an nginx base image"
    )


def test_nginx_service_build_context_is_nginx_dir(nginx_service: dict) -> None:
    """nginx service should build from the ./nginx directory."""
    build = nginx_service.get("build")
    if build is None:
        pytest.skip("nginx service uses a pre-built image, not a local build")
    if isinstance(build, dict):
        context = build.get("context", "")
    else:
        context = str(build)
    assert "nginx" in context, (
        f"nginx service build context should point to ./nginx, got: {context!r}"
    )


# ---------------------------------------------------------------------------
# nginx port exposure
# ---------------------------------------------------------------------------


def test_nginx_service_exposes_port_80(nginx_service: dict) -> None:
    """nginx must publish port 80 (public HTTP)."""
    ports = nginx_service.get("ports", [])
    assert any("80" in str(p) for p in ports), (
        f"nginx service must expose port 80, got ports: {ports}"
    )


def test_nginx_service_exposes_port_8433(nginx_service: dict) -> None:
    """nginx must publish port 8433 (admin / Tailscale)."""
    ports = nginx_service.get("ports", [])
    assert any("8433" in str(p) for p in ports), (
        f"nginx service must expose port 8433 (admin), got ports: {ports}"
    )


# ---------------------------------------------------------------------------
# ponddb should NOT expose ports directly (nginx handles ingress)
# ---------------------------------------------------------------------------


def test_ponddb_service_has_no_direct_ports(ponddb_service: dict) -> None:
    """ponddb service must NOT expose ports — nginx is the ingress point.

    Removing 'ports:' from ponddb means external traffic can only reach it
    through the nginx reverse proxy, enforcing the security boundary.
    """
    ports = ponddb_service.get("ports", [])
    assert len(ports) == 0, (
        f"ponddb service must NOT have 'ports:' exposed (nginx handles ingress). "
        f"Found ports: {ports}"
    )


# ---------------------------------------------------------------------------
# nginx service depends_on ponddb
# ---------------------------------------------------------------------------


def test_nginx_service_depends_on_ponddb(nginx_service: dict) -> None:
    """nginx service must declare depends_on: ponddb so ponddb starts first."""
    depends_on = nginx_service.get("depends_on", [])
    if isinstance(depends_on, dict):
        depends_list = list(depends_on.keys())
    else:
        depends_list = list(depends_on)
    assert "ponddb" in depends_list, (
        f"nginx service must have 'depends_on: [ponddb]', got: {depends_list}"
    )


# ---------------------------------------------------------------------------
# nginx service restart policy
# ---------------------------------------------------------------------------


def test_nginx_service_has_restart_policy(nginx_service: dict) -> None:
    """nginx service should have a restart policy."""
    restart = nginx_service.get("restart")
    assert restart in ("unless-stopped", "always", "on-failure"), (
        f"nginx service should have a restart policy (unless-stopped/always/on-failure), "
        f"got: {restart!r}"
    )


# ---------------------------------------------------------------------------
# Both services on same network (implicit or explicit)
# ---------------------------------------------------------------------------


def test_compose_services_can_communicate(nginx_service: dict, ponddb_service: dict) -> None:
    """Both services must be able to reach each other via Docker DNS.

    They are on the same network either implicitly (docker-compose default network)
    or via an explicitly declared shared network.
    """
    nginx_networks = set(
        nginx_service.get("networks", {}).keys()
        if isinstance(nginx_service.get("networks"), dict)
        else nginx_service.get("networks", [])
    )
    ponddb_networks = set(
        ponddb_service.get("networks", {}).keys()
        if isinstance(ponddb_service.get("networks"), dict)
        else ponddb_service.get("networks", [])
    )

    if not nginx_networks and not ponddb_networks:
        # Both use the implicit default network — they can communicate
        pass
    else:
        shared = nginx_networks & ponddb_networks
        assert len(shared) > 0, (
            f"nginx and ponddb must share at least one network. "
            f"nginx networks: {nginx_networks}, ponddb networks: {ponddb_networks}"
        )
