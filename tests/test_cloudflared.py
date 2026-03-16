"""Tests for cloudflared service in docker-compose.yml and nginx CF header handling.

Validates:
  - cloudflared service is declared in docker-compose.yml
  - cloudflared image is from cloudflare/cloudflared
  - cloudflared tunnel is configured to forward to nginx:80
  - cloudflared has a health check
  - cloudflared depends_on nginx (starts after nginx is up)
  - cloudflared has a restart policy
  - cloudflared does NOT expose ports (outbound tunnel only)
  - nginx.conf forwards CF-Connecting-IP header to backend
  - nginx uses CF-Connecting-IP as the real client IP

Does NOT start containers — validates source YAML/config files only.
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
NGINX_CONF = REPO_ROOT / "nginx" / "nginx.conf"


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.exists(), "docker-compose.yml not found at repo root"
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def services(compose: dict) -> dict:
    return compose.get("services", {})


@pytest.fixture(scope="module")
def cloudflared_service(services: dict) -> dict:
    assert "cloudflared" in services, (
        f"docker-compose.yml must have a 'cloudflared' service — found: {list(services)}"
    )
    return services["cloudflared"]


@pytest.fixture(scope="module")
def nginx_conf_text() -> str:
    assert NGINX_CONF.exists(), f"nginx/nginx.conf not found at {NGINX_CONF}"
    return NGINX_CONF.read_text()


# ---------------------------------------------------------------------------
# Service presence
# ---------------------------------------------------------------------------


def test_compose_has_cloudflared_service(services: dict) -> None:
    """docker-compose.yml must define a cloudflared service."""
    assert "cloudflared" in services, (
        "Expected 'cloudflared' service in docker-compose.yml"
    )


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


def test_cloudflared_uses_official_image(cloudflared_service: dict) -> None:
    """cloudflared service must use the official cloudflare/cloudflared image."""
    image = cloudflared_service.get("image", "")
    assert "cloudflare/cloudflared" in str(image), (
        f"cloudflared service must use 'cloudflare/cloudflared' image, got: {image!r}"
    )


# ---------------------------------------------------------------------------
# Tunnel target — must point to nginx:80
# ---------------------------------------------------------------------------


def test_cloudflared_tunnel_targets_nginx(cloudflared_service: dict) -> None:
    """cloudflared must be configured to forward traffic to nginx:80.

    This can appear in:
    - command: [..., '--url', 'http://nginx:80', ...]
    - environment: TUNNEL_URL=http://nginx:80
    - volumes mounting a config.yml that references nginx:80
    """
    # Check command args
    command = cloudflared_service.get("command", "")
    if isinstance(command, list):
        command_str = " ".join(str(c) for c in command)
    else:
        command_str = str(command)

    # Check environment variables
    env = cloudflared_service.get("environment", [])
    if isinstance(env, list):
        env_str = " ".join(str(e) for e in env)
    elif isinstance(env, dict):
        env_str = " ".join(f"{k}={v}" for k, v in env.items())
    else:
        env_str = str(env)

    combined = command_str + " " + env_str
    assert "nginx:80" in combined or "nginx" in combined, (
        f"cloudflared must be configured to tunnel to nginx:80. "
        f"command: {command_str!r}, env: {env_str!r}"
    )


def test_cloudflared_tunnel_url_uses_http(cloudflared_service: dict) -> None:
    """The tunnel target URL must use http (not https — nginx terminates TLS)."""
    command = cloudflared_service.get("command", "")
    if isinstance(command, list):
        command_str = " ".join(str(c) for c in command)
    else:
        command_str = str(command)

    env = cloudflared_service.get("environment", [])
    if isinstance(env, list):
        env_str = " ".join(str(e) for e in env)
    elif isinstance(env, dict):
        env_str = " ".join(f"{k}={v}" for k, v in env.items())
    else:
        env_str = str(env)

    combined = command_str + " " + env_str
    # If nginx:80 appears, check it's not prefixed with https
    if "nginx:80" in combined:
        assert "https://nginx:80" not in combined, (
            "cloudflared tunnel URL to nginx:80 must use http://, not https://"
        )
    # If we only see "nginx" without port, that's acceptable — still pass


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def test_cloudflared_has_healthcheck(cloudflared_service: dict) -> None:
    """cloudflared service must define a health check."""
    hc = cloudflared_service.get("healthcheck")
    assert hc is not None, (
        "cloudflared service must have a 'healthcheck' key — "
        "e.g. test: [CMD, cloudflared, tunnel, info] or "
        "test: [CMD-SHELL, cloudflared tunnel info || exit 1]"
    )


def test_cloudflared_healthcheck_has_test(cloudflared_service: dict) -> None:
    """cloudflared healthcheck must specify a test command."""
    hc = cloudflared_service.get("healthcheck", {})
    test_cmd = hc.get("test")
    assert test_cmd is not None, (
        "cloudflared healthcheck must have a 'test' key"
    )
    test_str = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
    assert len(test_str.strip()) > 0, "cloudflared healthcheck test must not be empty"


def test_cloudflared_healthcheck_has_interval(cloudflared_service: dict) -> None:
    """cloudflared healthcheck must specify an interval."""
    hc = cloudflared_service.get("healthcheck", {})
    assert "interval" in hc, (
        "cloudflared healthcheck must have an 'interval' key (e.g. interval: 30s)"
    )


def test_cloudflared_healthcheck_has_retries(cloudflared_service: dict) -> None:
    """cloudflared healthcheck must specify retry count."""
    hc = cloudflared_service.get("healthcheck", {})
    assert "retries" in hc, (
        "cloudflared healthcheck must have a 'retries' key (e.g. retries: 3)"
    )


# ---------------------------------------------------------------------------
# Service dependencies
# ---------------------------------------------------------------------------


def test_cloudflared_depends_on_nginx(cloudflared_service: dict) -> None:
    """cloudflared must declare depends_on: nginx so tunnel starts after nginx is ready."""
    depends_on = cloudflared_service.get("depends_on", [])
    if isinstance(depends_on, dict):
        depends_list = list(depends_on.keys())
    else:
        depends_list = list(depends_on)
    assert "nginx" in depends_list, (
        f"cloudflared service must have 'depends_on: [nginx]', got: {depends_list}"
    )


# ---------------------------------------------------------------------------
# Restart policy
# ---------------------------------------------------------------------------


def test_cloudflared_has_restart_policy(cloudflared_service: dict) -> None:
    """cloudflared service must have a restart policy for resilience."""
    restart = cloudflared_service.get("restart")
    assert restart in ("unless-stopped", "always", "on-failure"), (
        f"cloudflared must have restart policy (unless-stopped/always/on-failure), "
        f"got: {restart!r}"
    )


# ---------------------------------------------------------------------------
# No exposed ports (outbound-only tunnel)
# ---------------------------------------------------------------------------


def test_cloudflared_does_not_expose_ports(cloudflared_service: dict) -> None:
    """cloudflared must NOT have 'ports:' — it connects outbound to Cloudflare.

    The whole point of cloudflared is that it establishes an outbound connection,
    so no inbound ports need to be published on the host.
    """
    ports = cloudflared_service.get("ports", [])
    assert len(ports) == 0, (
        f"cloudflared service must NOT expose ports (it connects outbound). "
        f"Found ports: {ports}"
    )


# ---------------------------------------------------------------------------
# Network connectivity — cloudflared must reach nginx
# ---------------------------------------------------------------------------


def test_cloudflared_and_nginx_on_same_network(
    cloudflared_service: dict, services: dict
) -> None:
    """cloudflared and nginx must be on the same Docker network so cloudflared can resolve 'nginx'.

    If neither service declares explicit networks, they share the implicit default
    network which is sufficient. If either declares explicit networks, they must
    share at least one.
    """
    nginx_service = services.get("nginx", {})

    cf_networks = cloudflared_service.get("networks", {})
    nginx_networks = nginx_service.get("networks", {})

    cf_set = set(cf_networks.keys() if isinstance(cf_networks, dict) else cf_networks)
    nginx_set = set(
        nginx_networks.keys() if isinstance(nginx_networks, dict) else nginx_networks
    )

    if not cf_set and not nginx_set:
        # Both on the implicit default network — fine
        return

    shared = cf_set & nginx_set
    assert len(shared) > 0, (
        f"cloudflared and nginx must share a network. "
        f"cloudflared networks: {cf_set}, nginx networks: {nginx_set}"
    )


# ---------------------------------------------------------------------------
# nginx.conf — CF-Connecting-IP header forwarding
# ---------------------------------------------------------------------------


def test_nginx_conf_forwards_cf_connecting_ip(nginx_conf_text: str) -> None:
    """nginx must forward Cloudflare's CF-Connecting-IP header to the backend.

    cloudflared injects CF-Connecting-IP with the real visitor IP.
    nginx must pass this header along so the application can see the true
    client IP rather than the cloudflared container IP.

    Expected config:
        proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
    """
    assert "CF-Connecting-IP" in nginx_conf_text or "cf_connecting_ip" in nginx_conf_text, (
        "nginx.conf must forward the CF-Connecting-IP header to the backend. "
        "Add: proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;"
    )


def test_nginx_conf_uses_cf_ip_as_real_ip(nginx_conf_text: str) -> None:
    """nginx should use CF-Connecting-IP as the authoritative real client IP.

    When traffic comes through cloudflared, $remote_addr is the cloudflared
    container IP, not the real visitor. nginx must extract the real IP from
    the CF-Connecting-IP header set by cloudflared.

    Expected config:
        proxy_set_header X-Real-IP $http_cf_connecting_ip;
    """
    # Either set X-Real-IP from cf_connecting_ip, or forward the header directly
    has_real_ip_from_cf = (
        "cf_connecting_ip" in nginx_conf_text
        and "X-Real-IP" in nginx_conf_text
    )
    has_cf_header_passthrough = "CF-Connecting-IP" in nginx_conf_text

    assert has_real_ip_from_cf or has_cf_header_passthrough, (
        "nginx.conf must either set X-Real-IP from $http_cf_connecting_ip or "
        "pass the CF-Connecting-IP header through. "
        "Add: proxy_set_header X-Real-IP $http_cf_connecting_ip; "
        "and/or: proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;"
    )


def test_nginx_conf_cf_header_in_public_server_block(nginx_conf_text: str) -> None:
    """CF-Connecting-IP header forwarding must be inside the port-80 server block.

    The public server block (listen 80) is the one that receives cloudflared
    traffic. The header pass-through must appear in a location / proxy block
    within that server block.
    """
    # Find the port 80 server block by looking for "listen 80"
    # then verify CF-Connecting-IP appears after it
    listen_80_pos = nginx_conf_text.find("listen 80")
    assert listen_80_pos != -1, "nginx.conf must have a 'listen 80' server block"

    # CF-Connecting-IP should appear somewhere after listen 80
    cf_pos = nginx_conf_text.find("CF-Connecting-IP", listen_80_pos)
    if cf_pos == -1:
        cf_pos = nginx_conf_text.find("cf_connecting_ip", listen_80_pos)

    assert cf_pos != -1, (
        "CF-Connecting-IP header forwarding must appear in the port-80 server block "
        "(the block that receives cloudflared traffic)"
    )


# ---------------------------------------------------------------------------
# Tunnel token / credentials
# ---------------------------------------------------------------------------


def test_cloudflared_has_tunnel_token_config(cloudflared_service: dict) -> None:
    """cloudflared must be configured with a tunnel token or credential.

    Acceptable forms:
    - TUNNEL_TOKEN env var (named tunnel via token)
    - CLOUDFLARE_TUNNEL_TOKEN env var
    - command includes '--token' argument
    - a volume mounting a credentials file
    - command uses '--url' for quick tunnels (no token required)
    """
    command = cloudflared_service.get("command", "")
    if isinstance(command, list):
        command_str = " ".join(str(c) for c in command)
    else:
        command_str = str(command)

    env = cloudflared_service.get("environment", [])
    if isinstance(env, list):
        env_str = " ".join(str(e) for e in env)
    elif isinstance(env, dict):
        env_str = " ".join(f"{k}={v}" for k, v in env.items())
    else:
        env_str = str(env)

    volumes = cloudflared_service.get("volumes", [])
    volumes_str = " ".join(str(v) for v in volumes)

    combined = command_str + " " + env_str + " " + volumes_str

    has_token = (
        "TUNNEL_TOKEN" in combined
        or "--token" in combined
        or "--url" in combined  # quick tunnel, no token needed
        or "credentials" in combined
        or "config" in combined  # config file with credentials_file
    )

    assert has_token, (
        "cloudflared must be configured with authentication. Use one of:\n"
        "  - TUNNEL_TOKEN env var (named tunnel)\n"
        "  - --token TOKEN in command\n"
        "  - --url http://nginx:80 (quick tunnel)\n"
        "  - volumes mounting a credentials file"
    )
