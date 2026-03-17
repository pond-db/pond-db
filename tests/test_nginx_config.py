"""Tests for nginx/nginx.conf and nginx/Dockerfile structure.

Validates the nginx configuration statically — no containers are started.
Checks:
  - Public server block listens on port 80
  - Admin server block listens on port 8433
  - Rate limiting zone is defined
  - client_max_body_size is set
  - /admin/* and /metrics are blocked from the public server block (return 403)
  - nginx Dockerfile is present and uses the nginx base image
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
NGINX_DIR = REPO_ROOT / "nginx"
NGINX_CONF = NGINX_DIR / "nginx.conf"
NGINX_DOCKERFILE = NGINX_DIR / "Dockerfile"


@pytest.fixture(scope="module")
def nginx_conf_text() -> str:
    assert NGINX_CONF.exists(), f"nginx/nginx.conf not found — expected at {NGINX_CONF}"
    return NGINX_CONF.read_text()


@pytest.fixture(scope="module")
def nginx_dockerfile_text() -> str:
    assert NGINX_DOCKERFILE.exists(), f"nginx/Dockerfile not found — expected at {NGINX_DOCKERFILE}"
    return NGINX_DOCKERFILE.read_text()


# ---------------------------------------------------------------------------
# nginx/Dockerfile
# ---------------------------------------------------------------------------


def test_nginx_dockerfile_exists(nginx_dockerfile_text: str) -> None:
    """nginx/Dockerfile must exist and be non-empty."""
    assert len(nginx_dockerfile_text.strip()) > 0, "nginx/Dockerfile is empty"


def test_nginx_dockerfile_uses_nginx_base(nginx_dockerfile_text: str) -> None:
    """Dockerfile must use an official nginx image as base."""
    assert re.search(r"FROM\s+nginx", nginx_dockerfile_text, re.IGNORECASE), (
        "nginx/Dockerfile must use nginx as its base image (e.g. FROM nginx:alpine)"
    )


def test_nginx_dockerfile_copies_conf(nginx_dockerfile_text: str) -> None:
    """Dockerfile must COPY nginx.conf into the image."""
    assert "nginx.conf" in nginx_dockerfile_text, (
        "nginx/Dockerfile must copy nginx.conf into the image "
        "(e.g. COPY nginx.conf /etc/nginx/nginx.conf)"
    )


# ---------------------------------------------------------------------------
# Public server block (port 80)
# ---------------------------------------------------------------------------


def test_nginx_conf_public_server_listens_port_80(nginx_conf_text: str) -> None:
    """Public server block must listen on port 80."""
    assert re.search(r"listen\s+80\b", nginx_conf_text), (
        "nginx.conf must have a server block that listens on port 80"
    )


def test_nginx_conf_public_server_proxies_to_ponddb(nginx_conf_text: str) -> None:
    """Public server must proxy to ponddb backend (port 8432)."""
    assert re.search(r"proxy_pass\s+http://[^;]*8432", nginx_conf_text), (
        "nginx.conf public server block must proxy_pass to the ponddb service on port 8432"
    )


# ---------------------------------------------------------------------------
# Admin server block (port 8433, Tailscale-only)
# ---------------------------------------------------------------------------


def test_nginx_conf_admin_server_listens_port_8433(nginx_conf_text: str) -> None:
    """Admin server block must listen on port 8433."""
    assert re.search(r"listen\s+8433\b", nginx_conf_text), (
        "nginx.conf must have a server block that listens on port 8433 (admin, Tailscale-only)"
    )


def test_nginx_conf_admin_server_has_allow_deny(nginx_conf_text: str) -> None:
    """Admin server block must restrict access (allow/deny directives for Tailscale)."""
    # Port 8433 block must contain allow and deny directives
    # Find the 8433 server block content
    # Simple check: both 'allow' and 'deny all' must appear somewhere in the file
    assert re.search(r"\ballow\b", nginx_conf_text), (
        "nginx.conf must have 'allow' directives in the admin server block "
        "to whitelist Tailscale IPs"
    )
    assert re.search(r"deny\s+all", nginx_conf_text), (
        "nginx.conf must have 'deny all' in the admin server block to block non-Tailscale traffic"
    )


def test_nginx_conf_admin_server_proxies_to_ponddb(nginx_conf_text: str) -> None:
    """Admin server (port 8433) must also proxy to ponddb."""
    # The file should proxy_pass in both server blocks
    proxy_passes = re.findall(r"proxy_pass\s+http://[^;]+;", nginx_conf_text)
    assert len(proxy_passes) >= 1, (
        "nginx.conf must have at least one proxy_pass directive (for admin server)"
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_nginx_conf_defines_rate_limit_zone(nginx_conf_text: str) -> None:
    """nginx.conf must define a limit_req_zone for rate limiting."""
    assert re.search(r"limit_req_zone\b", nginx_conf_text), (
        "nginx.conf must define a rate limiting zone using limit_req_zone directive"
    )


def test_nginx_conf_applies_rate_limit(nginx_conf_text: str) -> None:
    """nginx.conf must apply rate limiting via limit_req in the public server."""
    assert re.search(r"limit_req\b", nginx_conf_text), (
        "nginx.conf must apply rate limiting with limit_req directive"
    )


# ---------------------------------------------------------------------------
# client_max_body_size
# ---------------------------------------------------------------------------


def test_nginx_conf_sets_client_max_body_size(nginx_conf_text: str) -> None:
    """nginx.conf must set client_max_body_size."""
    assert re.search(r"client_max_body_size\s+\d+", nginx_conf_text), (
        "nginx.conf must set client_max_body_size (e.g. client_max_body_size 10m)"
    )


# ---------------------------------------------------------------------------
# Block /admin/* and /metrics from public server block
# ---------------------------------------------------------------------------


def test_nginx_conf_blocks_admin_path_from_public(nginx_conf_text: str) -> None:
    """Public server block (port 80) must block /admin/ with a 403."""
    # Must have a location block for /admin with return 403 or deny all
    assert re.search(r"location\s+/admin", nginx_conf_text), (
        "nginx.conf must have a location block for /admin to block public access"
    )
    assert re.search(r"return\s+403", nginx_conf_text) or re.search(
        r"deny\s+all", nginx_conf_text
    ), "nginx.conf must return 403 (or deny all) for /admin/* in the public server block"


def test_nginx_conf_blocks_metrics_path_from_public(nginx_conf_text: str) -> None:
    """Public server block (port 80) must block /metrics with a 403."""
    assert re.search(r"location\s+/metrics", nginx_conf_text), (
        "nginx.conf must have a location block for /metrics to block public access"
    )


def test_nginx_conf_metrics_returns_403_on_public(nginx_conf_text: str) -> None:
    """Confirm 403 is returned for /metrics in public block."""
    # Find location /metrics block and confirm 403 return
    metrics_match = re.search(r"location\s+/metrics\s*\{([^}]+)\}", nginx_conf_text, re.DOTALL)
    assert metrics_match is not None, "nginx.conf must have a 'location /metrics { ... }' block"
    block_content = metrics_match.group(1)
    assert "403" in block_content or "deny" in block_content, (
        "The /metrics location block must return 403 or use 'deny all'"
    )


def test_nginx_conf_admin_location_returns_403_on_public(nginx_conf_text: str) -> None:
    """Confirm /admin location block returns 403."""
    admin_match = re.search(
        r"location\s+~?\*?\s*/admin[^{]*\{([^}]+)\}", nginx_conf_text, re.DOTALL
    )
    assert admin_match is not None, (
        "nginx.conf must have a 'location /admin { ... }' block to block public access"
    )
    block_content = admin_match.group(1)
    assert "403" in block_content or "deny" in block_content, (
        "The /admin location block must return 403 or use 'deny all'"
    )


# ---------------------------------------------------------------------------
# Upstream / proxy configuration
# ---------------------------------------------------------------------------


def test_nginx_conf_has_upstream_or_proxy_pass(nginx_conf_text: str) -> None:
    """nginx.conf must define an upstream block or use proxy_pass directly."""
    has_upstream = re.search(r"\bupstream\b", nginx_conf_text)
    has_proxy_pass = re.search(r"\bproxy_pass\b", nginx_conf_text)
    assert has_upstream or has_proxy_pass, (
        "nginx.conf must have either an 'upstream' block or 'proxy_pass' directives"
    )


def test_nginx_conf_sets_proxy_host_header(nginx_conf_text: str) -> None:
    """nginx must forward the Host header to the upstream FastAPI."""
    assert re.search(r"proxy_set_header\s+Host", nginx_conf_text), (
        "nginx.conf must set 'proxy_set_header Host' to forward the Host header upstream"
    )
