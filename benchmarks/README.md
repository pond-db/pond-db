# PondDB Benchmarks

PondDB benchmarks measure capabilities that no other agent memory system tests:
multi-agent concurrent writes, analytical queries across agent behavior,
and session lifecycle performance.

## Why these benchmarks?

Existing memory benchmarks (LoCoMo, LongMemEval) test single-user retrieval
accuracy. They don't measure what multi-agent production systems actually need:

- Can 5 agents write simultaneously without data loss?
- Can you query "what did agents know before failures" in milliseconds?
- Can sessions auto-suspend and resume under 500ms?

| Benchmark | What it proves |
| --- | --- |
| `bench_concurrent_write` | 5 agents, 500 writes, zero data loss |
| `bench_concurrent_read` | Reads stay fast under continuous write load |
| `bench_isolation` | Zero cross-workgroup data leaks under load |
| `bench_analytical_queries` | JOINs, window functions, cross-agent analysis — impossible on Mem0/Zep |
| `bench_session_lifecycle` | 100 create→suspend→resume→destroy cycles |
| `bench_pondapi_latency` | Async API latency at 1–50 concurrency |

## Run

```bash
# Requires PondDB running locally
cd benchmarks
pip install httpx
python run_all.py --url http://localhost:8432 --api-key pk_...
```

Run a single benchmark:

```bash
python bench_concurrent_write.py --url http://localhost:8432 --api-key pk_...
```

Results are written to `RESULTS.md`.

## Latest Results

See [RESULTS.md](./RESULTS.md).
