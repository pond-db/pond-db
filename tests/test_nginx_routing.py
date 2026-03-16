"""Tests for nginx routing behaviour — verifies FastAPI responses match what nginx would proxy.

These tests use the FastAPI test client directly (no actual nginx process). They verify:
  1. Normal routes work (nginx would proxy them through to FastAPI)
  2. /admin/* and /metrics are accessible on the FastAPI side (nginx blocks them at ingress)
  3. Oversized request bodies are rejected with 413

Additionally, static analysis tests verify that nginx config correctly returns 403 for
/admin/* and /metrics — this represents the nginx-level enforcement.

For integration: if nginx is running (NGINX_URL env var set), tests also probe the
real nginx endpoint to confirm the 403 and 413 behaviors live.
"""

import os
import pathlib
import re

import pytest
import httpx

REPO_ROOT = pathlib.Path(__file__).parent.parent
NGINX_CONF = REPO_ROOT / "nginx" / "nginx.conf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nginx_conf_text() -> str:
    assert NGINX_CONF.exists(), f"nginx/nginx.conf not found at {NGINX_CONF}"
    return NGINX_CONF.read_text()


def _extract_server_block(conf: str, listen_port: int) -> str:
    """Extract the content of the server block that listens on listen_port.

    Returns the raw text of the matched server { ... } block.
    Raises AssertionError if not found.
    """
    # Find all server { ... } blocks (greedy approach — does not handle nested braces deeply)
    pattern = re.compile(r"server\s*\{", re.DOTALL)
    blocks = []
    for m in pattern.finditer(conf):
        start = m.end()
        depth = 1
        i = start
        while i < len(conf) and depth > 0:
            if conf[i] == "{":
                depth += 1
            elif conf[i] == "}":
                depth -= 1
            i += 1
        blocks.append(conf[start : i - 1])

    for block in blocks:
        if re.search(rf"\blisten\s+{listen_port}\b", block):
            return block

    raise AssertionError(
        f"No server block found listening on port {listen_port}. "
        f"Found {len(blocks)} server block(s) in nginx.conf."
    )


# ---------------------------------------------------------------------------
# nginx.conf — public server block (port 80) access control
# ---------------------------------------------------------------------------


class TestPublicServerBlockAccessControl:
    """Verify /admin/* and /metrics are blocked in the port-80 server block."""

    def test_public_server_block_exists(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 80)
        assert len(block.strip()) > 0

    def test_public_block_has_location_admin(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 80)
        assert re.search(r"location\s+[~*\s]*/admin", block), (
            "Public server block (port 80) must have a location block for /admin"
        )

    def test_public_block_admin_returns_403(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 80)
        # Find /admin location sub-block
        admin_loc = re.search(
            r"location\s+[~*\s]*/admin[^{]*\{([^}]+)\}", block, re.DOTALL
        )
        assert admin_loc is not None, (
            "Public server block must have a 'location /admin { ... }' block"
        )
        loc_body = admin_loc.group(1)
        assert "403" in loc_body or "deny" in loc_body, (
            "Public server /admin location must return 403 or deny all"
        )

    def test_public_block_has_location_metrics(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 80)
        assert re.search(r"location\s+/metrics", block), (
            "Public server block (port 80) must have a location block for /metrics"
        )

    def test_public_block_metrics_returns_403(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 80)
        metrics_loc = re.search(
            r"location\s+/metrics\s*\{([^}]+)\}", block, re.DOTALL
        )
        assert metrics_loc is not None, (
            "Public server block must have a 'location /metrics { ... }' block"
        )
        loc_body = metrics_loc.group(1)
        assert "403" in loc_body or "deny" in loc_body, (
            "Public server /metrics location must return 403 or deny all"
        )

    def test_public_block_has_default_proxy(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 80)
        assert re.search(r"proxy_pass\s+http://[^;]*8432", block), (
            "Public server block (port 80) must proxy_pass to ponddb on port 8432"
        )


# ---------------------------------------------------------------------------
# nginx.conf — admin server block (port 8433) access control
# ---------------------------------------------------------------------------


class TestAdminServerBlock:
    """Verify the admin server block (port 8433) exists and restricts access."""

    def test_admin_server_block_exists(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 8433)
        assert len(block.strip()) > 0

    def test_admin_block_allows_tailscale_subnet(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 8433)
        # Tailscale typically uses 100.64.0.0/10 CGNAT range
        assert re.search(r"\ballow\b", block), (
            "Admin server block (port 8433) must have 'allow' directive for Tailscale IPs"
        )

    def test_admin_block_denies_all_others(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 8433)
        assert re.search(r"deny\s+all", block), (
            "Admin server block (port 8433) must have 'deny all' to block non-Tailscale traffic"
        )

    def test_admin_block_proxies_to_ponddb(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 8433)
        assert re.search(r"proxy_pass", block), (
            "Admin server block (port 8433) must proxy_pass to ponddb"
        )


# ---------------------------------------------------------------------------
# nginx.conf — rate limiting and body size
# ---------------------------------------------------------------------------


