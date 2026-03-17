"""Thin wrapper around the PondDB REST API."""

import json
import time
from typing import Any

import httpx

POLL_INTERVAL = 0.5
POLL_MAX_SECONDS = 30


class PondDBError(Exception):
    """Raised when PondDB returns an error response."""


class PondDBClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute(self, sql: str) -> dict[str, Any]:
        """Submit SQL, poll until complete, return result dict."""
        resp = self._client.post("/pondapi/execute", json={"sql": sql})
        resp.raise_for_status()
        execution_id = resp.json()["execution_id"]
        return self._poll(execution_id)

    def _poll(self, execution_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + POLL_MAX_SECONDS
        while time.monotonic() < deadline:
            r = self._client.get(f"/pondapi/execute/{execution_id}/result")
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status == "complete":
                return data
            if status == "failed":
                raise PondDBError(data.get("error_message", "Query failed"))
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"Query {execution_id} timed out after {POLL_MAX_SECONDS}s")

    # ------------------------------------------------------------------
    # Schema / datasets
    # ------------------------------------------------------------------

    def list_datasets(self) -> list[dict[str, Any]]:
        resp = self._client.get("/datasets")
        resp.raise_for_status()
        return resp.json()

    def get_schema(self) -> list[dict[str, Any]]:
        resp = self._client.get("/schema")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # CSV upload
    # ------------------------------------------------------------------

    def upload_csv(self, name: str, csv_content: str) -> dict[str, Any]:
        files = {"file": (f"{name}.csv", csv_content.encode(), "text/csv")}
        data = {"name": name}
        resp = self._client.post("/datasets", files=files, data=data)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
