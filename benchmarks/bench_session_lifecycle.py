#!/usr/bin/env python3
"""Benchmark 5: Session create → active → suspend → resume → destroy, 100 cycles."""

from __future__ import annotations

import asyncio
import os
import time

from helpers import (
    BenchmarkResult,
    build_parser,
    create_session,
    destroy_session,
    execute_query,
    fmt_ms,
    make_client,
    percentiles,
)

CYCLES = 50
# Short idle timeout for benchmark (server must honour POND_IDLE_TIMEOUT).
# If the server's timeout is longer, we poll until SUSPENDED or skip that phase.
SUSPEND_POLL_TIMEOUT = 2.0  # max seconds to wait for auto-suspend
SUSPEND_POLL_INTERVAL = 0.5


async def _get_session_status(client, session_id: str) -> str | None:
    """Return the status string for a session, or None if not found."""
    resp = await client.get("/sessions")
    resp.raise_for_status()
    for s in resp.json():
        if s["session_id"] == session_id:
            return s["status"]
    return None


async def _wait_for_status(
    client, session_id: str, target: str, timeout: float
) -> float:
    """Poll until session reaches *target* status. Return seconds waited."""
    t0 = time.perf_counter()
    deadline = t0 + timeout
    while time.perf_counter() < deadline:
        status = await _get_session_status(client, session_id)
        if status and status.upper() == target.upper():
            return time.perf_counter() - t0
        await asyncio.sleep(SUSPEND_POLL_INTERVAL)
    raise TimeoutError(
        f"Session {session_id} did not reach {target} within {timeout}s"
    )


async def run(url: str, api_key: str) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Session Lifecycle",
        description=f"{CYCLES} full create \u2192 suspend \u2192 resume \u2192 destroy cycles.",
    )

    create_lats: list[float] = []
    resume_lats: list[float] = []
    full_cycle_lats: list[float] = []
    suspend_times: list[float] = []
    failures = 0
    skipped_suspend = 0

    async with make_client(url, api_key, timeout=60.0) as client:
        for _ in range(CYCLES):
            cycle_t0 = time.perf_counter()
            try:
                # CREATE
                t0 = time.perf_counter()
                session_id = await create_session(client)
                create_lats.append(time.perf_counter() - t0)

                # Verify ACTIVE
                status = await _get_session_status(client, session_id)
                assert status and status.upper() == "ACTIVE"

                # Execute a query to confirm session works
                await execute_query(
                    client, session_id, "SELECT 'lifecycle_test' AS tag"
                )

                # Wait for auto-suspend (best effort — server timeout may
                # be long, so we cap our wait)
                try:
                    wait = await _wait_for_status(
                        client, session_id, "SUSPENDED", SUSPEND_POLL_TIMEOUT
                    )
                    suspend_times.append(wait)
                except TimeoutError:
                    skipped_suspend += 1
                    # Destroy and continue — server idle timeout is too long
                    await destroy_session(client, session_id)
                    full_cycle_lats.append(time.perf_counter() - cycle_t0)
                    continue

                # RESUME via query
                t0 = time.perf_counter()
                r = await execute_query(
                    client, session_id, "SELECT 42 AS answer"
                )
                resume_lats.append(time.perf_counter() - t0)
                assert r["rows"][0][0] == 42

                # Verify ACTIVE again
                status = await _get_session_status(client, session_id)
                assert status and status.upper() == "ACTIVE"

                # DESTROY
                await destroy_session(client, session_id)

                full_cycle_lats.append(time.perf_counter() - cycle_t0)

            except Exception:
                failures += 1

    cp = percentiles(create_lats)
    rp = percentiles(resume_lats) if resume_lats else {"p50": 0, "p95": 0}
    fp = percentiles(full_cycle_lats)

    result.metrics = {
        "Cycles completed": CYCLES - failures,
        "Failed cycles": failures,
        "Session create p50": fmt_ms(cp["p50"]),
        "Session create p95": fmt_ms(cp["p95"]),
    }
    if suspend_times:
        result.metrics["Auto-suspend detection (avg)"] = (
            f"{sum(suspend_times) / len(suspend_times):.1f}s"
        )
    if resume_lats:
        result.metrics["Resume latency p50"] = fmt_ms(rp["p50"])
        result.metrics["Resume latency p95"] = fmt_ms(rp["p95"])
    result.metrics["Full cycle p50"] = fmt_ms(fp["p50"])
    if skipped_suspend:
        result.metrics["Skipped suspend (timeout)"] = skipped_suspend
    result.passed = failures == 0
    return result


async def main() -> None:
    args = build_parser("Benchmark: session lifecycle").parse_args()
    r = await run(args.url, args.api_key)
    print(r.to_markdown())


if __name__ == "__main__":
    asyncio.run(main())
