#!/usr/bin/env python3
"""Run all PondDB benchmarks and generate RESULTS.md."""

from __future__ import annotations

import asyncio
import gc
import sys
import os
from datetime import datetime, timezone

# Ensure benchmarks/ is on sys.path so imports resolve when run from repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import BenchmarkResult, build_parser, system_info

import bench_concurrent_write
import bench_concurrent_read
import bench_isolation
import bench_analytical_queries
import bench_session_lifecycle
import bench_pondapi_latency

BENCHMARKS = [
    ("bench_pondapi_latency", bench_pondapi_latency),  # Run first (lightweight)
    ("bench_concurrent_write", bench_concurrent_write),
    ("bench_concurrent_read", bench_concurrent_read),
    ("bench_isolation", bench_isolation),
    ("bench_analytical_queries", bench_analytical_queries),
    ("bench_session_lifecycle", bench_session_lifecycle),  # Run last (creates many sessions)
]


def _generate_report(
    results: list[BenchmarkResult],
    sysinfo: dict[str, str],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# PondDB Benchmark Results",
        "",
        f"**Generated**: {now}",
        "",
        "## System",
        "",
        f"| Property | Value |",
        f"| --- | --- |",
    ]
    for k, v in sysinfo.items():
        lines.append(f"| {k} | {v} |")
    lines.append("")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines.append(f"## Summary: {passed}/{total} benchmarks passed")
    lines.append("")

    for r in results:
        lines.append(r.to_markdown())

    lines.extend([
        "---",
        "",
        "## How to reproduce",
        "",
        "```bash",
        "# Start PondDB locally",
        "pip install -e '.[dev]'",
        "ponddb serve --port 8432",
        "",
        "# Run benchmarks",
        "cd benchmarks",
        "pip install httpx",
        "python run_all.py --url http://localhost:8432 --api-key pk_YOUR_KEY",
        "```",
        "",
        "## Why these benchmarks matter",
        "",
        "Existing memory benchmarks (LoCoMo, LongMemEval) test single-user "
        "retrieval accuracy. They don't measure what multi-agent production "
        "systems actually need:",
        "",
        "- **Concurrent writes**: Can 5 agents write simultaneously without "
        "data loss?",
        "- **Read under write load**: Do reads stay fast while writes are "
        "happening?",
        "- **Workgroup isolation**: Is tenant data truly invisible across "
        "boundaries?",
        "- **Analytical queries**: Can you JOIN agent memories with execution "
        "logs in milliseconds?",
        "- **Session lifecycle**: Can sessions auto-suspend and resume under "
        "500ms?",
        "- **API latency**: How does the async PondAPI scale with concurrency?",
        "",
        "PondDB is the only agent memory system that can answer all of these.",
        "",
    ])
    return "\n".join(lines)


async def main() -> None:
    parser = build_parser("Run all PondDB benchmarks")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "RESULTS.md"),
        help="Output path for results markdown",
    )
    args = parser.parse_args()

    sysinfo = system_info()
    results: list[BenchmarkResult] = []

    for name, module in BENCHMARKS:
        print(f"\n{'='*60}")
        print(f"  Running: {name}")
        print(f"{'='*60}")
        try:
            r = await module.run(args.url, args.api_key)
            results.append(r)
            status = "PASS" if r.passed else "FAIL"
            print(f"  → {status}")
        except Exception as exc:
            print(f"  → ERROR: {exc}")
            results.append(
                BenchmarkResult(
                    name=name,
                    description=f"Benchmark failed with error: {exc}",
                    passed=False,
                )
            )
        # Give the server breathing room between benchmarks
        gc.collect()
        await asyncio.sleep(1)

    report = _generate_report(results, sysinfo)
    with open(args.output, "w") as f:
        f.write(report)
    print(f"\nResults written to {args.output}")

    # Also print to stdout
    print("\n" + report)


if __name__ == "__main__":
    asyncio.run(main())