class TestNginxLimits:
    """Verify rate limiting and body size limits are configured."""

    def test_rate_limit_zone_defined_in_http_context(self) -> None:
        conf = _nginx_conf_text()
        assert re.search(r"limit_req_zone\b", conf), (
            "nginx.conf must define limit_req_zone (rate limiting zone)"
        )

    def test_rate_limit_zone_uses_remote_addr(self) -> None:
        conf = _nginx_conf_text()
        zone_match = re.search(r"limit_req_zone\s+([^;]+);", conf)
        assert zone_match is not None, "limit_req_zone directive not found"
        zone_def = zone_match.group(1)
        assert "remote_addr" in zone_def or "binary_remote_addr" in zone_def, (
            "limit_req_zone should key on $remote_addr or $binary_remote_addr"
        )

    def test_rate_limit_applied_in_public_block(self) -> None:
        conf = _nginx_conf_text()
        block = _extract_server_block(conf, 80)
        assert re.search(r"limit_req\b", block), (
            "Public server block (port 80) must apply rate limiting with limit_req"
        )

    def test_client_max_body_size_configured(self) -> None:
        conf = _nginx_conf_text()
        match = re.search(r"client_max_body_size\s+(\d+\w*)", conf)
        assert match is not None, (
            "nginx.conf must set client_max_body_size (e.g. client_max_body_size 10m)"
        )
        # Verify a non-zero value is configured
        size_str = match.group(1)
        size_num = int(re.match(r"\d+", size_str).group())
        assert size_num > 0, f"client_max_body_size must be > 0, got: {size_str}"


# ---------------------------------------------------------------------------
# Live nginx tests (only when NGINX_URL is set in the environment)
# ---------------------------------------------------------------------------


NGINX_URL = os.environ.get("NGINX_URL", "").rstrip("/")


@pytest.mark.skipif(not NGINX_URL, reason="NGINX_URL not set — skipping live nginx tests")
class TestLiveNginxRouting:
    """Integration tests that probe a running nginx instance.

    Set NGINX_URL=http://localhost to enable (e.g. when running in Docker Compose).
    """

    def test_health_proxied_through_nginx(self) -> None:
        """GET /health should be proxied to FastAPI and return 200."""
        resp = httpx.get(f"{NGINX_URL}/health", timeout=5)
        assert resp.status_code == 200, (
            f"nginx should proxy /health to FastAPI (200), got {resp.status_code}"
        )
        data = resp.json()
        assert data.get("status") == "ok"

    def test_admin_blocked_by_nginx_public_port(self) -> None:
        """GET /admin via the public port (80) must return 403 from nginx."""
        resp = httpx.get(f"{NGINX_URL}/admin/", timeout=5)
        assert resp.status_code == 403, (
            f"nginx must return 403 for /admin/ on the public port, got {resp.status_code}"
        )

    def test_metrics_blocked_by_nginx_public_port(self) -> None:
        """GET /metrics via the public port (80) must return 403 from nginx."""
        resp = httpx.get(f"{NGINX_URL}/metrics", timeout=5)
        assert resp.status_code == 403, (
            f"nginx must return 403 for /metrics on the public port, got {resp.status_code}"
        )

    def test_oversized_body_returns_413(self) -> None:
        """POST with a body exceeding client_max_body_size must return 413."""
        # Read client_max_body_size from nginx.conf
        conf = _nginx_conf_text()
        match = re.search(r"client_max_body_size\s+(\d+)([mkMK]?)", conf)
        assert match is not None, "client_max_body_size not found in nginx.conf"
        size_num = int(match.group(1))
        unit = match.group(2).lower()
        multipliers = {"m": 1024 * 1024, "k": 1024, "": 1}
        max_bytes = size_num * multipliers.get(unit, 1)

        # Send 1 byte over the limit
        oversized = b"x" * (max_bytes + 1)
        resp = httpx.post(
            f"{NGINX_URL}/query",
            content=oversized,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        assert resp.status_code == 413, (
            f"nginx must return 413 for requests exceeding client_max_body_size "
            f"({max_bytes} bytes), got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# FastAPI-level: /admin and /metrics exist behind nginx (accessible internally)
# ---------------------------------------------------------------------------


def test_fastapi_health_endpoint_importable() -> None:
    """FastAPI app must have a /health endpoint (nginx proxies it on port 80)."""
    from fastapi.testclient import TestClient
    import os
    os.environ.setdefault("POND_JWT_SECRET", "test-secret-nginx")
    os.environ.setdefault("POND_API_KEY", "test-key-nginx")
    from ponddb.app import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200, (
        f"/health must return 200 (nginx proxies this); got {resp.status_code}"
    )


def test_fastapi_has_admin_routes() -> None:
    """FastAPI /admin routes must exist (nginx protects them, FastAPI serves them internally)."""
    from fastapi.testclient import TestClient
    import os
    os.environ.setdefault("POND_JWT_SECRET", "test-secret-nginx")
    os.environ.setdefault("POND_API_KEY", "test-key-nginx")
    from ponddb.app import app

    # Collect route paths
    paths = [route.path for route in app.routes]
    admin_paths = [p for p in paths if "/admin" in p]
    assert len(admin_paths) > 0, (
        "FastAPI must have /admin routes — nginx blocks public access, "
        "but the routes must exist for internal/admin-port access. "
        f"Registered paths: {paths}"
    )
