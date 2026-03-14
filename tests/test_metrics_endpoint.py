"""Tests for the GET /metrics endpoint.

Defines expected behavior for Prometheus-format metrics export:
  - ponddb_sessions_active   (gauge)
  - ponddb_query_duration_seconds (histogram)
  - ponddb_compute_units_total (counter)

Tests import from ponddb.app and will fail until the endpoint is implemented.
"""

import importlib
import os

import pytest
from fastapi.testclient import TestClient

VALID_KEY = "test-metrics-key-abc"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POND_API_KEY", VALID_KEY)


@pytest.fixture
def client(_set_api_key) -> TestClient:
    import ponddb.app as app_module
    importlib.reload(app_module)
    from ponddb.app import app
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_KEY}


@pytest.fixture
def session_id(client: TestClient) -> str:
    resp = client.post("/session")
    assert resp.status_code == 201
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# GET /metrics — response basics
# ---------------------------------------------------------------------------


def test_metrics_returns_200(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_content_type_is_text_plain(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


def test_metrics_body_is_non_empty(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert len(resp.text.strip()) > 0


def test_metrics_no_auth_required(client: TestClient) -> None:
    """Prometheus scrape endpoints must be unauthenticated."""
    resp = client.get("/metrics")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /metrics — ponddb_sessions_active gauge
# ---------------------------------------------------------------------------


def test_metrics_contains_sessions_active(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert "ponddb_sessions_active" in resp.text


def test_metrics_sessions_active_zero_when_no_sessions(client: TestClient) -> None:
    resp = client.get("/metrics")
    # Find the line that sets the gauge value
    for line in resp.text.splitlines():
        if line.startswith("ponddb_sessions_active") and not line.startswith("#"):
            value = float(line.split()[-1])
            assert value == 0.0
            return
    pytest.fail("ponddb_sessions_active metric line not found")


def test_metrics_sessions_active_increments_on_create(
    client: TestClient,
) -> None:
    client.post("/session")
    resp = client.get("/metrics")
    value = _extract_gauge(resp.text, "ponddb_sessions_active")
    assert value >= 1.0


def test_metrics_sessions_active_decrements_on_destroy(
    client: TestClient, session_id: str
) -> None:
    before = _extract_gauge(client.get("/metrics").text, "ponddb_sessions_active")
    client.delete(f"/session/{session_id}")
    after = _extract_gauge(client.get("/metrics").text, "ponddb_sessions_active")
    assert after == before - 1.0


def test_metrics_sessions_active_counts_multiple_sessions(client: TestClient) -> None:
    client.post("/session")
    client.post("/session")
    client.post("/session")
    value = _extract_gauge(client.get("/metrics").text, "ponddb_sessions_active")
    assert value == 3.0


def test_metrics_sessions_active_has_help_comment(client: TestClient) -> None:
    resp = client.get("/metrics")
    lines = resp.text.splitlines()
    help_lines = [l for l in lines if l.startswith("# HELP ponddb_sessions_active")]
    assert len(help_lines) >= 1


def test_metrics_sessions_active_has_type_comment(client: TestClient) -> None:
    resp = client.get("/metrics")
    lines = resp.text.splitlines()
    type_lines = [l for l in lines if l.startswith("# TYPE ponddb_sessions_active")]
    assert len(type_lines) >= 1
    assert "gauge" in type_lines[0]


# ---------------------------------------------------------------------------
# GET /metrics — ponddb_query_duration_seconds histogram
# ---------------------------------------------------------------------------


def test_metrics_contains_query_duration(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert "ponddb_query_duration_seconds" in resp.text


def test_metrics_query_duration_has_help_comment(client: TestClient) -> None:
    resp = client.get("/metrics")
    lines = resp.text.splitlines()
    help_lines = [l for l in lines if "# HELP ponddb_query_duration_seconds" in l]
    assert len(help_lines) >= 1


def test_metrics_query_duration_has_type_histogram(client: TestClient) -> None:
    resp = client.get("/metrics")
    lines = resp.text.splitlines()
    type_lines = [l for l in lines if "# TYPE ponddb_query_duration_seconds" in l]
    assert len(type_lines) >= 1
    assert "histogram" in type_lines[0]


def test_metrics_query_duration_has_bucket_lines(client: TestClient) -> None:
    resp = client.get("/metrics")
    bucket_lines = [
        l for l in resp.text.splitlines()
        if "ponddb_query_duration_seconds_bucket" in l and not l.startswith("#")
    ]
    assert len(bucket_lines) >= 1


def test_metrics_query_duration_has_count_line(client: TestClient) -> None:
    resp = client.get("/metrics")
    count_lines = [
        l for l in resp.text.splitlines()
        if "ponddb_query_duration_seconds_count" in l and not l.startswith("#")
    ]
    assert len(count_lines) >= 1


def test_metrics_query_duration_has_sum_line(client: TestClient) -> None:
    resp = client.get("/metrics")
    sum_lines = [
        l for l in resp.text.splitlines()
        if "ponddb_query_duration_seconds_sum" in l and not l.startswith("#")
    ]
    assert len(sum_lines) >= 1


def test_metrics_query_duration_count_starts_at_zero(client: TestClient) -> None:
    resp = client.get("/metrics")
    count = _extract_histogram_count(resp.text, "ponddb_query_duration_seconds")
    assert count == 0.0


def test_metrics_query_duration_count_increments_after_query(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    before = _extract_histogram_count(
        client.get("/metrics").text, "ponddb_query_duration_seconds"
    )
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    after = _extract_histogram_count(
        client.get("/metrics").text, "ponddb_query_duration_seconds"
    )
    assert after == before + 1.0


def test_metrics_query_duration_sum_positive_after_query(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    resp = client.get("/metrics")
    total = _extract_histogram_sum(resp.text, "ponddb_query_duration_seconds")
    assert total > 0.0


def test_metrics_query_duration_sum_in_seconds_not_ms(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Duration must be in seconds (Prometheus convention), not milliseconds."""
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    total = _extract_histogram_sum(
        client.get("/metrics").text, "ponddb_query_duration_seconds"
    )
    # A simple in-memory query should be well under 5 seconds
    assert total < 5.0


def test_metrics_query_duration_multiple_queries_accumulate(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    for i in range(5):
        client.post(
            "/query",
            json={"session_id": session_id, "sql": f"SELECT {i}"},
            headers=auth_headers,
        )
    count = _extract_histogram_count(
        client.get("/metrics").text, "ponddb_query_duration_seconds"
    )
    assert count == 5.0


def test_metrics_failed_query_not_recorded_in_histogram(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    """Only successful queries count toward the duration histogram."""
    before = _extract_histogram_count(
        client.get("/metrics").text, "ponddb_query_duration_seconds"
    )
    client.post(
        "/query",
        json={"session_id": session_id, "sql": "NOT VALID SQL !!!"},
        headers=auth_headers,
    )
    after = _extract_histogram_count(
        client.get("/metrics").text, "ponddb_query_duration_seconds"
    )
    # Failed queries should NOT increment the histogram
    assert after == before


# ---------------------------------------------------------------------------
# GET /metrics — ponddb_compute_units_total counter
# ---------------------------------------------------------------------------


def test_metrics_contains_compute_units_total(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert "ponddb_compute_units_total" in resp.text


def test_metrics_compute_units_has_help_comment(client: TestClient) -> None:
    resp = client.get("/metrics")
    lines = resp.text.splitlines()
    help_lines = [l for l in lines if "# HELP ponddb_compute_units_total" in l]
    assert len(help_lines) >= 1


def test_metrics_compute_units_has_type_counter(client: TestClient) -> None:
    resp = client.get("/metrics")
    lines = resp.text.splitlines()
    type_lines = [l for l in lines if "# TYPE ponddb_compute_units_total" in l]
    assert len(type_lines) >= 1
    assert "counter" in type_lines[0]


def test_metrics_compute_units_starts_at_zero(client: TestClient) -> None:
    value = _extract_gauge(client.get("/metrics").text, "ponddb_compute_units_total")
    assert value == 0.0


def test_metrics_compute_units_increments_after_query(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    before = _extract_gauge(client.get("/metrics").text, "ponddb_compute_units_total")
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    after = _extract_gauge(client.get("/metrics").text, "ponddb_compute_units_total")
    assert after > before


def test_metrics_compute_units_accumulate_across_queries(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    mid = _extract_gauge(client.get("/metrics").text, "ponddb_compute_units_total")
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 2"}, headers=auth_headers)
    final = _extract_gauge(client.get("/metrics").text, "ponddb_compute_units_total")
    assert final >= mid


def test_metrics_compute_units_is_non_negative(
    client: TestClient, session_id: str, auth_headers: dict
) -> None:
    client.post("/query", json={"session_id": session_id, "sql": "SELECT 1"}, headers=auth_headers)
    value = _extract_gauge(client.get("/metrics").text, "ponddb_compute_units_total")
    assert value >= 0.0


# ---------------------------------------------------------------------------
# GET /metrics — Prometheus format correctness
# ---------------------------------------------------------------------------


def test_metrics_lines_starting_with_hash_are_comments(client: TestClient) -> None:
    resp = client.get("/metrics")
    for line in resp.text.splitlines():
        if line.startswith("#"):
            assert line.startswith("# HELP") or line.startswith("# TYPE"), (
                f"Unexpected comment line: {line!r}"
            )


def test_metrics_data_lines_have_name_and_value(client: TestClient) -> None:
    resp = client.get("/metrics")
    for line in resp.text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) >= 2, f"Malformed metric line: {line!r}"
        # Last token must be a numeric value
        try:
            float(parts[-1])
        except ValueError:
            pytest.fail(f"Non-numeric value in metric line: {line!r}")


def test_metrics_all_three_metric_families_present(client: TestClient) -> None:
    resp = client.get("/metrics")
    text = resp.text
    assert "ponddb_sessions_active" in text
    assert "ponddb_query_duration_seconds" in text
    assert "ponddb_compute_units_total" in text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_gauge(text: str, metric_name: str) -> float:
    """Extract the scalar value from the first non-comment line matching metric_name."""
    for line in text.splitlines():
        if line.startswith(metric_name) and not line.startswith("#"):
            return float(line.split()[-1])
    raise AssertionError(f"Metric {metric_name!r} not found in:\n{text}")


def _extract_histogram_count(text: str, metric_name: str) -> float:
    return _extract_gauge(text, f"{metric_name}_count")


def _extract_histogram_sum(text: str, metric_name: str) -> float:
    return _extract_gauge(text, f"{metric_name}_sum")
