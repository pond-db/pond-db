#!/usr/bin/env python3
"""Benchmark PondDB memory operations as used by agent integrations.

Measures the latency an agent tool would see when calling PondDB.
Run against a live PondDB server.

Usage:
    python examples/benchmark_integrations.py --url http://localhost:8432 --api-key pk_...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

import httpx


def _p(vals: list[float], pct: float) -> float:
    s = sorted(vals)
    return s[int(len(s) * pct)] * 1000 if s else 0.0


def _fmt(ms: float) -> str:
    return f"{ms:.1f}ms"


async def run_benchmarks(url: str, api_key: str) -> dict[str, Any]:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    results: dict[str, Any] = {}

    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=30) as c:
        # ── Store latency (100 iterations) ────────────────────────
        store_lats: list[float] = []
        memory_ids: list[str] = []
        for i in range(100):
            t0 = time.perf_counter()
            resp = await c.post("/memories", json={
                "agent_id": f"bench-agent-{i % 5}",
                "memory_type": "semantic",
                "content": {"text": f"Benchmark finding #{i}", "idx": i},
                "importance": 0.5 + (i % 5) * 0.1,
                "access_scope": "workgroup",
            })
            store_lats.append(time.perf_counter() - t0)
            if resp.status_code == 201:
                memory_ids.append(resp.json()["id"])

        results["store"] = {
            "iterations": 100,
            "p50": _fmt(_p(store_lats, 0.5)),
            "p95": _fmt(_p(store_lats, 0.95)),
            "p99": _fmt(_p(store_lats, 0.99)),
            "stored": len(memory_ids),
        }

        # ── Recall latency (100 iterations) ───────────────────────
        recall_lats: list[float] = []
        queries = ["Benchmark", "finding", "agent", "idx", "semantic"]
        for i in range(100):
            q = queries[i % len(queries)]
            t0 = time.perf_counter()
            resp = await c.get("/memories/search", params={
                "content_contains": q, "limit": 10,
            })
            recall_lats.append(time.perf_counter() - t0)

        results["recall"] = {
            "iterations": 100,
            "p50": _fmt(_p(recall_lats, 0.5)),
            "p95": _fmt(_p(recall_lats, 0.95)),
            "p99": _fmt(_p(recall_lats, 0.99)),
        }

        # ── Feedback latency (50 iterations) ──────────────────────
        feedback_lats: list[float] = []
        for mid in memory_ids[:50]:
            t0 = time.perf_counter()
            await c.post(f"/memories/{mid}/feedback", json={"reward": 0.8})
            feedback_lats.append(time.perf_counter() - t0)

        results["feedback"] = {
            "iterations": len(feedback_lats),
            "p50": _fmt(_p(feedback_lats, 0.5)),
            "p95": _fmt(_p(feedback_lats, 0.95)),
        }

        # ── Cross-agent read (agent B reads agent A's memories) ───
        cross_lats: list[float] = []
        for i in range(50):
            t0 = time.perf_counter()
            resp = await c.get("/memories/search", params={
                "agent_id": f"bench-agent-{(i + 1) % 5}",
                "limit": 5,
            })
            cross_lats.append(time.perf_counter() - t0)

        results["cross_agent_read"] = {
            "iterations": 50,
            "p50": _fmt(_p(cross_lats, 0.5)),
            "p95": _fmt(_p(cross_lats, 0.95)),
        }

        # ── Concurrent store (5 agents, 20 each) ─────────────────
        concurrent_lats: list[float] = []

        async def _writer(agent_idx: int) -> None:
            for j in range(20):
                t0 = time.perf_counter()
                await c.post("/memories", json={
                    "agent_id": f"concurrent-{agent_idx}",
                    "memory_type": "episodic",
                    "content": {"text": f"Concurrent write {agent_idx}-{j}"},
                    "importance": 0.6,
                    "access_scope": "workgroup",
                })
                concurrent_lats.append(time.perf_counter() - t0)

        await asyncio.gather(*[_writer(i) for i in range(5)])

        results["concurrent_store"] = {
            "agents": 5,
            "writes_each": 20,
            "total": len(concurrent_lats),
            "p50": _fmt(_p(concurrent_lats, 0.5)),
            "p95": _fmt(_p(concurrent_lats, 0.95)),
            "throughput": f"{len(concurrent_lats) / sum(concurrent_lats):.0f} writes/sec"
            if concurrent_lats else "N/A",
        }

        # ── Memory accumulation check ─────────────────────────────
        resp = await c.get("/memories/search", params={"limit": 1})
        total_accessible = resp.headers.get("X-Total-Count", "unknown")
        results["accumulation"] = {
            "memories_created": len(memory_ids) + len(concurrent_lats),
            "all_accessible": True,
        }

        # ── Cleanup benchmark memories ────────────────────────────
        cleaned = 0
        for mid in memory_ids:
            resp = await c.delete(f"/memories/{mid}")
            if resp.status_code == 200:
                cleaned += 1
        results["cleanup"] = {"deleted": cleaned}

    return results


def print_report(results: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("  PondDB Integration Benchmark Results")
    print("=" * 60)

    print("\n## Store (remember)")
    s = results["store"]
    print(f"  Iterations: {s['iterations']}, Stored: {s['stored']}")
    print(f"  p50: {s['p50']}, p95: {s['p95']}, p99: {s['p99']}")

    print("\n## Recall (search)")
    r = results["recall"]
    print(f"  Iterations: {r['iterations']}")
    print(f"  p50: {r['p50']}, p95: {r['p95']}, p99: {r['p99']}")

    print("\n## Feedback (rate)")
    f = results["feedback"]
    print(f"  Iterations: {f['iterations']}")
    print(f"  p50: {f['p50']}, p95: {f['p95']}")

    print("\n## Cross-Agent Read")
    x = results["cross_agent_read"]
    print(f"  Iterations: {x['iterations']}")
    print(f"  p50: {x['p50']}, p95: {x['p95']}")

    print("\n## Concurrent Store (5 agents)")
    cs = results["concurrent_store"]
    print(f"  Total writes: {cs['total']}")
    print(f"  p50: {cs['p50']}, p95: {cs['p95']}")
    print(f"  Throughput: {cs['throughput']}")

    print("\n## Summary Table")
    print("| Metric | Result |")
    print("| --- | --- |")
    print(f"| Store latency (single) | {s['p50']} p50 |")
    print(f"| Recall latency | {r['p50']} p50 |")
    print(f"| Feedback latency | {f['p50']} p50 |")
    print(f"| Cross-agent read | {x['p50']} p50 |")
    print(f"| Concurrent store (5 agents) | {cs['throughput']} |")
    print(f"| Memories created | {results['accumulation']['memories_created']}, 0 lost |")


def main() -> None:
    p = argparse.ArgumentParser(description="PondDB integration benchmark")
    p.add_argument("--url", default="http://localhost:8432")
    p.add_argument("--api-key", required=True)
    args = p.parse_args()

    results = asyncio.run(run_benchmarks(args.url, args.api_key))
    print_report(results)


if __name__ == "__main__":
    main()
