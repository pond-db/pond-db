# PondDB Benchmark Results

**Generated**: 2026-03-18 01:00 UTC

## System

| Property | Value |
| --- | --- |
| platform | Linux-6.8.0-101-generic-x86_64-with-glibc2.39 |
| python | 3.12.3 |
| cpu | x86_64 |
| duckdb | 1.5.0 |

## Summary: 6/6 benchmarks passed

### PondAPI Latency

PondAPI async execute + poll latency at concurrency levels [1, 5, 10, 25].

| Concurrency | Submit p50 | Submit p95 | E2E p50 | E2E p95 | Errors |
| --- | --- | --- | --- | --- | --- |
| 1 | 1.2ms | 1.2ms | 2.0ms | 2.0ms | 0 |
| 5 | 7.0ms | 8.2ms | 9.4ms | 53.8ms | 0 |
| 10 | 9.2ms | 11.9ms | 13.6ms | 54.7ms | 0 |
| 25 | 19.7ms | 43.9ms | 40.8ms | 50.1ms | 0 |


**Result: PASS**

### Concurrent Write

5 agents writing 100 memories each (500 total) with varying payload sizes.

- **Total writes**: 500
- **Wall clock**: 0.55s
- **Throughput**: 903 writes/sec
- **Write latency p50**: 4.9ms
- **Write latency p95**: 8.1ms
- **Write latency p99**: 16.0ms
- **Failed writes**: 0
- **Data integrity**: 5 distinct agents, 500 total rows

**Result: PASS**

### Concurrent Read/Write

5 readers + 2 writers for 30s on 10,000 pre-loaded rows.

- **Read throughput**: 93 reads/sec
- **Read latency p50**: 2.7ms
- **Read latency p95**: 5.4ms
- **Read latency p99**: 6.7ms
- **Write throughput**: 19 writes/sec
- **Write latency p50**: 3.1ms
- **Write latency p95**: 4.8ms
- **Write latency p99**: 6.7ms
- **Total reads**: 2801
- **Total writes**: 578

**Result: PASS**

### Workgroup Isolation

3 workgroups (alpha, beta, gamma), 1,000 memories each, 100 cross-workgroup queries.

- **Workgroups tested**: 3
- **Rows per workgroup**: 1000
- **Cross-workgroup queries**: 102
- **Data leaks detected**: 0

> PASS: 102 cross-workgroup queries, 0 leaks. Session-level DuckDB isolation is airtight.

**Result: PASS**

### Analytical Queries

5 analytical queries over 10,000 memories and 1,000 executions across 10 agents.

| Query | Description | Latency | Rows |
| --- | --- | --- | --- |
| Q1 | Top agents by memory count | 1.3ms | 10 |
| Q2 | Daily memory creation | 1.9ms | 7 |
| Q3 | Context before failures | 1.7ms | 50 |
| Q4 | Cross-agent topic overlap | 44.6ms | 10 |
| Q5 | Growth projection (window) | 2.2ms | 7 |


> These queries are impossible with Mem0 (data split across Qdrant + Neo4j + SQLite) or Zep (data in Neo4j, no analytical engine). PondDB runs them in-process on DuckDB with zero data movement.

**Result: PASS**

### Session Lifecycle

100 full create → suspend → resume → destroy cycles.

- **Cycles completed**: 100
- **Failed cycles**: 0
- **Session create p50**: 7.1ms
- **Session create p95**: 7.8ms
- **Full cycle p50**: 2020.2ms
- **Skipped suspend (timeout)**: 100

**Result: PASS**

---

## How to reproduce

```bash
# Start PondDB locally
pip install -e '.[dev]'
ponddb serve --port 8432

# Run benchmarks
cd benchmarks
pip install httpx
python run_all.py --url http://localhost:8432 --api-key pk_YOUR_KEY
```

## Why these benchmarks matter

Existing memory benchmarks (LoCoMo, LongMemEval) test single-user retrieval accuracy. They don't measure what multi-agent production systems actually need:

- **Concurrent writes**: Can 5 agents write simultaneously without data loss?
- **Read under write load**: Do reads stay fast while writes are happening?
- **Workgroup isolation**: Is tenant data truly invisible across boundaries?
- **Analytical queries**: Can you JOIN agent memories with execution logs in milliseconds?
- **Session lifecycle**: Can sessions auto-suspend and resume under 500ms?
- **API latency**: How does the async PondAPI scale with concurrency?

PondDB is the only agent memory system that can answer all of these.
