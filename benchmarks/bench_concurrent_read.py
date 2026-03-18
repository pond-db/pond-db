#!/usr/bin/env python3
"""Benchmark 2: 5 readers + 2 writers running concurrently for 30 seconds."""

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

DURATION = 30  # seconds
NUM_READERS = 5
NUM_WRITERS = 2
WRITER_RATE = 10  # writes/sec each
READER_RATE = 20  # reads/sec each
PRELOAD_ROWS = 10_000
NUM_AGENTS = 5


def _rand(n: int = 80) -> str:
    return "".join(random.choices(string.ascii_letters, k=n))


async def _preload(client, session_id: str) -> None:
    """Bulk-insert PRELOAD_ROWS rows (respects 50 KB SQL limit)."""
    batch_size = 200
    for offset in range(0, PRELOAD_ROWS, batch_size):
        values = ", ".join(
            f"({i % NUM_AGENTS}, '{_rand()}', NOW())"
            for i in range(offset, min(offset + batch_size, PRELOAD_ROWS))
        )
        await execute_query(
            client,
            session_id,
            f"INSERT INTO bench_rw (agent_id, content, created_at) VALUES {values}",
        )


async def _writer_loop(
    client, session_id: str, stop: asyncio.Event, latencies: list[float]
) -> int:
    """Write at WRITER_RATE until stop is set. Return write count."""
    interval = 1.0 / WRITER_RATE
    count = 0
    while not stop.is_set():
        sql = (
            f"INSERT INTO bench_rw (agent_id, content, created_at) "
            f"VALUES ({random.randint(0, NUM_AGENTS - 1)}, '{_rand()}', NOW())"
        )
        t0 = time.perf_counter()
        try:
            await execute_query(client, session_id, sql)
            latencies.append(time.perf_counter() - t0)
            count += 1
        except Exception:
            pass
        await asyncio.sleep(interval)
    return count


async def _reader_loop(
    client, session_id: str, stop: asyncio.Event, latencies: list[float]
) -> int:
    """Read at READER_RATE until stop is set. Return read count."""
    interval = 1.0 / READER_RATE
    count = 0
    queries = [
        "SELECT COUNT(*) FROM bench_rw",
        "SELECT agent_id, COUNT(*) FROM bench_rw GROUP BY 1",
        "SELECT * FROM bench_rw ORDER BY created_at DESC LIMIT 10",
        f"SELECT * FROM bench_rw WHERE agent_id = {random.randint(0, 4)} LIMIT 20",
    ]
    while not stop.is_set():
        sql = random.choice(queries)
        t0 = time.perf_counter()
        try:
            await execute_query(client, session_id, sql)
            latencies.append(time.perf_counter() - t0)
            count += 1
        except Exception:
            pass
        await asyncio.sleep(interval)
    return count


async def run(url: str, api_key: str) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Concurrent Read/Write",
        description=(
            f"{NUM_READERS} readers + {NUM_WRITERS} writers for {DURATION}s "
            f"on {PRELOAD_ROWS:,} pre-loaded rows."
        ),
    )
    async with make_client(url, api_key, timeout=60.0) as client:
        session_id = await create_session(client)
        await execute_query(
            client,
            session_id,
            "CREATE TABLE bench_rw ("
            "  agent_id INTEGER, content VARCHAR, created_at TIMESTAMP"
            ")",
        )
        await _preload(client, session_id)

        stop = asyncio.Event()
        w_lat: list[float] = []
        r_lat: list[float] = []

        writers = [
            _writer_loop(client, session_id, stop, w_lat)
            for _ in range(NUM_WRITERS)
        ]
        readers = [
            _reader_loop(client, session_id, stop, r_lat)
            for _ in range(NUM_READERS)
        ]
        all_tasks = [asyncio.create_task(c) for c in writers + readers]

        await asyncio.sleep(DURATION)
        stop.set()
        counts = await asyncio.gather(*all_tasks)

        write_count = sum(counts[:NUM_WRITERS])
        read_count = sum(counts[NUM_WRITERS:])

        # Cleanup
        await execute_query(client, session_id, "DROP TABLE bench_rw")
        await destroy_session(client, session_id)

    rp = percentiles(r_lat)
    wp = percentiles(w_lat)
    result.metrics = {
        "Read throughput": f"{read_count / DURATION:.0f} reads/sec",
        "Read latency p50": fmt_ms(rp["p50"]),
        "Read latency p95": fmt_ms(rp["p95"]),
        "Read latency p99": fmt_ms(rp["p99"]),
        "Write throughput": f"{write_count / DURATION:.0f} writes/sec",
        "Write latency p50": fmt_ms(wp["p50"]),
        "Write latency p95": fmt_ms(wp["p95"]),
        "Write latency p99": fmt_ms(wp["p99"]),
        "Total reads": read_count,
        "Total writes": write_count,
    }
    result.passed = read_count > 0 and write_count > 0
    return result


async def main() -> None:
    args = build_parser("Benchmark: concurrent read/write").parse_args()
    r = await run(args.url, args.api_key)
    print(r.to_markdown())


if __name__ == "__main__":
    asyncio.run(main())
