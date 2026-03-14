"""Tests for FastAPI app skeleton and /health endpoint.

Defines the expected behavior for M1: app setup + /health endpoint.
"""

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from ponddb.app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# /health — happy path
# ---------------------------------------------------------------------------


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_content_type_is_json(client: TestClient) -> None:
    response = client.get("/health")
    assert "application/json" in response.headers["content-type"]


def test_health_response_has_status_ok(client: TestClient) -> None:
    data = client.get("/health").json()
    assert data["status"] == "ok"


def test_health_response_has_version_string(client: TestClient) -> None:
    data = client.get("/health").json()
    assert "version" in data
    assert isinstance(data["version"], str)
    # semver-ish: e.g. "0.1.0"
    assert re.match(r"^\d+\.\d+\.\d+", data["version"])


def test_health_response_has_sessions_int(client: TestClient) -> None:
    data = client.get("/health").json()
    assert "sessions" in data
    assert isinstance(data["sessions"], int)
    assert data["sessions"] >= 0


def test_health_response_has_exactly_three_keys(client: TestClient) -> None:
    """No extra undocumented fields — schema is contractual."""
    data = client.get("/health").json()
    assert set(data.keys()) == {"status", "version", "sessions"}


def test_health_version_matches_package_version(client: TestClient) -> None:
    from ponddb import __version__

    data = client.get("/health").json()
    assert data["version"] == __version__


# ---------------------------------------------------------------------------
# /health — method and routing
# ---------------------------------------------------------------------------


def test_health_head_request_returns_200(client: TestClient) -> None:
    """HEAD should be allowed and return no body."""
    response = client.head("/health")
    assert response.status_code == 200


def test_health_post_returns_405(client: TestClient) -> None:
    response = client.post("/health")
    assert response.status_code == 405


def test_health_delete_returns_405(client: TestClient) -> None:
    response = client.delete("/health")
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# App metadata
# ---------------------------------------------------------------------------


def test_app_title_is_ponddb(client: TestClient) -> None:
    from ponddb.app import app

    assert app.title == "PondDB"


def test_app_version_matches_package(client: TestClient) -> None:
    from ponddb import __version__
    from ponddb.app import app

    assert app.version == __version__


def test_openapi_schema_reachable(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "PondDB"


# ---------------------------------------------------------------------------
# 404 / error response format
# ---------------------------------------------------------------------------


def test_unknown_route_returns_404(client: TestClient) -> None:
    response = client.get("/nonexistent")
    assert response.status_code == 404


def test_unknown_route_returns_json_body(client: TestClient) -> None:
    """Errors must be loud and structured (design tenet 4)."""
    response = client.get("/nonexistent")
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    assert "detail" in body


# ---------------------------------------------------------------------------
# App lifespan / startup
# ---------------------------------------------------------------------------


def test_app_has_lifespan_or_startup_handler() -> None:
    """App must define a lifespan context manager for resource management."""
    from ponddb.app import app

    # FastAPI lifespan is stored on the router
    assert app.router.lifespan_context is not None


# ---------------------------------------------------------------------------
# __version__ sanity
# ---------------------------------------------------------------------------


def test_package_version_is_semver() -> None:
    from ponddb import __version__

    assert re.match(r"^\d+\.\d+\.\d+$", __version__), f"Not semver: {__version__!r}"
