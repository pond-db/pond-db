"""Shared utilities for PondDB benchmarks."""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def build_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--url", default="http://localhost:8432", help="PondDB base URL")
    p.add_argument("--api-key", required=True, help="PondDB API key (pk_…)")
    return p


# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------

def make_client(base_url: str, api_key: str, timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=timeout,
    )


async def create_session(
    client: httpx.AsyncClient,
    *,
    workgroup_id: str = "default",
    namespace: str = "default",
) -> str:
    """Create a session and return its session_id."""
    body: dict[str, str] = {"namespace": namespace}
    if workgroup_id != "default":
        body["workgroup_id"] = workgroup_id
    resp = await client.post("/session", json=body)
    resp.raise_for_status()
    return resp.json()["session_id"]


async def destroy_session(client: httpx.AsyncClient, session_id: str) -> None:
    await client.delete(f"/session/{session_id}")


async def execute_query(
    client: httpx.AsyncClient,
    session_id: str,
    sql: str,
) -> dict[str, Any]:
    """Run a synchronous query via POST /query."""
    resp = await client.post(
        "/query",
        json={"session_id": session_id, "sql": sql, "format": "json"},
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# PondAPI (async execute + poll)
# ---------------------------------------------------------------------------

async def pondapi_submit(
    client: httpx.AsyncClient,
    session_id: str,
    sql: str,
) -> str:
    """Submit via PondAPI, return execution_id."""
    resp = await client.post(
        "/pondapi/execute",
        json={"session_id": session_id, "sql": sql},
    )
    resp.raise_for_status()
    return resp.json()["execution_id"]


async def pondapi_poll(
    client: httpx.AsyncClient,
    execution_id: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Poll until complete/error."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get(f"/pondapi/execute/{execution_id}/result")
        resp.raise_for_status()
        data = resp.json()
        if data["status"] in ("complete", "error"):
            return data
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Execution {execution_id} did not complete in {timeout}s")


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def percentiles(latencies: list[float]) -> dict[str, float]:
    """Return p50, p95, p99 from a list of latencies (seconds → ms)."""
    s = sorted(latencies)
    n = len(s)
    if n == 0:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    return {
        "p50": s[n // 2] * 1000,
        "p95": s[int(n * 0.95)] * 1000,
        "p99": s[int(n * 0.99)] * 1000,
    }


def fmt_ms(ms: float) -> str:
    return f"{ms:.1f}ms"


# ---------------------------------------------------------------------------
# System info for reports
# ---------------------------------------------------------------------------

def system_info() -> dict[str, str]:
    import duckdb

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor() or platform.machine(),
        "duckdb": duckdb.__version__,
    }


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    name: str
    description: str
    metrics: dict[str, Any] = field(default_factory=dict)
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    passed: bool = True
    notes: str = ""

    def to_markdown(self) -> str:
        lines = [f"### {self.name}", "", self.description, ""]
        if self.table_headers:
            lines.append("| " + " | ".join(self.table_headers) + " |")
            lines.append("| " + " | ".join("---" for _ in self.table_headers) + " |")
            for row in self.table_rows:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
        for k, v in self.metrics.items():
            lines.append(f"- **{k}**: {v}")
        if self.notes:
            lines.append("")
            lines.append(f"> {self.notes}")
        lines.append("")
        status = "PASS" if self.passed else "FAIL"
        lines.append(f"**Result: {status}**")
        lines.append("")
        return "\n".join(lines)
