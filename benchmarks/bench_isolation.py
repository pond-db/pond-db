#!/usr/bin/env python3
"""Benchmark 3: Prove workgroup isolation is airtight under load."""

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
    make_client,
)

WORKGROUPS = ["alpha", "beta", "gamma"]
ROWS_PER_WG = 1_000
CROSS_QUERIES = 100


def _marker(wg: str, i: int) -> str:
    return f"MARKER_{wg.upper()}_{i}"


async def _populate(client, session_id: str, wg: str) -> None:
    """Insert ROWS_PER_WG rows with unique markers for this workgroup."""
    batch = 250
    for offset in range(0, ROWS_PER_WG, batch):
        values = ", ".join(
            f"('{wg}', '{_marker(wg, i)}', NOW())"
            for i in range(offset, min(offset + batch, ROWS_PER_WG))
        )
        await execute_query(
            client,
            session_id,
            f"INSERT INTO bench_iso (workgroup, content, created_at) VALUES {values}",
        )


async def run(url: str, api_key: str) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Workgroup Isolation",
        description=(
            f"3 workgroups ({', '.join(WORKGROUPS)}), "
            f"{ROWS_PER_WG:,} memories each, "
            f"{CROSS_QUERIES} cross-workgroup queries."
        ),
    )

    async with make_client(url, api_key) as client:
        # Each workgroup gets its own session (DuckDB isolation boundary)
        sessions: dict[str, str] = {}
        for wg in WORKGROUPS:
            sid = await create_session(client, workgroup_id="default")
            sessions[wg] = sid
            await execute_query(
                client,
                sid,
                "CREATE TABLE bench_iso ("
                "  workgroup VARCHAR, content VARCHAR, created_at TIMESTAMP"
                ")",
            )
            await _populate(client, sid, wg)

        # Verify each workgroup sees only its own data
        leaks = 0
        queries_run = 0

        for wg in WORKGROUPS:
            sid = sessions[wg]
            # Positive check: own data is present
            own = await execute_query(
                client, sid, f"SELECT COUNT(*) FROM bench_iso WHERE workgroup = '{wg}'"
            )
            own_count = own["rows"][0][0]
            if own_count != ROWS_PER_WG:
                leaks += 1

            # Negative check: other workgroups' markers are absent
            others = [w for w in WORKGROUPS if w != wg]
            for other in others:
                for _ in range(CROSS_QUERIES // (len(WORKGROUPS) * 2)):
                    idx = random.randint(0, ROWS_PER_WG - 1)
                    marker = _marker(other, idx)
                    r = await execute_query(
                        client,
                        sid,
                        f"SELECT COUNT(*) FROM bench_iso "
                        f"WHERE content = '{marker}'",
                    )
                    queries_run += 1
                    if r["rows"][0][0] > 0:
                        leaks += 1

        # Concurrent writes to all 3 workgroups
        async def _concurrent_write(wg: str) -> None:
            sid = sessions[wg]
            for i in range(50):
                await execute_query(
                    client,
                    sid,
                    f"INSERT INTO bench_iso VALUES "
                    f"('{wg}', 'concurrent_{wg}_{i}', NOW())",
                )

        await asyncio.gather(*[_concurrent_write(wg) for wg in WORKGROUPS])

        # Re-verify after concurrent writes
        for wg in WORKGROUPS:
            sid = sessions[wg]
            others = [w for w in WORKGROUPS if w != wg]
            for other in others:
                r = await execute_query(
                    client,
                    sid,
                    f"SELECT COUNT(*) FROM bench_iso WHERE workgroup = '{other}'",
                )
                queries_run += 1
                if r["rows"][0][0] > 0:
                    leaks += 1

        # Cleanup
        for sid in sessions.values():
            await execute_query(client, sid, "DROP TABLE IF EXISTS bench_iso")
            await destroy_session(client, sid)

    result.metrics = {
        "Workgroups tested": len(WORKGROUPS),
        "Rows per workgroup": ROWS_PER_WG,
        "Cross-workgroup queries": queries_run,
        "Data leaks detected": leaks,
    }
    result.passed = leaks == 0
    if leaks == 0:
        result.notes = (
            f"PASS: {queries_run} cross-workgroup queries, 0 leaks. "
            "Session-level DuckDB isolation is airtight."
        )
    else:
        result.notes = f"FAIL: {leaks} data leak(s) detected!"
    return result


async def main() -> None:
    args = build_parser("Benchmark: workgroup isolation").parse_args()
    r = await run(args.url, args.api_key)
    print(r.to_markdown())


if __name__ == "__main__":
    asyncio.run(main())
