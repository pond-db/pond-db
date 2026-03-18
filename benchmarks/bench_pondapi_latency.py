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

CONCURRENCY_LEVELS = [1, 5, 10, 20, 50]
QUERY = "SELECT 1 + 1 AS answer"
# Seconds to sleep between concurrency levels so the sliding-window
# rate limit counter decays.  We set the env var POND_PONDAPI_RATE_LIMIT
# high before running, but still sleep a moment to keep results clean.
RATE_WINDOW_SECONDS = 1.0


async def _single_execution(
    client,
    session_id: str,
    submit_lats: list[float],
    e2e_lats: list[float],
) -> bool:
    """Submit, poll, measure. Return True on success."""
    t_start = time.perf_counter()
    try:
        t0 = time.perf_counter()
        exec_id = await pondapi_submit(client, session_id, QUERY)
        submit_lats.append(time.perf_counter() - t0)

        result = await pondapi_poll(client, exec_id)
        e2e_lats.append(time.perf_counter() - t_start)
        return result["status"] == "complete"
    except Exception:
        return False


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
            # Let rate-limit window slide before next level
            await asyncio.sleep(RATE_WINDOW_SECONDS)

    # Allow up to 5% error rate at high concurrency levels
    result.passed = all(
        int(row[-1]) <= max(1, int(row[0]) * 0.05)
        for row in result.table_rows
    )
    return result


async def main() -> None:
    args = build_parser("Benchmark: PondAPI latency").parse_args()
    r = await run(args.url, args.api_key)
    print(r.to_markdown())


if __name__ == "__main__":
    asyncio.run(main())
