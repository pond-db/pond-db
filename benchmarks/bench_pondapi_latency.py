#!/usr/bin/env python3
"""Benchmark 6: PondAPI submit + poll latency at increasing concurrency."""

from __future__ import annotations

import asyncio
import time

from helpers import (
    BenchmarkResult,
    build_parser,
    create_session,
    destroy_session,
    fmt_ms,
    make_client,
    percentiles,
    pondapi_poll,
    pondapi_submit,
)

CONCURRENCY_LEVELS = [1, 5, 10, 25]
QUERY = "SELECT 1 + 1 AS answer"
MAX_RETRIES = 1
RETRY_BACKOFF = 0.1  # seconds


async def _single_execution(
    client,
    session_id: str,
    submit_lats: list[float],
    e2e_lats: list[float],
) -> bool:
    """Submit, poll, measure.  Retry once on transient failure."""
    for attempt in range(1 + MAX_RETRIES):
        t_start = time.perf_counter()
        try:
            t0 = time.perf_counter()
            exec_id = await pondapi_submit(client, session_id, QUERY)
            submit_lats.append(time.perf_counter() - t0)

            result = await pondapi_poll(client, exec_id)
            e2e_lats.append(time.perf_counter() - t_start)
            return result["status"] == "complete"
        except Exception:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF)
                continue
            return False
    return False  # unreachable, keeps mypy happy


async def run(url: str, api_key: str) -> BenchmarkResult:
    result = BenchmarkResult(
        name="PondAPI Latency",
        description=(
            "PondAPI async execute + poll latency at concurrency "
            f"levels {CONCURRENCY_LEVELS}."
        ),
    )
    result.table_headers = [
        "Concurrency",
        "Submit p50",
        "Submit p95",
        "E2E p50",
        "E2E p95",
        "Errors",
    ]

    async with make_client(url, api_key, timeout=60.0) as client:
        for level in CONCURRENCY_LEVELS:
            # Each concurrency level gets its own session to avoid
            # rate-limit carry-over from prior levels.
            session_id = await create_session(client)
            submit_lats: list[float] = []
            e2e_lats: list[float] = []

            tasks = [
                _single_execution(client, session_id, submit_lats, e2e_lats)
                for _ in range(level)
            ]
            results_list = await asyncio.gather(*tasks)
            errors = results_list.count(False)

            sp = percentiles(submit_lats)
            ep = percentiles(e2e_lats)
            result.table_rows.append([
                str(level),
                fmt_ms(sp["p50"]),
                fmt_ms(sp["p95"]),
                fmt_ms(ep["p50"]),
                fmt_ms(ep["p95"]),
                str(errors),
            ])

            await destroy_session(client, session_id)
            # Brief pause between levels for clean measurement
            await asyncio.sleep(0.5)

    result.passed = all(row[-1] == "0" for row in result.table_rows)
    return result


async def main() -> None:
    args = build_parser("Benchmark: PondAPI latency").parse_args()
    r = await run(args.url, args.api_key)
    print(r.to_markdown())


if __name__ == "__main__":
    asyncio.run(main())
