#!/usr/bin/env python3
"""Benchmark 1: 5 simulated agents writing memories concurrently."""

from __future__ import annotations

import asyncio
import random
import string
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

NUM_AGENTS = 5
WRITES_PER_AGENT = 100
PAYLOAD_SIZES = [100, 1_024, 10_240]  # bytes


def _random_content(size: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=size))


async def _writer(
    client, session_id: str, agent_id: int, latencies: list[float]
) -> int:
    """Write WRITES_PER_AGENT rows, return count of failures."""
    failures = 0
    for i in range(WRITES_PER_AGENT):
        size = PAYLOAD_SIZES[i % len(PAYLOAD_SIZES)]
        content = _random_content(size)
        sql = (
            f"INSERT INTO bench_memories (agent_id, content, created_at) "
            f"VALUES ({agent_id}, '{content}', NOW())"
        )
        t0 = time.perf_counter()
        try:
            await execute_query(client, session_id, sql)
            latencies.append(time.perf_counter() - t0)
        except Exception:
            failures += 1
    return failures


async def run(url: str, api_key: str) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Concurrent Write",
        description=(
            f"{NUM_AGENTS} agents writing {WRITES_PER_AGENT} memories each "
            f"({NUM_AGENTS * WRITES_PER_AGENT} total) with varying payload sizes."
        ),
    )
    async with make_client(url, api_key) as client:
        session_id = await create_session(client)
        # Setup table
        await execute_query(
            client,
            session_id,
            "CREATE TABLE bench_memories ("
            "  agent_id INTEGER, content VARCHAR, created_at TIMESTAMP"
            ")",
        )

        latencies: list[float] = []
        t_start = time.perf_counter()
        tasks = [
            _writer(client, session_id, i, latencies) for i in range(NUM_AGENTS)
        ]
        failures_list = await asyncio.gather(*tasks)
        wall_clock = time.perf_counter() - t_start
        total_failures = sum(failures_list)

        # Integrity check
        rows = await execute_query(
            client, session_id, "SELECT COUNT(*) AS n FROM bench_memories"
        )
        total_rows = rows["rows"][0][0]
        distinct = await execute_query(
            client,
            session_id,
            "SELECT COUNT(DISTINCT agent_id) AS n FROM bench_memories",
        )
        distinct_agents = distinct["rows"][0][0]

        # Cleanup
        await execute_query(client, session_id, "DROP TABLE bench_memories")
        await destroy_session(client, session_id)

    total_writes = NUM_AGENTS * WRITES_PER_AGENT
    pcts = percentiles(latencies)
    result.metrics = {
        "Total writes": total_writes,
        "Wall clock": f"{wall_clock:.2f}s",
        "Throughput": f"{total_writes / wall_clock:.0f} writes/sec",
        "Write latency p50": fmt_ms(pcts["p50"]),
        "Write latency p95": fmt_ms(pcts["p95"]),
        "Write latency p99": fmt_ms(pcts["p99"]),
        "Failed writes": total_failures,
        "Data integrity": (
            f"{distinct_agents} distinct agents, {total_rows} total rows"
        ),
    }
    result.passed = total_failures == 0 and total_rows == total_writes
    return result


async def main() -> None:
    args = build_parser("Benchmark: concurrent writes").parse_args()
    r = await run(args.url, args.api_key)
    print(r.to_markdown())


if __name__ == "__main__":
    asyncio.run(main())
