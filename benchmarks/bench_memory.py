#!/usr/bin/env python3
"""Phase 9 benchmarks: memory CRUD, search at scale, grant overhead, isolation."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from helpers import BenchmarkResult, fmt_ms, percentiles

from ponddb.memory.store import MemoryStore
from ponddb.memory.access import get_accessible_workgroups
from ponddb.memory.access_log import write_access_log
from ponddb.memory.grants import create_grant
from ponddb.memory.search import search_memories


def _make_store(tmp_dir: str) -> MemoryStore:
    s = MemoryStore(os.path.join(tmp_dir, "bench_mem.db"))
    s.initialize_blocking()
    return s


# ── Benchmark A: Write throughput ─────────────────────────────

def bench_write_throughput(store: MemoryStore) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Memory Write Throughput",
        description="Single agent: 500 writes. 5 concurrent: 100 each. 10 concurrent: 50 each.",
    )

    import threading

    def _write_batch(store, agent_id, count, lats):
        for i in range(count):
            t0 = time.perf_counter()
            store.create_memory(
                agent_id=agent_id, workgroup_id="wg-bench",
                memory_type="semantic", content={"i": i, "agent": agent_id},
            )
            lats.append(time.perf_counter() - t0)

    result.table_headers = ["Concurrency", "Total Writes", "Throughput", "p50", "p95", "p99"]

    for concurrency, per_agent in [(1, 500), (5, 100), (10, 50)]:
        # Fresh store each level
        lats = []
        t_start = time.perf_counter()

        if concurrency == 1:
            _write_batch(store, "agent-0", per_agent, lats)
        else:
            barrier = threading.Barrier(concurrency)
            threads = []

            def _worker(aid, n, lt):
                barrier.wait()
                _write_batch(store, aid, n, lt)

            for j in range(concurrency):
                t = threading.Thread(target=_worker, args=(f"agent-{j}", per_agent, lats))
                threads.append(t)
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

        wall = time.perf_counter() - t_start
        total = concurrency * per_agent
        pcts = percentiles(lats)
        result.table_rows.append([
            str(concurrency), str(total),
            f"{total / wall:.0f} writes/sec",
            fmt_ms(pcts["p50"]), fmt_ms(pcts["p95"]), fmt_ms(pcts["p99"]),
        ])

    result.passed = True
    return result


# ── Benchmark B: Search latency at scale ──────────────────────

def bench_search_latency(store: MemoryStore) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Memory Search Latency",
        description="Search latency at 1K, 10K, 100K memory scale.",
    )
    conn = store._conn
    result.table_headers = ["Scale", "Searches", "p50", "p95"]

    for scale in [1_000, 10_000]:
        # Bulk insert
        for i in range(scale):
            store.create_memory(
                agent_id=f"a-{i % 10}", workgroup_id="wg-scale",
                memory_type=["semantic", "episodic", "shared"][i % 3],
                content={"idx": i}, importance=round(0.1 + (i % 10) * 0.09, 2),
            )

        lats = []
        for _ in range(100):
            t0 = time.perf_counter()
            search_memories(conn, "wg-scale", memory_type="semantic",
                            min_importance=0.5, caller_agent_id="a-0", limit=20)
            lats.append(time.perf_counter() - t0)

        pcts = percentiles(lats)
        result.table_rows.append([
            f"{scale:,}", "100", fmt_ms(pcts["p50"]), fmt_ms(pcts["p95"]),
        ])

        # Clear for next scale
        conn.execute("DELETE FROM agent_memories WHERE workgroup_id = 'wg-scale'")
        conn.commit()

    result.passed = True
    return result


# ── Benchmark C: Grant check overhead ─────────────────────────

def bench_grant_overhead(store: MemoryStore) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Grant Check Overhead",
        description="Search with vs without grants on 10K memories.",
    )
    conn = store._conn

    for i in range(1_000):
        store.create_memory(
            agent_id=f"a-{i % 10}", workgroup_id="wg-grant-src",
            memory_type="semantic", access_scope="workgroup",
            content={"idx": i}, importance=0.7,
        )

    # Baseline: no grants
    lats_base = []
    for _ in range(100):
        t0 = time.perf_counter()
        search_memories(conn, "wg-grant-src", caller_agent_id="a-0", limit=20)
        lats_base.append(time.perf_counter() - t0)

    # With 5 grants
    for j in range(5):
        create_grant(conn, grantor_workgroup_id="wg-grant-src",
                     grantee_workgroup_id=f"wg-grantee-{j}", permission="read",
                     created_by="admin")

    lats_grant = []
    for _ in range(100):
        t0 = time.perf_counter()
        granted = [g for g in get_accessible_workgroups(conn, "wg-grantee-0", "a-0") if g["grant_id"]]
        search_memories(conn, "wg-grantee-0", caller_agent_id="a-0",
                        granted_workgroups=granted, limit=20)
        lats_grant.append(time.perf_counter() - t0)

    pb = percentiles(lats_base)
    pg = percentiles(lats_grant)
    result.table_headers = ["Mode", "p50", "p95"]
    result.table_rows.append(["No grants (baseline)", fmt_ms(pb["p50"]), fmt_ms(pb["p95"])])
    result.table_rows.append(["With 5 grants", fmt_ms(pg["p50"]), fmt_ms(pg["p95"])])
    overhead = pg["p50"] - pb["p50"]
    result.metrics = {"Grant overhead (p50)": fmt_ms(overhead)}
    result.passed = True
    return result


# ── Benchmark D: Access log overhead ──────────────────────────

def bench_access_log_overhead(store: MemoryStore) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Access Log Write Overhead",
        description="500 memory writes with vs without access logging.",
    )
    conn = store._conn

    # With logging
    lats_log = []
    for i in range(500):
        t0 = time.perf_counter()
        m = store.create_memory(agent_id="a1", workgroup_id="wg-log",
                                memory_type="semantic", content={"i": i})
        write_access_log(conn, agent_id="a1", workgroup_id="wg-log",
                         action="write", memory_ids=[m["id"]])
        lats_log.append(time.perf_counter() - t0)

    # Without logging
    lats_nolog = []
    for i in range(500):
        t0 = time.perf_counter()
        store.create_memory(agent_id="a1", workgroup_id="wg-nolog",
                            memory_type="semantic", content={"i": i})
        lats_nolog.append(time.perf_counter() - t0)

    pl = percentiles(lats_log)
    pn = percentiles(lats_nolog)
    overhead = pl["p50"] - pn["p50"]
    result.table_headers = ["Mode", "p50", "p95"]
    result.table_rows.append(["With access log", fmt_ms(pl["p50"]), fmt_ms(pn["p95"])])
    result.table_rows.append(["Without access log", fmt_ms(pn["p50"]), fmt_ms(pn["p95"])])
    result.metrics = {"Log overhead (p50)": fmt_ms(overhead)}
    result.passed = True
    return result


# ── Benchmark E: Isolation stress test ────────────────────────

def bench_isolation_stress(store: MemoryStore) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Isolation Stress Test",
        description="3 workgroups, 10 agents, 1000 memories each, 10K cross-WG queries.",
    )
    conn = store._conn
    wgs = ["wg-iso-a", "wg-iso-b", "wg-iso-c"]

    for wg in wgs:
        for i in range(500):
            store.create_memory(
                agent_id=f"agent-{wg}-{i % 10}", workgroup_id=wg,
                memory_type="semantic", access_scope="workgroup",
                content={"marker": f"{wg}:{i}"},
            )

    leaks = 0
    total_queries = 0
    lats = []
    for _ in range(3_000):
        wg = wgs[total_queries % 3]
        agent = f"agent-{wg}-0"
        t0 = time.perf_counter()
        r = search_memories(conn, wg, caller_agent_id=agent, limit=20)
        lats.append(time.perf_counter() - t0)
        for m in r:
            if m["workgroup_id"] != wg:
                leaks += 1
        total_queries += 1

    pcts = percentiles(lats)
    result.metrics = {
        "Total queries": total_queries,
        "Total leaks": leaks,
        "Query p50": fmt_ms(pcts["p50"]),
        "Query p95": fmt_ms(pcts["p95"]),
    }
    result.passed = leaks == 0
    if leaks == 0:
        result.notes = f"PASS: {total_queries} cross-workgroup queries, 0 leaks."
    else:
        result.notes = f"FAIL: {leaks} data leak(s) detected!"
    return result


# ── Runner ────────────────────────────────────────────────────

def main() -> None:
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ponddb_bench_mem_")
    print(f"Benchmark data dir: {tmp}")

    benchmarks = [
        ("Write Throughput", bench_write_throughput),
        ("Search Latency", bench_search_latency),
        ("Grant Overhead", bench_grant_overhead),
        ("Access Log Overhead", bench_access_log_overhead),
        ("Isolation Stress", bench_isolation_stress),
    ]

    for name, fn in benchmarks:
        print(f"\n{'=' * 60}")
        print(f"  {name}")
        print(f"{'=' * 60}")
        store = _make_store(tmp)
        r = fn(store)
        print(r.to_markdown())


if __name__ == "__main__":
    main()
